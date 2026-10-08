"""Opt-in production publication continuity ticks. Invoke progress and deliver separately.

No timer is installed by this module. The delivery tick never takes the research
controller lock or starts a model job; its publisher validates saved evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import operational_settings
from smn_recovery import verify_generation
from subscription_writer import load_json, save_json

DEFAULT_ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')
ACTIVATION = Path('/etc/SMN/subscription-primary.json')
MAX_JOBS = 40
MAX_UNCHANGED_ATTEMPTS = 6
ZONE = ZoneInfo('America/New_York')


def require_production_host():
    from production_continuity import require_policy
    require_policy()


def preserved_release_day(day):
    """Leave pre-release recovered editions untouched by newly activated workers."""
    if not ACTIVATION.is_file():
        return False
    activation = load_json(ACTIVATION)
    if activation.get('publication_policy') != 'continuity-v1':
        return False
    first = activation.get('continuity_first_edition_date')
    if first is None:
        return False  # Existing releases retain their prior schedule semantics.
    if not isinstance(first, str) or datetime.strptime(first, '%Y-%m-%d').strftime('%Y-%m-%d') != first:
        raise ValueError('Invalid release first edition date')
    return day < first


@contextmanager
def _lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _setting(root, day):
    snapshot = root/day/'schedule-settings.json'
    if snapshot.is_file():
        bound = load_json(snapshot)
        if bound.get('date') != day:
            raise ValueError('Scheduled settings snapshot date mismatch')
        return bound['daily_generation']
    return operational_settings.for_date(day)['daily_generation']


def _eligible(root, day, current, field):
    setting = _setting(root, day)
    local = current.astimezone(ZoneInfo(setting['timezone']))
    if local.date().isoformat() != day or local.weekday() >= 5:
        return False
    return local.strftime('%H:%M') >= setting[field]


def _fingerprint(root, day):
    """Track substantive progress, excluding changing timestamps and hold prose."""
    edition = root/day/'chatgpt'
    state_path = edition/'smn-daily-state.json'
    state = load_json(state_path) if state_path.is_file() else {}
    articles = state.get('articles', {})
    jobs = edition/'jobs'
    job_rows = sorted((p.name, (p/'receipt.json').is_file(),
                       len(list(p.glob('failed-attempt-*')))) for p in jobs.iterdir()
                      if p.is_dir()) if jobs.is_dir() else []
    selected = root/day/'inputs/input-selection.json'
    selection_hash = hashlib.sha256(selected.read_bytes()).hexdigest() if selected.is_file() else None
    rows = {sym: {key: article.get(key) for key in ('draft', 'mechanical_ok',
            'review_stage', 'editorially_finalized', 'finalized')}
            for sym, article in articles.items()}
    from model_job_evidence import jobs_used
    # A repaired review, released hold or added subject must wake terminal recovery.
    # Ignore timestamps and changing event logs; hash the actual durable evidence.
    evidence = {}
    for pattern in ('sources.json', 'input-selection.json', 'primary/*.receipt.json',
                    'research/*.json', 'jobs/*/job.json', 'jobs/*/receipt.json',
                    'jobs/*/output.json', 'results/*/article.json',
                    'jobs/*/state.json','jobs/*/execution.json',
                    'results/*/article.html', 'results/*/review-binding*.json',
                    'results/*/bundle.json', 'results/*/commission.json',
                    'results/*/generation.json', 'results/*/hero-asset.json',
                    'results/*/mechanical-checks.json', 'results/*/visual-checks.json'):
        for path in sorted(edition.glob(pattern)):
            if path.is_file():
                evidence[path.relative_to(edition).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for sym, article in articles.items():
        rows[sym]['held'] = bool(article.get('held'))
    used = jobs_used(edition)
    return hashlib.sha256(json.dumps([selection_hash, rows, job_rows, evidence, used],
                                     sort_keys=True).encode()).hexdigest(), used


def progress(root, day, current=None):
    """One bounded retry of saved work; a held day remains pending."""
    from smn_subscription_daily import run
    root = Path(root).resolve()
    current = current or datetime.now(timezone.utc)
    if preserved_release_day(day):
        return {'status': 'preserved_release_day', 'date': day}
    if not _eligible(root, day, current, 'start_time'):
        return {'status': 'before_generation_window'}
    path = root/day/'continuity-progress.json'
    with _lock(root/day/'continuity-progress.lock') as acquired:
        if not acquired:
            return {'status': 'in_progress'}
        prior = load_json(path) if path.is_file() else {}
        edition = root/day/'chatgpt'
        for name in ('production-publication-receipt.json', 'dev-publication-receipt.json'):
            receipt_path = edition/name
            if receipt_path.is_file():
                receipt = load_json(receipt_path)
                if receipt.get('status') == 'live_verified' and receipt.get('publication_policy') != 'continuity-v1':
                    from production_continuity import reconcile_legacy
                    reconcile_legacy(edition,day)
                    record = {'date':day,'status':'generation_complete','reused_legacy':True}
                    save_json(path, record)
                    return record
        before, used = _fingerprint(root, day)
        if prior.get('status') in {'generation_complete', 'needs_attention'}:
            if prior.get('fingerprint') == before:
                return prior
            # Retain the terminal diagnostic while allowing repaired saved work to
            # resume. A changed artifact is not an accuracy approval or a cap raise.
            prior = {**prior, 'unchanged_attempts': 0, 'next_attempt_utc': None,
                     'recovered_from': {key: prior.get(key) for key in
                         ('status', 'reason', 'fingerprint', 'updated_utc', 'attempts')}}
        if used >= MAX_JOBS:
            record = {'date': day, 'status': 'needs_attention', 'jobs_used': used,
                      'max_jobs': MAX_JOBS, 'fingerprint': before,
                      'reason': 'Cumulative model-job budget exhausted; saved approvals remain available for delivery',
                      'updated_utc': current.isoformat()}
            if prior.get('recovered_from'):
                record['recovered_from'] = prior['recovered_from']
            save_json(path, record)
            return record
        retry_at = prior.get('next_attempt_utc')
        if retry_at and current < datetime.fromisoformat(retry_at):
            return prior
        # Write before running so an interrupted process waits before restarting.
        running = {'date': day, 'status': 'running',
                         'updated_utc': current.isoformat(),
                         'next_attempt_utc': (current + timedelta(minutes=15)).isoformat(),
                         'attempts': prior.get('attempts', 0) + 1,
                         'unchanged_attempts': prior.get('unchanged_attempts', 0),
                         'fingerprint': before}
        if prior.get('recovered_from'):
            running['recovered_from'] = prior['recovered_from']
        save_json(path, running)
        try:
            result = run(root, day, publish=False, target='production', scheduled=True, continuity=True)
            reader = (result.get('providers') or {}).get('chatgpt') or {}
            verification=verify_generation(root,day)
            complete = verification['complete'] is True
            status = 'generation_complete' if complete else 'pending'
            reason = reader.get('reason') or result.get('status', '')
        except BlockingIOError:
            status, reason = 'busy', 'Daily controller is already running'
        except Exception as exc:
            status, reason = 'pending', str(exc)[:500]
        after, jobs = _fingerprint(root, day)
        unchanged = prior.get('unchanged_attempts', 0) + 1 if before == after else 0
        if status == 'pending' and unchanged >= MAX_UNCHANGED_ATTEMPTS:
            status = 'needs_attention'
            reason = 'No substantive progress after bounded retries; ' + reason
        minutes = min(60, 5 * 2 ** min(unchanged, 4))
        record = {'date': day, 'status': status, 'reason': reason,
                  'attempts': prior.get('attempts', 0) + 1,
                  'unchanged_attempts': unchanged, 'jobs_used': jobs,
                  'max_jobs': MAX_JOBS, 'fingerprint': after,
                  'artifact_verification':verification if 'verification' in locals() else {'complete':False},
                  'updated_utc': datetime.now(timezone.utc).isoformat(),
                  'next_attempt_utc': None if status in {'generation_complete', 'needs_attention'} else
                      (current + timedelta(minutes=minutes)).isoformat()}
        if prior.get('recovered_from'):
            record['recovered_from'] = prior['recovered_from']
        save_json(path, record)
        return record


def deliver(root, day, current=None, repo=None):
    """Deadline/revision delivery; independent of a running research worker."""
    root = Path(root).resolve()
    current = current or datetime.now(timezone.utc)
    if preserved_release_day(day):
        return {'status': 'preserved_release_day', 'date': day}
    if not _eligible(root, day, current, 'target_time'):
        progress_path = root/day/'continuity-progress.json'
        ready = load_json(progress_path) if progress_path.is_file() else {}
        if (ready.get('date') != day or ready.get('status') != 'generation_complete' or
                not _eligible(root, day, current, 'start_time')):
            return {'status': 'before_delivery_window'}
    state_path = root/day/'continuity-delivery-state.json'
    with _lock(root/day/'continuity-delivery.lock') as acquired:
        if not acquired:
            return {'status': 'delivery_in_progress'}
        prior = load_json(state_path) if state_path.is_file() else {}
        fingerprint, _ = _fingerprint(root, day)
        if prior.get('fingerprint') == fingerprint:
            result = prior.get('result') or {}
            retry_at = prior.get('next_attempt_utc')
            # A complete receipt still needs byte/custody verification by the
            # publisher. Evidence fingerprints do not include public web files.
            if retry_at and current < datetime.fromisoformat(retry_at):
                return result or {'status': prior.get('status', 'held'),
                                  'reason': prior.get('reason', 'Delivery is unverified')}
        from production_continuity import publish_available
        try:
            result = publish_available(root/day/'chatgpt', day, 'production',
                                       repo=repo or Path(__file__).resolve().parent.parent,
                                       max_jobs=MAX_JOBS)
        except Exception as exc:
            save_json(state_path, {'date': day, 'fingerprint': fingerprint,
                      'next_attempt_utc': (current+timedelta(minutes=5)).isoformat(),
                      'status': 'held', 'reason': str(exc)[:500]})
            raise
        save_json(state_path, {'date': day, 'fingerprint': fingerprint,
                  'next_attempt_utc': None if result.get('complete') is True and
                      result.get('status') == 'live_verified' else
                      (current+timedelta(minutes=15)).isoformat(),
                  'result': result, 'updated_utc': datetime.now(timezone.utc).isoformat()})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('progress', 'deliver', 'reconcile'))
    parser.add_argument('--enable-production-continuity', action='store_true', required=True)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--date')
    args = parser.parse_args()
    if not args.enable_production_continuity:
        parser.error('Explicit --enable-production-continuity is required')
    current = datetime.now(timezone.utc)
    day = args.date or current.astimezone(ZONE).date().isoformat()
    try:
        require_production_host()
        if preserved_release_day(day):
            print(json.dumps({'status': 'preserved_release_day', 'date': day}))
            return 0
        from smn_recovery import worker_start,worker_finish,watch_workers,supervise
        watch_workers(args.root,day,current)
        worker=worker_start(args.root,day,args.phase,current)
        if args.phase == 'reconcile':
            recovery=supervise(args.root,day,current)
            from send_smn_emails import poll_pending_campaigns
            try:
                mail=poll_pending_campaigns(current)
            except Exception as exc:
                mail={'status':'held','reason':str(exc)[:500],'resend_attempted':False}
            result={**recovery,'mail_reconciliation':mail}
        else:
            result = progress(args.root, day, current) if args.phase == 'progress' else deliver(args.root, day, current)
    except Exception as exc:
        result = {'status': 'held', 'reason': str(exc)[:500]}
    if 'worker' in locals():
        worker_finish(worker,result,datetime.now(timezone.utc))
    print(json.dumps(result, default=str))
    return 2 if result.get('status') in {'held','needs_attention'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
