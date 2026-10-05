"""Independent, read-only edition observer with durable Resend alert receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DEFAULT_FROM = 'TradeWave <help@tradewave.ai>'
PUBLIC_CHECK_TTL_SECONDS = 300
ORIGINS = {'production':'https://seasonalmarketnews.com', 'dev':'https://smn-dev.trxstat.com'}
LEDGER_DIR = Path('/var/lib/smn-dashboard/schedule-runs')
TRANSIENT = re.compile(r'OAuth token|rate.?limit|overloaded|\b(?:429|500|502|503|529)\b|'
                       r'temporarily|ECONNRESET|ETIMEDOUT|timed? ?out', re.I)


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _settings(now):
    from operational_settings import for_instant
    return for_instant(now)


def _key(date, kind, detail):
    return date + ':' + kind + ':' + hashlib.sha256(detail.encode()).hexdigest()[:16]


def _incident(date, kind, detail, subject):
    return {'key': _key(date, kind, detail), 'date': date, 'kind': kind,
            'subject': subject, 'detail': detail[:500]}


def _expected(root, date):
    base = root/date/'inputs'
    selection = _json(base/'input-selection.json')
    posts = _json(base/'production/posts.json')
    if not isinstance(selection, dict) or not isinstance(selection.get('symbols'), list):
        return None
    symbols = selection['symbols']
    if not symbols or not isinstance(posts, list):
        return None
    selected = {p.get('symbol') for p in posts if isinstance(p, dict)}
    if set(symbols) != selected or len(symbols) != len(selected):
        return None
    return symbols


def _public_probe(origin, expected):
    """Read public catalog and check the actual expected article URLs once."""
    with urlopen(Request(origin+'/posts.json', headers={'User-Agent':'SMNOperationalAlert/1.0'}), timeout=8) as response:
        posts = json.load(response)
    statuses = {}
    for url in expected.values():
        try:
            with urlopen(Request(url, method='HEAD', headers={'User-Agent':'SMNOperationalAlert/1.0'}), timeout=8) as response:
                statuses[url] = 'available' if response.status == 200 else 'unknown'
        except HTTPError as exc:
            statuses[url] = 'missing' if exc.code == 404 else 'unknown'
        except (OSError, URLError):
            statuses[url] = 'unknown'
    return posts, statuses


def _last_progress(root, date, current):
    """Use saved reader work, not controller age alone, for optional stall detection."""
    reader = root/date/'chatgpt'
    paths = [reader/'smn-daily-state.json', reader/'daily-check.json', reader/'HOLD.json']
    for pattern in ('jobs/*/state.json', 'jobs/*/output.json', 'jobs/*/events.jsonl',
                    'jobs/*/*.png', 'jobs/*/artifacts/*.png',
                    'results/*/generation.json', 'results/*/article.html',
                    'results/*/*.png', 'results/*/assets/*.png',
                    'results/*/visual/*.png', 'results/*/screenshots/*.png'):
        paths.extend(reader.glob(pattern))
    observed = [p.stat().st_mtime for p in paths if p.is_file()]
    try:
        observed.append(datetime.fromisoformat(current['utc'].replace('Z', '+00:00')).timestamp())
    except (KeyError, TypeError, ValueError):
        pass
    return max(observed) if observed else None


def inspect(root, now, settings, public_probe=_public_probe, target='production', ledger_dir=LEDGER_DIR):
    """Return confirmed incidents; never start generation or mutate source receipts."""
    root = Path(root)
    zone = ZoneInfo(settings['daily_generation']['timezone'])
    local = now.astimezone(zone)
    if local.weekday() >= 5:
        return []
    date = local.date().isoformat()
    last = _json(root/'last-run.json')
    current = last if isinstance(last, dict) and last.get('date') == date and last.get('target', target) == target else None
    terminal = bool(current and current.get('status') not in {'running', 'waiting_for_selection'})
    ledgers = []
    for path in sorted(Path(ledger_dir).glob(f'{target}-*-{date}*.json')):
        record = _json(path)
        if isinstance(record, dict) and record.get('target') == target and record.get('date') == date:
            ledgers.append(record)
    failed = [r for r in ledgers if r.get('status') == 'failed']
    incidents = []
    state = _json(root/date/'chatgpt/smn-daily-state.json') or {}
    for symbol, row in (state.get('articles') or {}).items():
        if not isinstance(row, dict) or not isinstance(row.get('held'), dict):
            continue
        reason = str(row['held'].get('reason') or 'unknown cause')
        # Existing model retries can clear passing faults before run completion.
        if TRANSIENT.search(reason):
            continue
        detail = f'{symbol}: {reason}'
        incidents.append(_incident(date, 'article-held', detail,
                                   f'SMN article held: {symbol} ({date})'))
    reader_check = _json(root/date/'chatgpt/daily-check.json') or {}
    reader_hold = _json(root/date/'chatgpt/HOLD.json') or {}
    origin = ORIGINS.get(target)
    expected = _expected(root, date)
    receipt_name = 'dev-publication-receipt.json' if target == 'dev' else 'production-publication-receipt.json'
    receipt = _json(root/date/'chatgpt'/receipt_name)
    receipt_ok = bool(origin and isinstance(receipt, dict) and receipt.get('status') == 'live_verified'
                      and (target == 'dev' or receipt.get('production_written') is True))
    reader_done = False
    public_problem = None
    if receipt_ok and expected:
        urls = {symbol:f'{origin}/editions/{date}/{symbol}/article.html' for symbol in expected}
        try:
            public, statuses = public_probe(origin, urls)
            live = {(p.get('symbol'), p.get('url')) for p in public if isinstance(p, dict)
                    and str(p.get('published_date', ''))[:10] == date
                    and p.get('edition_id') == 'subscription-' + date}
            missing = sorted(symbol for symbol, url in urls.items()
                             if (symbol, url) not in live or statuses.get(url) == 'missing')
            unknown = sorted(symbol for symbol, url in urls.items() if statuses.get(url) != 'available')
            if missing:
                public_problem = 'Expected public articles missing: ' + ', '.join(missing)
            elif unknown:
                public_problem = 'Public article URL availability could not be verified: ' + ', '.join(unknown)
            else:
                reader_done = True
        except (OSError, ValueError, TypeError, HTTPError, URLError):
            public_problem = 'Public article catalog could not be verified'
    if reader_done:
        return []
    if failed:
        detail = 'Scheduled child failed: ' + ', '.join(f"{r.get('phase')} exit {r.get('exit_code')}" for r in failed)
        return [_incident(date, 'scheduler-failed', detail, f'SMN scheduled run failed ({date})')]
    if incidents:
        # Each confirmed article is actionable; avoid repeating it as a run/deadline alert.
        return incidents
    if reader_hold:
        reason = str(reader_hold.get('reason') or 'reader publication held')
        if not TRANSIENT.search(reason) or terminal:
            return [_incident(date, 'reader-held', reason,
                              f'SMN reader publication held ({date})')]
    if reader_check.get('passed') is False:
        held = reader_check.get('held') or []
        detail = 'Reader article checks failed' + (': ' + ', '.join(map(str, held)) if held else '')
        return [_incident(date, 'reader-incomplete', detail,
                          f'SMN reader edition incomplete ({date})')]
    if current and current.get('status') == 'completed' and (not expected or not receipt_ok or public_problem):
        detail = public_problem or ('No valid frozen expected lineup' if not expected else
                                    f'No live-verified publication receipt for {len(expected)} expected articles')
        kind = 'verification-unavailable' if 'could not be verified' in detail else 'edition-incomplete'
        return [_incident(date, kind, detail, f'SMN edition incomplete ({date})')]
    if current and current.get('status') == 'held':
        detail = str(current.get('reason') or current.get('status'))
        return [_incident(date, 'run-unsuccessful', detail,
                          f'SMN morning run unsuccessful ({date})')]
    schedule = settings['daily_generation']
    grace = schedule.get('no_start_grace_minutes')
    active_child = any(r.get('status') == 'running' for r in ledgers)
    waiting = current and current.get('status') == 'waiting_for_selection'
    if (current is None or waiting) and not active_child and isinstance(grace, int) and grace > 0:
        start = datetime.combine(local.date(), time.fromisoformat(schedule['start_time']), zone)
        if local >= start + timedelta(minutes=grace):
            return [_incident(date, 'no-start', 'No fresh reader generation after configured start grace',
                              f'SMN morning run did not start ({date})')]
    stall = schedule.get('stall_minutes')
    if current and current.get('status') == 'running' and isinstance(stall, int) and stall > 0:
        progress = _last_progress(root, date, current)
        if progress is not None and now.timestamp() - progress >= stall * 60:
            return [_incident(date, 'no-progress',
                              f'No recorded reader generation progress for at least {stall} minutes',
                              f'SMN reader generation has no recorded progress ({date})')]
    return incidents


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(temp, path)


def send_resend(recipient, incident, sender=DEFAULT_FROM):
    """Send one stable incident email; Resend and local receipts suppress retries."""
    key = os.environ.get('RESEND_API_KEY', '')
    if not key or key.upper() == 'PLACEHOLDER':
        return False
    body = f"{incident['subject']}\n\n{incident['detail']}\n\nEdition: {incident['date']}\n"
    payload = {'from': sender, 'to': [recipient], 'subject': incident['subject'], 'text': body}
    token = hashlib.sha256((incident['key'] + ':' + recipient).encode()).hexdigest()
    request = Request('https://api.resend.com/emails', method='POST',
                      data=json.dumps(payload).encode(), headers={
                          'Authorization':'Bearer '+key, 'Content-Type':'application/json',
                          'Idempotency-Key':'smn-alert-'+token})
    try:
        with urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (OSError, HTTPError, URLError):
        return False


def run(root, now=None, settings=None, public_probe=_public_probe, sender=send_resend,
        baseline=False, target='production', ledger_dir=LEDGER_DIR):
    root = Path(root)
    status_path = Path(os.environ.get('SMN_DASHBOARD_STATE', '/var/lib/smn-dashboard'))/'operational-alerts-status.json'
    now = now or datetime.now(timezone.utc)
    settings = settings or _settings(now)
    path = root/'operational-alerts-state.json'
    state = _json(path)
    if not isinstance(state, dict):
        state = {'activated_utc': now.isoformat(), 'acknowledged': [], 'delivered': {}, 'public_checks': {}}
    last = _json(root/'last-run.json') or {}
    local_date = now.astimezone(ZoneInfo(settings['daily_generation']['timezone'])).date().isoformat()
    receipt_name = 'dev-publication-receipt.json' if target == 'dev' else 'production-publication-receipt.json'
    receipt_path = root/local_date/'chatgpt'/receipt_name
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest() if receipt_path.exists() else ''
    version = _key(str(last.get('date','')), target, str(last.get('utc','')) + ':' +
                   str(last.get('status','')) + ':' + receipt_hash)
    def cached_probe(origin, expected):
        cached = state.get('public_checks', {}).get(version)
        if cached:
            try:
                age = now.timestamp() - datetime.fromisoformat(cached['checked_utc']).timestamp()
            except (KeyError, TypeError, ValueError):
                age = PUBLIC_CHECK_TTL_SECONDS
            if 0 <= age < PUBLIC_CHECK_TTL_SECONDS:
                return cached['posts'], cached['statuses']
        posts, statuses = public_probe(origin, expected)
        if all(value in {'available','missing'} for value in statuses.values()):
            selected = [p for p in posts if isinstance(p, dict) and p.get('url') in set(expected.values())]
            state['public_checks'] = {version: {'posts':selected, 'statuses':statuses,
                                              'checked_utc':now.isoformat()}}
            _save(path, state)
        return posts, statuses
    incidents = inspect(root, now, settings, cached_probe, target, ledger_dir)
    acknowledged = set(state.get('acknowledged') or [])
    if baseline:
        acknowledged.update(item['key'] for item in incidents)
    state['acknowledged'] = sorted(acknowledged)
    _save(path, state)
    recipients = settings.get('alert_recipients') or []
    credential = bool(os.environ.get('RESEND_API_KEY') and os.environ.get('RESEND_API_KEY','').upper() != 'PLACEHOLDER')
    enabled = bool(settings.get('alerts_enabled', False))
    status = {'last_checked_utc':now.astimezone(timezone.utc).isoformat(),
              'preferences_enabled':enabled, 'credential_available':credential,
              'send_ready':bool(enabled and credential and recipients),
              'sender':os.environ.get('SMN_ALERT_FROM') or DEFAULT_FROM,
              'last_delivery_error':None, 'sent':0,
              'observed_incidents':len(incidents),
              'unacknowledged_incidents':sum(item['key'] not in acknowledged for item in incidents),
              'delivery_state':('disabled' if not enabled else 'missing_credential' if not credential
                                else 'missing_recipients' if not recipients else 'ready'),
              'watchdog':'no-start configured' if settings['daily_generation'].get('no_start_grace_minutes')
              else 'unconfigured',
              'stall_watchdog':'recorded-progress configured' if
              settings['daily_generation'].get('stall_minutes') else 'unconfigured'}
    if baseline or not enabled or not status['send_ready']:
        if enabled and not credential:
            status['last_delivery_error'] = 'Resend key unavailable'
        elif enabled and not recipients:
            status['last_delivery_error'] = 'Alert recipients unavailable'
        _save(status_path, status)
        return {'observed': len(incidents), 'sent': 0, 'baseline': baseline}
    sent = 0
    for item in incidents:
        if item['key'] in acknowledged:
            continue
        for recipient in recipients:
            receipt = item['key'] + ':' + recipient
            if state['delivered'].get(receipt):
                continue
            if sender(recipient, item, os.environ.get('SMN_ALERT_FROM') or DEFAULT_FROM):
                state['delivered'][receipt] = now.isoformat()
                _save(path, state)
                sent += 1
            else:
                status['last_delivery_error'] = 'Resend delivery not confirmed'
    status['sent'] = sent
    _save(status_path, status)
    return {'observed': len(incidents), 'sent': sent, 'baseline': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--target', choices=tuple(ORIGINS), required=True)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    import fcntl
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root/'operational-alerts.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(run(args.root, baseline=args.baseline, target=args.target)))


if __name__ == '__main__':
    main()
