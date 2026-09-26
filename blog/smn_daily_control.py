"""Select the subscription provider for future SMN editions, or run today's edition."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import smn_models
from subscription_writer import load_json, save_json, utc_now

DEFAULT_ROOT = Path('/var/lib/tradewave/smn-daily')


def selection(root):
    path = root / 'settings.json'
    value = load_json(path) if path.exists() else {'profile': 'claude'}
    smn_models.load(profile=value['profile'])
    return value


def set_profile(root, profile):
    smn_models.load(profile=profile)
    value = {'profile': profile, 'updated_utc': utc_now(), 'applies_to': 'new editions'}
    save_json(root / 'settings.json', value)
    return value


def edition_profile(root, date):
    state = root / date / 'smn-daily-state.json'
    if state.exists():
        bound = load_json(state)
        if bound.get('date') != date:
            raise ValueError('Edition date mismatch')
        # Older Claude runs predate the named profile field.
        profile = bound.get('profile')
        if profile in smn_models.PROFILES:
            return profile
        matches = [p for p in smn_models.PROFILES if smn_models.load(profile=p) == bound.get('roles')]
        if len(matches) == 1:
            return matches[0]
        raise ValueError('Edition uses custom or changed settings; resume explicitly with its saved roles')
    return selection(root)['profile']


def run(root, date, max_jobs=40, publish=True):
    datetime.strptime(date, '%Y-%m-%d')
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.script-run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Another run owns .script-run.lock; inspect ownership before recovery')
    try:
        save_json(lock / 'owner.json', {'pid': os.getpid(), 'date': date, 'utc': utc_now()})
        edition = root / date
        receipt = edition / 'dev-publication-receipt.json'
        if receipt.exists() and load_json(receipt).get('status') == 'live_verified':
            print(json.dumps({'status': 'already_live_verified', 'date': date}), flush=True)
            return 0
        profile = edition_profile(root, date)
        cmd = [sys.executable, str(Path(__file__).with_name('smn_daily.py')),
               '--root', str(edition), '--date', date, '--profile', profile,
               '--max-jobs', str(max_jobs)]
        if publish:
            cmd.append('--publish')
        print(json.dumps({'status': 'starting', 'date': date, 'profile': profile,
                          'max_jobs': max_jobs, 'publish': publish}), flush=True)
        save_json(root/'last-run.json', {'status': 'running', 'date': date, 'profile': profile, 'utc': utc_now()})
        result = subprocess.run(cmd, check=False)
        if receipt.exists() and load_json(receipt).get('status') == 'live_verified':
            status = 'live_verified'
        elif result.returncode:
            status = 'hold'
        elif not (edition/'smn-daily-state.json').exists():
            status = 'waiting_for_production'
        else:
            status = 'ready_to_publish'
        record = {'status': status, 'date': date, 'profile': profile, 'utc': utc_now(),
                  'exit_code': result.returncode}
        if status == 'hold' and (edition/'HOLD.json').exists():
            record['reason'] = load_json(edition/'HOLD.json').get('reason')
        if status == 'live_verified':
            record['url'] = 'https://smn-dev.trxstat.com/editions/' + date + '/'
        save_json(root/'last-run.json', record)
        return result.returncode
    finally:
        (lock / 'owner.json').unlink(missing_ok=True)
        lock.rmdir()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-root', type=Path, default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest='command', required=True)
    profile = sub.add_parser('profile')
    profile.add_argument('provider', choices=tuple(smn_models.PROFILES), nargs='?')
    daily = sub.add_parser('run')
    daily.add_argument('--date', default=datetime.now(ZoneInfo('America/New_York')).date().isoformat())
    daily.add_argument('--max-jobs', type=int, default=40)
    daily.add_argument('--no-publish', action='store_true')
    args = ap.parse_args()
    try:
        if args.command == 'profile':
            result = set_profile(args.state_root, args.provider) if args.provider else selection(args.state_root)
            print(json.dumps(result))
            return 0
        return run(args.state_root, args.date, args.max_jobs, not args.no_publish)
    except Exception as exc:
        print(json.dumps({'status': 'hold', 'reason': str(exc)}), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
