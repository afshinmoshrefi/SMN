"""Independent, read-only edition observer with durable Resend alert receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urljoin, quote
from zoneinfo import ZoneInfo

DEFAULT_FROM = 'TradeWave <help@tradewave.ai>'
PUBLIC_CHECK_TTL_SECONDS = 300
MAX_PUBLIC_BYTES = 2 * 1024 * 1024
# Exact observed edge addition; no arbitrary script stripping is permitted.
CF_BEACON = b'''<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/v31edd6df95cf4e85bb4c19e7a9bdbcba1788362987495" integrity="sha512-iIg7k2xntmwu6/uSb5tpc/hySgZc4eoL31yB29W6tJFo2akwjPWcEqnCEdJvGexCL0KEQwVYv5BlowfhVz26hg==" data-cf-beacon='{"version":"2024.11.0","token":"6c5153e7bcd94cb993504acb03b8923e","r":1,"spa":2}' crossorigin="anonymous"></script>'''
ORIGINS = {'production':'https://seasonalmarketnews.com', 'dev':'https://smn-dev.trxstat.com'}
LEDGER_DIR = Path('/var/lib/smn-dashboard/schedule-runs')
CAMPAIGN_STATE = Path('/home/flask/blog/logs/sent_smn_emails.json')
CONTINUITY_ACTIVATION = Path('/etc/SMN/subscription-primary.json')
MAX_ALERT_SEND_ATTEMPTS = 3
MAX_ALERT_DELIVERY_POLLS = 48
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


class _Preview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.values = {'headline': [], 'preview': [], 'qualification': [], 'full_article_value': []}
        self.capture = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent = self.stack[-1] if self.stack else None
        kind = None
        if parent == 'main' and tag == 'h1': kind = 'headline'
        if parent == 'main' and tag == 'p' and attrs.get('role') != 'status':
            kind = 'qualification' if 'qualification' in attrs.get('class', '').split() else 'preview'
        if self.stack[-2:] == ['main', 'aside'] and tag == 'p' and 'small' not in attrs.get('class', '').split():
            kind = 'full_article_value'
        if kind:
            self.capture = (kind, len(self.stack))
            self.parts = []
        if tag not in {'meta', 'link', 'img', 'br', 'input', 'hr', 'source', 'wbr'}:
            self.stack.append(tag)

    def handle_data(self, data):
        if self.capture: self.parts.append(data)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
            if self.capture and self.capture[1] == len(self.stack):
                self.values[self.capture[0]].append(''.join(self.parts))
                self.capture = None


def _matches_content(body, symbol, url, receipt):
    private = (receipt or {}).get('membership_publication') or {}
    row = next((r for r in private.get('new', []) if r.get('url') == url), None)
    if row:
        copy = row.get('preview_content')
        if not copy: return 'unknown'
        parser = _Preview()
        parser.feed(body.decode('utf-8'))
        expected = {key: [p['text'] for p in copy[key]] if key == 'preview' else [copy[key]['text']]
                    for key in parser.values}
        return 'available' if parser.values == expected else 'changed'
    digest = (receipt or {}).get('files', {}).get(url.split('/', 3)[-1])
    if not digest: return 'unknown'
    if hashlib.sha256(body).hexdigest() == digest: return 'available'
    if body.count(CF_BEACON) == 1:
        for addition in (CF_BEACON, CF_BEACON + b'\n'):
            if addition in body and hashlib.sha256(body.replace(addition, b'', 1)).hexdigest() == digest:
                return 'available'
    return 'changed'


def _public_probe(origin, expected, receipt=None):
    """Bounded public GETs verify receipt-bound content, not just HTTP success."""
    with urlopen(Request(origin+'/posts.json', headers={'User-Agent':'SMNOperationalAlert/1.0'}), timeout=8) as response:
        posts = json.load(response)
    statuses = {}
    if len(expected) > 6: raise ValueError('Unexpected observer article count')
    for symbol, url in expected.items():
        try:
            with urlopen(Request(url, headers={'User-Agent':'SMNOperationalAlert/1.0', 'Cache-Control':'no-cache'}), timeout=8) as response:
                body = response.read(MAX_PUBLIC_BYTES + 1)
                statuses[url] = (_matches_content(body, symbol, url, receipt)
                                 if response.status == 200 and len(body) <= MAX_PUBLIC_BYTES else 'unknown')
        except HTTPError as exc:
            statuses[url] = 'missing' if exc.code == 404 else 'unknown'
        except (OSError, URLError, ValueError, KeyError, TypeError):
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


def _delivery_incidents(now, settings, target, ledger_dir):
    if target != 'production':
        return []
    incidents = []
    for phase, setting in (('weekday_newsletter', settings.get('weekday_newsletter',
                                                              settings['daily_generation'])),
                           ('sunday_summary', settings.get('sunday_summary',
                                                           settings['daily_generation']))):
        local = now.astimezone(ZoneInfo(setting['timezone']))
        if phase == 'weekday_newsletter' and local.weekday() >= 5:
            continue
        if phase == 'sunday_summary' and local.weekday() != 6:
            continue
        date = local.date().isoformat()
        record = _json(Path(ledger_dir)/f'production-{phase}-{date}.json')
        if not isinstance(record, dict) or record.get('target') != 'production' or record.get('phase') != phase or record.get('date') != date:
            continue
        if record.get('status') == 'failed':
            detail = f'{phase} failed with exit {record.get("exit_code")}'
        elif record.get('status') == 'running':
            try:
                started = datetime.fromisoformat(record['started_at'].replace('Z', '+00:00'))
                age = (now - started).total_seconds() if started.tzinfo else 0
            except (KeyError, AttributeError, TypeError, ValueError):
                age = 0
            if age < 30 * 60:
                continue
            detail = f'{phase} has remained running for at least 30 minutes'
        else:
            continue
        incidents.append(_incident(date, 'newsletter-delivery-failed' if record['status'] == 'failed' else 'newsletter-delivery-stuck',
                                   detail, f'SMN {phase.replace("_", " ")} needs attention ({date})'))
    return incidents


def inspect(root, now, settings, public_probe=_public_probe, target='production', ledger_dir=LEDGER_DIR):
    """Return independent reader and mail incidents without mutating source receipts."""
    reader = _inspect_reader(root, now, settings, public_probe, target, ledger_dir)
    return reader + _delivery_incidents(now, settings, target, ledger_dir) + _campaign_incidents(now, target)


def _campaign_incidents(now, target):
    if target != 'production':return []
    state=_json(CAMPAIGN_STATE) or {}
    incidents=[]
    for key,record in state.get('campaigns',{}).items():
        if not isinstance(record,dict):continue
        phase=record.get('phase','')
        status=record.get('provider_status')
        if status in {'failed','canceled','cancelled'}:
            detail='Campaign '+key+' provider reports '+status
        elif phase in {'create_unknown','schedule_unknown','provider_unknown'}:
            detail='Campaign '+key+' has an uncertain provider outcome; inspect known ID before any retry'
        elif record.get('poll_count',0)>=48 and not (status=='sent' and record.get('provider_finished_at')):
            detail='Campaign '+key+' exhausted bounded provider status polls'
        elif status in {'ready','queued','sending'} and _campaign_overdue(record,now):
            detail='Campaign '+key+' remains '+status+' more than 30 minutes after its intended send time; delivery is unconfirmed'
        else:continue
        date=str(record.get('date') or key.rsplit(':',1)[-1])
        incidents.append(_incident(date,'newsletter-provider-needs-attention',detail,'SMN newsletter provider needs attention'))
    return incidents


def _campaign_overdue(record, now):
    try:
        if record.get('scheduled_for_utc'):
            intended=datetime.fromisoformat(record['scheduled_for_utc'].replace('Z','+00:00'))
        else:
            date,clock,zone=record['scheduled_for_account_time'].split()
            intended=datetime.strptime(date+' '+clock,'%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(zone))
        return intended.tzinfo is not None and (now-intended).total_seconds()>30*60
    except (KeyError,TypeError,ValueError):
        return False


def _inspect_reader(root, now, settings, public_probe, target, ledger_dir):
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
        if (isinstance(record, dict) and record.get('target') == target and record.get('date') == date and
                record.get('phase') not in {'weekday_newsletter', 'sunday_summary'}):
            ledgers.append(record)
    failed = [r for r in ledgers if r.get('status') == 'failed']
    incidents = []
    continuity_incidents = []
    for name in ('continuity-progress.json', 'continuity-delivery-state.json'):
        record = _json(root/date/name) or {}
        if record.get('date') == date and record.get('status') == 'running':
            ledgers.append({'status':'running', 'started_at':record.get('updated_utc')})
        if record.get('date') == date and record.get('status') in {'needs_attention', 'held'}:
            continuity_incidents.append(_incident(date, 'continuity-needs-attention',
                name + ': ' + str(record.get('reason') or record['status']),
                f'SMN automatic recovery needs attention ({date})'))
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
    continuity = receipt_ok and receipt.get('publication_policy') == 'continuity-v1'
    published = expected
    if continuity:
        published = receipt.get('published_symbols')
        pending = receipt.get('pending_symbols')
        declared = receipt.get('expected_symbols')
        coverage = receipt.get('coverage_status')
        valid = (receipt.get('edition_date') == date and isinstance(published, list) and isinstance(pending, list) and
                 isinstance(declared, list) and all(isinstance(s, str) for s in published + pending + declared) and
                 len(set(published + pending)) == len(published + pending) and
                 set(published + pending) == set(declared) and
                 (declared == expected or (not expected and not declared and coverage == 'notice')) and
                 coverage == ('complete' if declared and not pending else 'partial' if published else 'notice') and
                 receipt.get('complete') is (coverage == 'complete'))
        if not valid:
            return [_incident(date, 'edition-incomplete', 'Invalid declared publication coverage',
                              f'SMN publication coverage differs ({date})')]
        if not published:
            return continuity_incidents + [_incident(date, 'coverage-pending', 'Dated notice published; current article coverage remains pending',
                              f'SMN current coverage pending ({date})')]
    reader_done = False
    public_problem = None
    if receipt_ok and published:
        urls = {symbol:f'{origin}/editions/{date}/{symbol}/article.html' for symbol in published}
        try:
            public, statuses = (public_probe(origin, urls, receipt) if public_probe is _public_probe
                                else public_probe(origin, urls))
            live = {(p.get('symbol'), urljoin(origin, p.get('url', ''))) for p in public if isinstance(p, dict)
                    and str(p.get('published_date', ''))[:10] == date
                    and (p.get('edition_id') == 'subscription-' + date or receipt.get('membership_publication'))}
            missing = sorted(symbol for symbol, url in urls.items()
                             if (symbol, url) not in live or statuses.get(url) == 'missing')
            unknown = sorted(symbol for symbol, url in urls.items() if statuses.get(url) != 'available')
            if missing:
                public_problem = 'Expected public articles missing: ' + ', '.join(missing)
            elif any(statuses.get(url) == 'changed' for url in urls.values()):
                public_problem = 'Public article content differs from approved publication: ' + ', '.join(
                    symbol for symbol, url in urls.items() if statuses.get(url) == 'changed')
            elif unknown:
                public_problem = 'Public article URL availability could not be verified: ' + ', '.join(unknown)
            else:
                reader_done = not continuity or receipt.get('complete') is True
        except (OSError, ValueError, TypeError, HTTPError, URLError):
            public_problem = 'Public article catalog could not be verified'
    if continuity and not reader_done:
        detail = public_problem or 'Verified coverage published; pending subjects: ' + ', '.join(receipt['pending_symbols'])
        kind = ('verification-unavailable' if 'could not be verified' in detail else 'edition-incomplete') if public_problem else 'coverage-pending'
        return continuity_incidents + [_incident(date, kind, detail, f'SMN publication update ({date})')]
    if reader_done:
        return []
    if receipt_ok and public_problem:
        kind = 'verification-unavailable' if 'could not be verified' in public_problem else 'edition-incomplete'
        return [_incident(date, kind, public_problem, f'SMN published content requires attention ({date})')]
    if continuity_incidents:
        return continuity_incidents
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
    # Only the explicit continuity opt-in makes target_time a delivery deadline.
    # Legacy target settings retain their existing no-start/stall semantics.
    policy = _json(CONTINUITY_ACTIVATION) or {}
    if (target == 'production' and policy.get('reader_provider') == 'chatgpt' and
            policy.get('publication_policy') == 'continuity-v1' and
            local.strftime('%H:%M') >= schedule['target_time']):
        return [_incident(date, 'deadline-missed',
                          'Continuity delivery deadline reached without verified current coverage',
                          f'SMN publication deadline needs attention ({date})')]
    grace = schedule.get('no_start_grace_minutes')
    active_child = any(r.get('status') == 'running' for r in ledgers)
    waiting = current and current.get('status') == 'waiting_for_selection'
    if (current is None or waiting) and not active_child and isinstance(grace, int) and grace > 0:
        start = datetime.combine(local.date(), time.fromisoformat(schedule['start_time']), zone)
        if local >= start + timedelta(minutes=grace):
            return [_incident(date, 'no-start', 'No fresh reader generation after configured start grace',
                              f'SMN morning run did not start ({date})')]
    stall = schedule.get('stall_minutes')
    if (active_child or (current and current.get('status') == 'running')) and isinstance(stall, int) and stall > 0:
        progress = _last_progress(root, date, current or {})
        starts = []
        for record in ledgers:
            if record.get('status') != 'running': continue
            try:
                starts.append(datetime.fromisoformat(record['started_at'].replace('Z', '+00:00')).timestamp())
            except (KeyError, TypeError, ValueError):
                pass
        if starts: progress = max(starts + ([progress] if progress is not None else []))
        if progress is None:
            progress = datetime.combine(local.date(), time.fromisoformat(schedule['start_time']), zone).timestamp()
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
            result = json.load(response)
            identifier = result.get('id') if isinstance(result, dict) else None
            if 200 <= response.status < 300 and isinstance(identifier, str) and identifier.strip():
                return {'provider_id': identifier, 'status': 'accepted'}
            return False
    except (OSError, HTTPError, URLError, ValueError):
        return False


def retrieve_resend(provider_id):
    """GET-only evidence for a known accepted email; returns no message body."""
    key = os.environ.get('RESEND_API_KEY', '')
    if not key or key.upper() == 'PLACEHOLDER':
        return None
    request = Request('https://api.resend.com/emails/'+quote(provider_id, safe=''),
                      headers={'Authorization': 'Bearer '+key})
    with urlopen(request, timeout=10) as response:
        data = json.load(response)
    return {name: data.get(name) for name in ('id', 'to', 'subject', 'last_event')}


def reconcile_alert_delivery(state, now, fetcher):
    """Record explicit provider delivery, never reinterpret acceptance as delivery."""
    delivered = 0
    errors = []
    for key, record in state.get('accepted', {}).items():
        if key in state.get('delivered', {}):
            continue
        if record.get('delivery_poll_count', 0) >= MAX_ALERT_DELIVERY_POLLS:
            errors.append('Alert delivery polling exhausted; inspect known provider ID')
            continue
        retry = record.get('next_delivery_poll_utc')
        if retry and now < datetime.fromisoformat(retry):
            continue
        record['delivery_poll_count'] = record.get('delivery_poll_count', 0)+1
        record['next_delivery_poll_utc'] = (now+timedelta(minutes=30)).isoformat()
        try:
            data = fetcher(record['provider_id'])
            if (not isinstance(data, dict) or data.get('id') != record['provider_id'] or
                    data.get('to') != [record.get('recipient')] or
                    not record.get('subject') or data.get('subject') != record['subject']):
                raise ValueError('Alert delivery identity is unconfirmed')
            event = data.get('last_event')
            record['last_event'] = event
            record['last_delivery_check_utc'] = now.isoformat()
            if event == 'delivered':
                state.setdefault('delivered', {})[key] = {
                    'provider_id': record['provider_id'], 'status': 'delivered',
                    'evidence': data, 'observed_delivered_utc': now.isoformat()}
                delivered += 1
            elif event in {'bounced', 'failed', 'canceled', 'suppressed', 'complained'}:
                record['delivery_poll_count'] = MAX_ALERT_DELIVERY_POLLS
                errors.append('Provider reports alert '+event+'; no duplicate send')
            else:
                errors.append('Accepted alert delivery remains unconfirmed')
        except Exception:
            # Do not store raw relay errors, which may expose request credentials.
            errors.append('Alert delivery evidence unavailable')
    return delivered, errors


def run(root, now=None, settings=None, public_probe=_public_probe, sender=send_resend,
        baseline=False, target='production', ledger_dir=LEDGER_DIR, delivery_fetcher=None):
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
    version = _key(str(last.get('date','')), target, 'content-v2:' + str(last.get('utc','')) + ':' +
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
        posts, statuses = (public_probe(origin, expected, _json(receipt_path)) if public_probe is _public_probe
                           else public_probe(origin, expected))
        if all(value in {'available','missing','changed'} for value in statuses.values()):
            selected = [p for p in posts if isinstance(p, dict) and urljoin(origin, p.get('url','')) in set(expected.values())]
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
              'last_delivery_error':None, 'sent':0, 'accepted':0, 'delivered':0,
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
        return {'observed': len(incidents), 'sent': 0, 'accepted':0, 'delivered':0, 'baseline': baseline}
    accepted = 0
    state.setdefault('accepted', {})
    state.setdefault('send_attempts', {})
    delivered, errors = reconcile_alert_delivery(state, now, delivery_fetcher or retrieve_resend)
    _save(path, state)
    if errors:
        status['last_delivery_error'] = errors[-1]
        status['delivery_state'] = 'needs_attention'
    for item in incidents:
        if item['key'] in acknowledged:
            continue
        for recipient in recipients:
            receipt = item['key'] + ':' + recipient
            # Preserve old dedupe records without reclassifying them as actual delivery.
            if state.get('delivered', {}).get(receipt) or state['accepted'].get(receipt):
                continue
            attempt = state['send_attempts'].setdefault(receipt, {'count': 0, 'first_utc': now.isoformat()})
            # A stable provider idempotency key has a bounded lifetime. Never
            # repeat an uncertain POST on a later day as though it were new.
            age = (now-datetime.fromisoformat(attempt['first_utc'])).total_seconds()
            if attempt['count'] >= MAX_ALERT_SEND_ATTEMPTS or age >= 23*60*60:
                status['last_delivery_error'] = 'Alert acceptance retry limit reached; inspect provider before retry'
                status['delivery_state'] = 'needs_attention'
                continue
            retry = attempt.get('next_attempt_utc')
            if retry and now < datetime.fromisoformat(retry):
                continue
            attempt['count'] += 1
            attempt['next_attempt_utc'] = (now+timedelta(minutes=5)).isoformat()
            _save(path, state)  # Journal the attempt before calling the relay.
            try:
                result = sender(recipient, item, os.environ.get('SMN_ALERT_FROM') or DEFAULT_FROM)
            except Exception:
                result = None
            if isinstance(result, dict) and result.get('status') == 'accepted' and result.get('provider_id'):
                state['accepted'][receipt] = {**result, 'accepted_utc':now.isoformat(),
                                             'recipient':recipient, 'subject':item['subject']}
                _save(path, state)
                accepted += 1
            else:
                status['last_delivery_error'] = 'Resend acceptance not confirmed'
                status['delivery_state'] = 'needs_attention'
    status['accepted'] = accepted
    status['delivered'] = delivered
    _save(status_path, status)
    return {'observed': len(incidents), 'sent': 0, 'accepted': accepted, 'delivered':delivered, 'baseline': False}


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
