"""Minute-tick entry point for a separately activated SMN scheduler.

On production this replaces only the existing selector, article queue, and
Sunday recap cron lines. On Dev it replaces the subscription timer. Installing
it is an explicit environment release; merely saving settings does not do so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import operational_settings

MARKERS = Path(os.environ.get('SMN_OPERATIONAL_SCHEDULE_STATE', '/var/lib/smn-dashboard/schedule-runs'))
CONTROLLER_ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')
WEB_ROOT = Path('/var/www/smn')


def verified_reader_urls(root, day, web_root=WEB_ROOT):
    """Return only a complete, still-live production reader edition."""
    edition = root/day
    selected_path = edition/'inputs/input-selection.json'
    receipt_path = edition/'chatgpt/production-publication-receipt.json'
    if not selected_path.is_file() or not receipt_path.is_file():
        return None
    selected = json.loads(selected_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('publication_policy') == 'continuity-v1' and receipt.get('complete') is not True:
        return None
    symbols = selected.get('symbols')
    if (selected.get('date') != day or not isinstance(symbols, list) or
            not 1 <= len(symbols) <= 6 or len(set(symbols)) != len(symbols) or
            any(not re.fullmatch(r'[A-Z0-9]{1,12}', s) for s in symbols)):
        raise ValueError('Reader selection is incomplete')
    if (receipt.get('status') != 'live_verified' or receipt.get('production_written') is not True or
            receipt.get('edition_date') != day or not receipt.get('source_commit')):
        raise ValueError('Reader publication is not fully verified')
    urls = receipt.get('urls')
    if not isinstance(urls, list) or len(urls) != len(symbols) or len(set(urls)) != len(urls):
        raise ValueError('Reader publication lineup differs from selection')
    expected = {'https://seasonalmarketnews.com/editions/'+day+'/'+s+'/article.html' for s in symbols}
    if set(urls) != expected:
        raise ValueError('Reader publication URLs differ from selection')
    # The scheduled controller freezes settings and validates source receipts;
    # recheck the primary fetch times before releasing the subscriber email.
    schedule = edition/'schedule-settings.json'
    if not schedule.is_file():
        raise ValueError('Scheduled reader source proof is missing')
    bound = json.loads(schedule.read_text())
    if bound.get('date') != day:
        raise ValueError('Scheduled reader date differs')
    morning = bound['daily_generation']
    start = datetime.combine(datetime.fromisoformat(day).date(),
                             datetime.strptime(morning['start_time'], '%H:%M').time(),
                             ZoneInfo(morning['timezone'])).timestamp()
    if selected_path.stat().st_mtime < start:
        raise ValueError('Reader selection predates the morning window')
    for symbol in symbols:
        source = edition/'chatgpt/primary'/(symbol+'.receipt.json')
        if not source.is_file():
            raise ValueError('A reader primary source receipt is missing')
        fetched = datetime.fromisoformat(json.loads(source.read_text())['fetched_utc'])
        if fetched.tzinfo is None or fetched.timestamp() < start:
            raise ValueError('Reader primary research predates the morning window')
    posts = json.loads((web_root/'posts.json').read_text())
    current = {p.get('url'): p for p in posts if p.get('url') in expected}
    if len(current) != len(expected):
        raise ValueError('Published reader lineup is missing from the live catalog')
    for url in expected:
        post = current[url]
        rel = urlparse(url).path.lstrip('/')
        file = web_root/rel
        digest = receipt.get('files', {}).get(rel)
        if (post.get('source_commit') != receipt['source_commit'] or
                post.get('edition_id') != 'subscription-'+day or
                str(post.get('published_date', ''))[:10] != day or
                str(post.get('publish_status')).lower() != 'true' or
                not digest or not file.is_file() or file.is_symlink() or
                hashlib.sha256(file.read_bytes()).hexdigest().lower() != str(digest).lower()):
            raise ValueError('A verified reader article changed or was unpublished')
    return expected


def _local_when(now, setting):
    return now.astimezone(ZoneInfo(setting['timezone']))


def _at(local, setting):
    return local.strftime('%H:%M') == setting['time']


def due(now, target, settings, newsletter_only=False):
    """Return (phase, UTC edition date); duplicate DST minutes share a marker."""
    if target not in {'dev', 'production'}:
        raise ValueError('target must be dev or production')
    if newsletter_only:
        if target != 'production':
            raise ValueError('Subscriber email is production-only')
        local = _local_when(now, settings['weekday_newsletter'])
        if local.weekday() < 5 and local.strftime('%H:%M') >= settings['weekday_newsletter']['time']:
            return [('weekday_newsletter', local.date().isoformat())]
        return []
    daily = settings['daily_generation']
    local = _local_when(now, daily)
    day = local.date().isoformat()
    tasks = []
    if local.weekday() < 5:
        clock = local.strftime('%H:%M')
        if daily['start_time'] <= clock:
            tasks.append(('daily', day))
        if target == 'production':
            # The selector has always preceded the article queue by one hour.
            following = (now + timedelta(hours=1)).astimezone(ZoneInfo(daily['timezone']))
            if following.weekday() < 5 and following.strftime('%H:%M') == daily['start_time']:
                tasks.append(('selector', following.date().isoformat()))
    if target == 'production':
        weekly = settings['sunday_summary']
        sunday = _local_when(now, weekly)
        if weekly['enabled'] and sunday.weekday() == 6 and _at(sunday, weekly):
            tasks.append(('sunday_summary', sunday.date().isoformat()))
    return tasks


def command(target, phase, day):
    python = sys.executable
    blog = Path(__file__).resolve().parent
    if phase == 'daily':
        return [python, str(blog/'smn_subscription_daily.py'), '--root', str(CONTROLLER_ROOT),
                '--date', day, '--target', target, '--publish', '--scheduled']
    if phase == 'weekday_newsletter':
        return [python, str(blog/'send_smn_emails.py'), '--verified-reader-date', day]
    script = {'selector': 'select_news_articles.py',
              'sunday_summary': 'send_smn_emails.py'}[phase]
    return [python, str(blog/script)]


def tick(target, now=None, newsletter_only=False):
    import fcntl
    now = now or datetime.now(timezone.utc)
    settings = operational_settings.for_instant(now)
    MARKERS.mkdir(parents=True, exist_ok=True)
    with (MARKERS/('newsletter.lock' if newsletter_only else 'scheduler.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        for phase, day in due(now, target, settings, newsletter_only):
            marker = MARKERS / f'{target}-{phase}-{day}.json'
            if phase == 'daily':
                activation = Path('/etc/SMN/subscription-primary.json')
                if target == 'production' and activation.is_file() and json.loads(activation.read_text()).get('publication_policy') == 'continuity-v1':
                    from production_continuity import require_policy
                    require_policy()
                    continue  # Independent progress service owns the opt-in generation.
                last = CONTROLLER_ROOT/'last-run.json'
                if last.exists():
                    status = json.loads(last.read_text())
                    if status.get('date') == day and status.get('status') != 'waiting_for_selection':
                        continue
                marker = MARKERS / f'{target}-daily-{day}-{now.strftime("%H%M")}.json'
            if marker.exists():
                continue
            if phase == 'weekday_newsletter' and verified_reader_urls(CONTROLLER_ROOT, day) is None:
                continue
            # A started marker prevents duplicate newsletter/selector execution
            # after a crash. Operators inspect a failed run before any retry.
            record = {'target': target, 'phase': phase, 'date': day,
                      'started_at': now.isoformat(), 'status': 'running'}
            with marker.open('x') as output:
                json.dump(record, output)
            env = os.environ.copy()
            if target == 'production':
                setting = (settings['sunday_summary'] if phase == 'sunday_summary' else
                           settings['weekday_newsletter'] if phase == 'weekday_newsletter' else
                           settings['daily_generation'])
                # MailerLite's existing +5-minute campaign schedule uses host UTC.
                # Eligibility above is evaluated in the configured newsletter zone.
                env['TZ'] = 'UTC' if phase == 'weekday_newsletter' else setting['timezone']
            try:
                result = subprocess.run(command(target, phase, day), cwd=Path(__file__).resolve().parent,
                                        env=env)
                code = result.returncode
            except OSError:
                code = 2
            record.update(finished_at=datetime.now(timezone.utc).isoformat(),
                          status='completed' if code == 0 else 'waiting' if code == 75 else 'failed',
                          exit_code=code)
            temporary = marker.with_suffix('.tmp')
            temporary.write_text(json.dumps(record), encoding='utf-8')
            os.replace(temporary, marker)
            if code:
                return code
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['dev', 'production'], required=True)
    parser.add_argument('--newsletter-only', action='store_true')
    args = parser.parse_args()
    raise SystemExit(tick(args.target, newsletter_only=args.newsletter_only))
