"""One immutable selection, ChatGPT reader edition, two Claude comparison editions.

Installed and activated by the production operator after Dev qualification.
No article-writing API fallback; external hero costs are recorded separately.
"""
import argparse
from contextlib import contextmanager
from datetime import date as Date
import hashlib
import json
from pathlib import Path

from subscription_writer import load_json, save_json
from smn_daily import Day, CLIS, now

ORIGIN = 'https://seasonalmarketnews.com'
AUTH_FAILURE = ('login', 'auth', 'quota', 'rate limit', 'rate_limit', 'usage limit')


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
                    day._archive(job, reason, 'authentication-retry')
        del state['held']
    day.save()


def run_profile(root, date, profile, canonical, publication_origin=ORIGIN):
    root.mkdir(parents=True, exist_ok=True)
    authenticate(profile, root)
    freeze_inputs(canonical, root)
    day = Day(root, date, profile=profile,
              publication_origin=publication_origin if profile == 'chatgpt' else None)
    day.symbols = [p['symbol'] for p in load_json(root/'production/posts.json')]
    release_login_holds(day)
    day.release_transient_holds()
    day.research()
    day.articles()
    day.visual()
    return day.check()


def run(root, date, publish=False, target='production'):
    Date.fromisoformat(date)
    root = Path(root).resolve()
    if target not in {'production','dev'}:
        raise ValueError('Unsupported publication target')
    with lock(root):
        path = root/'comparison-state.json'
        state = load_json(path) if path.exists() else {'reader_provider': 'chatgpt', 'comparison_dates': []}
        if state.get('reader_provider') != 'chatgpt':
            raise ValueError('Reader provider must remain ChatGPT')
        if state.get('target',target) != target:
            raise ValueError('Controller publication target changed')
        state['target'] = target
        # Reserve two actual selected publication dates. A failed day resumes in
        # its own directory and never silently spends a third comparison edition.
        compare = date in state['comparison_dates'] or len(state['comparison_dates']) < 2
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
        prepare_heroes(canonical, date)
        outcomes = {}
        for profile in profiles:
            edition = root/date/profile
            try:
                if profile in auth_errors:
                    raise ValueError(auth_errors[profile])
                outcomes[profile] = (run_profile(edition, date, profile, canonical) if target == 'production' else
                    run_profile(edition, date, profile, canonical, publication_origin=None))
                if profile == 'chatgpt' and publish:
                    if target == 'production':
                        from smn_subscription_publish import publish_edition
                        outcomes[profile]['publication'] = publish_edition(edition, date)
                    else:
                        day = Day(edition,date,profile='chatgpt')
                        outcomes[profile]['publication'] = day.publish(Path(__file__).resolve().parent.parent)
            except Exception as exc:
                outcomes[profile] = {'passed': False, 'status': 'held', 'reason': str(exc)[:500]}
                save_json(edition/'HOLD.json', {'utc': now(), **outcomes[profile],
                          'resume': 'Fix authentication/quota or the named cause; rerun this date. Saved work is retained.'})
        record = {'utc': now(), 'date': date, 'reader_provider': 'chatgpt',
                  'claude_comparison': compare, 'api_writer_fallback': False,
                  'external_costs': 'Shared hero generation/checking uses configured paid APIs; see inputs/input-heroes.json',
                  'providers': outcomes}
        save_json(root/date/'comparison.json', record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--date', default=Date.today().isoformat())
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--target', choices=['production','dev'], default='production')
    args = parser.parse_args()
    try:
        result = run(args.root, args.date, args.publish, args.target)
        print(json.dumps(result))
        return 0 if all(p.get('passed') for p in result.get('providers', {}).values()) else 2
    except Exception as exc:
        save_json(args.root/args.date/'HOLD.json', {'utc': now(), 'reason': str(exc)[:500], 'api_writer_fallback': False})
        print(json.dumps({'status': 'held', 'reason': str(exc)[:500]}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
