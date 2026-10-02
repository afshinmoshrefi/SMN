"""Minute-tick entry point for a separately activated SMN scheduler.

On production this replaces only the existing selector, article queue, and
Sunday recap cron lines. On Dev it replaces the subscription timer. Installing
it is an explicit environment release; merely saving settings does not do so.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import operational_settings

MARKERS = Path(os.environ.get('SMN_OPERATIONAL_SCHEDULE_STATE', '/var/lib/smn-dashboard/schedule-runs'))
CONTROLLER_ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')


def _local_when(now, setting):
    return now.astimezone(ZoneInfo(setting['timezone']))


def _at(local, setting):
    return local.strftime('%H:%M') == setting['time']


def due(now, target, settings):
    """Return (phase, UTC edition date); duplicate DST minutes share a marker."""
    if target not in {'dev', 'production'}:
        raise ValueError('target must be dev or production')
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
    script = {'selector': 'select_news_articles.py',
              'sunday_summary': 'send_smn_emails.py'}[phase]
    return [python, str(blog/script)]


def tick(target, now=None):
    import fcntl
    now = now or datetime.now(timezone.utc)
    settings = operational_settings.for_instant(now)
    MARKERS.mkdir(parents=True, exist_ok=True)
    with (MARKERS/'scheduler.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        for phase, day in due(now, target, settings):
            marker = MARKERS / f'{target}-{phase}-{day}.json'
            if phase == 'daily':
                last = CONTROLLER_ROOT/'last-run.json'
                if last.exists():
                    status = json.loads(last.read_text())
                    if status.get('date') == day and status.get('status') != 'waiting_for_selection':
                        continue
                marker = MARKERS / f'{target}-daily-{day}-{now.strftime("%H%M")}.json'
            if marker.exists():
                continue
            # A started marker prevents duplicate newsletter/selector execution
            # after a crash. Operators inspect a failed run before any retry.
            record = {'target': target, 'phase': phase, 'date': day,
                      'started_at': now.isoformat(), 'status': 'running'}
            with marker.open('x') as output:
                json.dump(record, output)
            env = os.environ.copy()
            if target == 'production':
                setting = settings['sunday_summary'] if phase == 'sunday_summary' else settings['daily_generation']
                env['TZ'] = setting['timezone']
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
    args = parser.parse_args()
    raise SystemExit(tick(args.target))
