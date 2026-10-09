"""One immutable selection, ChatGPT reader edition, two Claude comparison editions.

Installed and activated by the production operator after Dev qualification.
No article-writing API fallback; external hero costs are recorded separately.
"""
import argparse
from contextlib import contextmanager
from datetime import date as Date, datetime, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import operational_settings

from subscription_writer import load_json, save_json
from smn_daily import Day, Hold, CLIS, now
from model_job_evidence import DAILY_JOB_LIMIT

ORIGIN = 'https://seasonalmarketnews.com'
AUTH_FAILURE = ('login', 'auth', 'quota', 'rate limit', 'rate_limit', 'usage limit')


def edition_date():
    # Production's 02:00 selector names its CSV for the UTC publication date.
    return datetime.now(timezone.utc).date().isoformat()


def scheduled_window(root, date, current=None):
    """Hold scheduled editions that start early or reuse overnight sources."""
    snapshot = root/date/'schedule-settings.json'
    if snapshot.exists():
        bound = json.loads(snapshot.read_text())
        if bound.get('date') != date:
            raise ValueError('Scheduled settings snapshot date mismatch')
        setting = bound['daily_generation']
    else:
        setting = operational_settings.for_date(date)['daily_generation']
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with snapshot.open('x') as output:
            json.dump({'date': date, 'daily_generation': setting,
                       'created_utc': datetime.now(timezone.utc).isoformat()}, output)
    local = (current or datetime.now(timezone.utc)).astimezone(ZoneInfo(setting['timezone']))
    if local.date().isoformat() != date or local.weekday() >= 5:
        raise ValueError('Scheduled edition must run on its local weekday')
    start = datetime.combine(local.date(), datetime.strptime(setting['start_time'], '%H:%M').time(),
                             ZoneInfo(setting['timezone']))
    if local < start:
        raise ValueError('Scheduled edition cannot start before the morning generation window')
    cutoff = start.timestamp()
    source_paths = [root/date/'inputs'/'input-selection.json',
                    root/date/'inputs'/'production-engine-export.json']
    for profile in ('chatgpt', 'claude'):
        edition = root/date/profile
        source_paths.append(edition/'sources.json')
        for folder in ('primary', 'research', 'jobs'):
            if (edition/folder).exists():
                source_paths.extend(path for path in (edition/folder).rglob('*') if path.is_file())
    if any(path.is_file() and path.stat().st_mtime < cutoff for path in source_paths):
        raise ValueError('Pre-window inputs or research cannot be reused for a scheduled edition')
    for profile in ('chatgpt', 'claude'):
        primary = root/date/profile/'primary'
        if not primary.exists():
            continue
        for receipt_path in primary.glob('*.receipt.json'):
            receipt = json.loads(receipt_path.read_text())
            fetched = datetime.fromisoformat(receipt['fetched_utc'])
            text_path = primary/(receipt_path.name[:-len('.receipt.json')] + '.txt')
            if (receipt.get('edition_date') != date or fetched.tzinfo is None or
                    fetched.timestamp() < cutoff or not text_path.is_file() or
                    hashlib.sha256(text_path.read_bytes()).hexdigest() != receipt.get('text_sha256')):
                raise ValueError('Primary source receipt predates the morning window or changed')
        for cache_path in primary.glob('*.fetch-cache.json'):
            cache = json.loads(cache_path.read_text())
            for page in cache.get('pages', {}).values():
                fetched = datetime.fromisoformat(page['fetched_utc'])
                if fetched.tzinfo is None or fetched.timestamp() < cutoff:
                    raise ValueError('Primary source page predates the morning window')
    return start


def outcome(result):
    if result.get('status') == 'waiting_for_selection':
        return 'waiting_for_selection', 75
    providers = result.get('providers')
    reader = providers.get('chatgpt') if isinstance(providers,dict) else None
    if (isinstance(reader,dict) and reader.get('passed') is True and
            (not result.get('publication_requested') or
             (reader.get('publication') or {}).get('status') == 'live_verified')):
        return 'completed', 0
    return 'held', 2


@contextmanager
def lock(root):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root/'controller.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def freeze_inputs(source, destination):
    """Copy only frozen inputs; never share mutable provider jobs or receipts."""
    names = ('production', 'production-engine-export.json', 'engine-capture.json',
             'input-selection.json', 'input-heroes.json')
    files = [p for name in names for p in
             ((source/name).rglob('*') if (source/name).is_dir() else [source/name])
             if p.is_file()]
    required = ('production/posts.json', 'production-engine-export.json', 'input-selection.json', 'input-heroes.json')
    if any(not (source/n).is_file() for n in required):
        raise ValueError('Canonical inputs are incomplete')
    hashes = {}
    for item in files:
        if item.is_symlink():
            raise ValueError('Input symlink refused')
        relative = item.relative_to(source)
        raw = item.read_bytes()
        hashes[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        target = destination/relative
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Frozen provider input changed: '+str(relative))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    manifest = destination/'shared-inputs.json'
    if manifest.exists() and load_json(manifest) != hashes:
        raise ValueError('Shared input manifest changed')
    save_json(manifest, hashes)
    return hashes


def authenticate(profile, root):
    if profile == 'chatgpt':
        from subscription_writer import account_snapshot
        return account_snapshot(CLIS['codex'], root)
    from claude_subscription_writer import account_snapshot
    return account_snapshot(CLIS['claude'], root)


def release_login_holds(day):
    """Only after a successful auth preflight; preserve every failed attempt."""
    for symbol, state in day.state['articles'].items():
        reason = state.get('held', {}).get('reason', '').lower()
        if not any(word in reason for word in AUTH_FAILURE):
            continue
        for job in (day.root/'jobs').glob(symbol+'-'+day.date.replace('-', '')+'-*'):
            if not (job/'receipt.json').exists() and (job/'state.json').exists():
                status = load_json(job/'state.json')
                if status.get('status') == 'failed_needs_review':
                    try:day._archive(job, reason, 'authentication-retry')
                    except Hold:break
        else:
            del state['held']
            continue
    day.save()


def run_profile(root, date, profile, canonical, publication_origin=ORIGIN, continuity=False):
    root.mkdir(parents=True, exist_ok=True)
    authenticate(profile, root)
    freeze_inputs(canonical, root)
    day = Day(root, date, profile=profile, max_jobs=DAILY_JOB_LIMIT,
              publication_origin=publication_origin if profile == 'chatgpt' else None)
    day.symbols = [p['symbol'] for p in load_json(root/'production/posts.json')]
    if continuity:
        if profile != 'chatgpt' or publication_origin != ORIGIN:
            raise ValueError('Continuity progression requires the production ChatGPT origin')
        day.continuity = True
    release_login_holds(day)
    day.release_transient_holds()
    if continuity:
        symbols = day.symbols
        try:
            for symbol in symbols:
                day.symbols = [symbol]
                try:
                    from smn_recovery import checkpoint
                    checkpoint(day,symbol,'research','running')
                    day.research()
                    checkpoint(day,symbol,'research','completed')
                    checkpoint(day,symbol,'article','running')
                    day.articles()
                    checkpoint(day,symbol,'article','completed' if not day.state['articles'].get(symbol,{}).get('held') else 'held')
                    checkpoint(day,symbol,'visual','running')
                    day.visual()
                    checkpoint(day,symbol,'visual','completed' if day.state['articles'].get(symbol,{}).get('finalized') else 'pending')
                except Exception as exc:
                    # An article hold does not prevent later subjects progressing.
                    article=day.state['articles'].setdefault(symbol,{})
                    if not article.get('held'):article['held']={'utc':now(),'reason':str(exc)[:500]}
                    day.save();checkpoint(day,symbol,'pipeline','held',article['held']['reason'])
                    continue
        finally:
            day.symbols = symbols
    else:
        day.research()
        day.articles()
        day.visual()
    return day.check()


def run(root, date, publish=False, target='production', scheduled=False, continuity=False):
    Date.fromisoformat(date)
    root = Path(root).resolve()
    if target not in {'production','dev'}:
        raise ValueError('Unsupported publication target')
    if continuity and (target != 'production' or publish):
        raise ValueError('Continuity progression is Dev-only and uses a separate delivery tick')
    if continuity:
        from production_continuity import require_policy
        require_policy()
    with lock(root):
        if scheduled:
            scheduled_window(root, date)
        path = root/'comparison-state.json'
        state = load_json(path) if path.exists() else {'reader_provider': 'chatgpt', 'comparison_dates': []}
        if state.get('reader_provider') != 'chatgpt':
            raise ValueError('Reader provider must remain ChatGPT')
        if state.get('target',target) != target:
            raise ValueError('Controller publication target changed')
        state['target'] = target
        # Reserve two actual selected publication dates. A failed day resumes in
        # its own directory and never silently spends a third comparison edition.
        compare = False if continuity else date in state['comparison_dates'] or len(state['comparison_dates']) < 2
        canonical = root/date/'inputs'
        canonical.mkdir(parents=True, exist_ok=True)
        from subscription_inputs import capture, prepare_heroes
        from subscription_capture import engine
        result = capture(canonical, date)
        if result['status'].startswith('waiting'):
            return result
        if compare and date not in state['comparison_dates']:
            state['comparison_dates'].append(date)
            save_json(path, state)
        profiles = ['chatgpt', 'claude'] if compare else ['chatgpt']
        auth_errors = {}
        for profile in profiles:
            try:
                authenticate(profile, canonical)
            except Exception as exc:
                auth_errors[profile] = str(exc)[:500]
        if len(auth_errors) == len(profiles):
            raise ValueError('Subscription authentication required: '+json.dumps(auth_errors))
        engine(canonical, date, 'engine')
        if target == 'dev' and not (canonical/'input-heroes.json').exists():
            raise ValueError('Dev qualification requires explicitly staged hero assets; no production hero API call')
        if continuity:
            prepare_heroes(canonical,date,nonessential_fallback=True)
        else:
            prepare_heroes(canonical,date)
        outcomes = {}
        for profile in profiles:
            edition = root/date/profile
            try:
                if profile in auth_errors:
                    raise ValueError(auth_errors[profile])
                outcomes[profile] = (run_profile(edition, date, profile, canonical, continuity=continuity) if target == 'production' else
                    run_profile(edition, date, profile, canonical, publication_origin=None,
                                continuity=continuity and profile == 'chatgpt'))
                if profile == 'chatgpt' and publish and outcomes[profile].get('passed') is True:
                    if scheduled:
                        scheduled_window(root, date)
                    if target == 'production':
                        from smn_subscription_publish import publish_edition
                        outcomes[profile]['publication'] = publish_edition(edition, date)
                    else:
                        day = Day(edition,date,profile='chatgpt')
                        outcomes[profile]['publication'] = day.publish(Path(__file__).resolve().parent.parent)
            except Exception as exc:
                outcomes[profile] = {**outcomes.get(profile,{}), 'passed': False, 'status': 'held', 'reason': str(exc)[:500]}
                save_json(edition/'HOLD.json', {'utc': now(), **outcomes[profile],
                          'resume': 'Fix authentication/quota or the named cause; rerun this date. Saved work is retained.'})
        record = {'utc': now(), 'date': date, 'reader_provider': 'chatgpt',
                  'claude_comparison': compare, 'api_writer_fallback': False,
                  'publication_requested': publish,
                  'comparison_status': ('passed' if outcomes.get('claude',{}).get('passed') is True else
                                        'held' if compare else 'not_requested'),
                  'external_costs': ('Continuity uses retained heroes or deterministic neutral placeholders; no paid hero retry' if continuity else
                                     'Shared hero generation/checking uses configured paid APIs; see inputs/input-heroes.json'),
                  'providers': outcomes}
        save_json(root/date/'comparison.json', record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--date', default=edition_date())
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--target', choices=['production','dev'], default='production')
    parser.add_argument('--scheduled', action='store_true', help='Enforce morning source and publication window')
    args = parser.parse_args()
    last_run = args.root/'last-run.json'
    try:
        save_json(last_run, {'utc': now(), 'date': args.date, 'status': 'running',
                            'target': args.target, 'publish': args.publish})
        result = run(args.root, args.date, args.publish, args.target, args.scheduled)
        status, code = outcome(result)
        save_json(last_run, {'utc': now(), 'date': args.date, 'status': status,
                            'exit_code': code, 'target': args.target, 'publish': args.publish,
                            'comparison_status': result.get('comparison_status','unknown'),
                            'reader_publication_status': ((result.get('providers') or {}).get('chatgpt') or {}).get('publication',{}).get('status','not_verified'),
                            'result': result})
        print(json.dumps(result))
        return code
    except Exception as exc:
        save_json(args.root/args.date/'HOLD.json', {'utc': now(), 'reason': str(exc)[:500], 'api_writer_fallback': False})
        save_json(last_run, {'utc': now(), 'date': args.date, 'status': 'held', 'exit_code': 2,
                            'target': args.target, 'publish': args.publish, 'reason': str(exc)[:500]})
        print(json.dumps({'status': 'held', 'reason': str(exc)[:500]}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
