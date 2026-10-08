"""Count failed reconciliation attempts, independently of healthy minute ticks.

The systemd timer remains the only restart source. A persisted reservation makes
an interrupted worker a failed attempt before another child may run. This guard
never dispatches models or sends mail; the existing reconciliation child retains
its receipt-aware and factual/source safeguards.
"""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

POLICY = 'failure-only-reconcile-v1'
FAILURE_WINDOW_SECONDS = 3600
MAX_FAILURES = 3
CHILD_TIMEOUT_SECONDS = 85
DEFAULT_ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')
ACTIVATION = Path('/etc/SMN/subscription-primary.json')


def _save(path, value):
    fd, name = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2); out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if Path(name).exists(): Path(name).unlink()


def _identity(pid):
    try:
        base = Path('/proc')/str(pid)
        stat = (base/'stat').read_text(); fields = stat[stat.rindex(')')+2:].split()
        return {'start_ticks': fields[19], 'argv_sha256': hashlib.sha256((base/'cmdline').read_bytes()).hexdigest()}
    except (OSError, ValueError, IndexError):
        return None


def _alive(pid):
    if not isinstance(pid, int) or pid <= 1: return None
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except (PermissionError, OSError): return None
    return True


def _stamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError('Failure evidence requires UTC')
    return stamp


def run_tick(root, day, command, current=None, launcher=None):
    current = current or datetime.now(timezone.utc)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day): raise ValueError('Exact edition date required')
    datetime.strptime(day, '%Y-%m-%d')
    if current.tzinfo is None or current.utcoffset() != timedelta(0): raise ValueError('UTC clock required')
    root = Path(root).resolve(); directory = root/day
    if directory.is_symlink(): raise ValueError('Guard day cannot be a symlink')
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory/'reconcile-tick-guard.json'; lock_path = directory/'reconcile-tick-guard.lock'
    if state_path.is_symlink() or lock_path.is_symlink(): raise ValueError('Guard paths cannot be symlinks')
    with lock_path.open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'status': 'reconcile_guard_busy', 'date': day}
        try:
            state = json.loads(state_path.read_text()) if state_path.is_file() else {
                'policy': POLICY, 'date': day, 'failures': [], 'successful_ticks': 0}
            if state.get('policy') != POLICY or state.get('date') != day or not isinstance(state.get('failures'), list):
                raise ValueError('Guard evidence policy/date changed')
            failures = []
            for item in state['failures']:
                stamp = _stamp(item['observed_utc'])
                if stamp > current: raise ValueError('Future failure evidence')
                if stamp > current-timedelta(seconds=FAILURE_WINDOW_SECONDS): failures.append(item)
            if len(failures) > MAX_FAILURES: raise ValueError('Guard failure evidence exceeds bound')
            pending = state.get('inflight')
            if pending:
                if (pending.get('host') != socket.gethostname() or not pending.get('identity') or
                        _stamp(pending['started_utc']) > current):
                    raise ValueError('Unverifiable reconciliation reservation')
                alive = _alive(pending.get('pid'))
                if alive is not False:
                    status = 'reconciliation_in_progress' if alive and _identity(pending['pid']) == pending['identity'] else 'reconcile_owner_uncertain'
                    return {'status': status, 'date': day, 'new_child_started': False}
                failures.append({'observed_utc': current.isoformat(), 'reason': 'previous reserved process exited without completion',
                                 'attempt_id': pending['attempt_id']})
                state.pop('inflight')
                state['next_attempt_utc'] = (current+timedelta(seconds=60)).isoformat()
            state['failures'] = failures
            state['updated_utc'] = current.isoformat()
            if len(failures) >= MAX_FAILURES:
                state.update(status='failure_budget_exhausted', next_attempt_utc=(
                    _stamp(failures[0]['observed_utc'])+timedelta(seconds=FAILURE_WINDOW_SECONDS)).isoformat())
                _save(state_path, state)
                return {'status': 'failure_budget_exhausted', 'date': day, 'failures_in_window': len(failures),
                        'next_attempt_utc': state['next_attempt_utc'], 'new_child_started': False}
            retry_at = state.get('next_attempt_utc')
            if retry_at and current < _stamp(retry_at):
                state['status'] = 'child_backoff'; _save(state_path, state)
                return {'status': 'child_backoff', 'date': day, 'failures_in_window': len(failures),
                        'next_attempt_utc': retry_at, 'new_child_started': False}
            identity = _identity(os.getpid())
            if identity is None: raise ValueError('Own process birth identity unavailable')
            attempt_id = hashlib.sha256((day+current.isoformat()+str(os.getpid())).encode()).hexdigest()
            state.update(status='running', next_attempt_utc=None, inflight={'pid': os.getpid(), 'host': socket.gethostname(),
                'identity': identity, 'started_utc': current.isoformat(), 'attempt_id': attempt_id})
            _save(state_path, state)
            try:
                result = (launcher or subprocess.run)(command, timeout=CHILD_TIMEOUT_SECONDS, check=False)
                failed = result.returncode != 0
                reason = 'child return code '+str(result.returncode)
            except (OSError, subprocess.TimeoutExpired) as exc:
                failed, reason = True, type(exc).__name__
            completed = current if launcher is not None else datetime.now(timezone.utc)
            state.pop('inflight')
            state.update(updated_utc=completed.isoformat(), last_child_result=reason)
            if failed:
                state['failures'].append({'observed_utc': completed.isoformat(), 'reason': reason, 'attempt_id': attempt_id})
                state['status'] = 'failure_budget_exhausted' if len(state['failures']) >= MAX_FAILURES else 'child_failed'
                state['next_attempt_utc'] = ((_stamp(state['failures'][0]['observed_utc'])+timedelta(seconds=FAILURE_WINDOW_SECONDS)).isoformat()
                                             if len(state['failures']) >= MAX_FAILURES else (completed+timedelta(seconds=60)).isoformat())
            else:
                state['status'] = 'observed'; state['successful_ticks'] = state.get('successful_ticks', 0)+1
            _save(state_path, state)
            return {'status': state['status'], 'date': day, 'failures_in_window': len(state['failures']),
                    'new_child_started': True, 'child_succeeded': not failed, 'next_attempt_utc': state.get('next_attempt_utc')}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Preserve malformed/foreign/custody evidence; never call the child.
            return {'status': 'reconcile_guard_needs_attention', 'date': day,
                    'reason': str(exc)[:240], 'new_child_started': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--enable-production-continuity', action='store_true', required=True)
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT); p.add_argument('--date')
    args = p.parse_args()
    day = args.date or datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    # The existing first-edition gate runs before guard directories or reservations.
    from production_continuity_schedule import preserved_release_day, require_production_host
    require_production_host()
    if preserved_release_day(day):
        print(json.dumps({'status': 'preserved_release_day', 'date': day})); return 0
    command = [sys.executable, str(Path(__file__).with_name('production_continuity_schedule.py')), 'reconcile',
               '--enable-production-continuity', '--root', str(args.root), '--date', day]
    print(json.dumps(run_tick(args.root, day, command)))
    # The minute timer observes held/exhausted states too; a success exit is not
    # child completion proof. The persisted failure ledger is authoritative.
    return 0


if __name__ == '__main__': raise SystemExit(main())
