"""Byte-bound, date-only engine volume delivery. No financial calculations."""
from contextlib import contextmanager
from datetime import datetime, time, timezone
import csv
import copy
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

FILES = ('highest_volume_list.csv', 'highest_volume_spikes.csv')
SCHEMA = 'tradewave-volume-v2'
DELIVERY_SCHEMA = 'tradewave-volume-delivery-v1'
NY = ZoneInfo('America/New_York')
SHA = re.compile(r'[0-9a-f]{64}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
TICKER = re.compile(r'[A-Z0-9][A-Z0-9.^_-]{0,31}\Z')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def timestamp(value):
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.tzinfo is None:
        raise ValueError('Timestamp needs timezone')
    return instant.astimezone(timezone.utc)

def session_date(value):
    day = datetime.fromisoformat(value).date()
    if day.isoformat() != value:
        raise ValueError('Session needs exact ISO date')
    return day

def parse_feed(raw, proof, *, now, warn=lambda message: None):
    """Retain date precision and supplied values; older rows remain dated only."""
    if now.tzinfo is None or proof.get('schema') != SCHEMA or proof.get('source') != 'tradewave':
        raise ValueError('Invalid volume clock or provenance identity')
    if proof.get('csv_sha256') != digest(raw) or not SHA.fullmatch(proof['csv_sha256']):
        raise ValueError('Volume bytes/hash mismatch')
    if proof.get('date_precision') != 'day' or proof.get('observation_kind') != 'completed_session':
        raise ValueError('Date-only completed-session provenance required')
    generated = timestamp(proof['generated_at'])
    if not 0 <= (now - generated).total_seconds() <= 24 * 3600:
        raise ValueError('Export clock is future or stale')
    authority = proof['completion_proof']
    completed = session_date(authority['completed_session'])
    if authority.get('ok') is not True or not SHA.fullmatch(authority['marker_sha256']) or not SHA.fullmatch(authority['generation_fingerprint']):
        raise ValueError('Missing successful engine completion proof')
    completion_clock=timestamp(authority['completed_at'])
    if not 0 <= (generated-completion_clock).total_seconds() <= 96*3600 or completed >= generated.astimezone(NY).date():
        raise ValueError('Completion/export/session mismatch')
    if not COMMIT.fullmatch(proof['source_commit']):
        raise ValueError('Missing immutable producer revision')
    observations = proof['observations']; rows = []; seen = set()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    if reader.fieldnames != ['rank', 'ticker', 'avg_volume_30d', 'today_volume', 'rvol']:
        raise ValueError('Unexpected engine CSV schema')
    for row in reader:
        ticker = row['ticker']
        if not TICKER.fullmatch(ticker) or ticker in seen:
            raise ValueError('Invalid or duplicate volume ticker')
        seen.add(ticker)
        observation = observations[ticker]; day = session_date(observation['observation_date'])
        if day > completed or not SHA.fullmatch(observation['source_csv_sha256']):
            raise ValueError('Row date/source differs from engine session proof')
        if int(row['rank']) < 1 or int(row['avg_volume_30d']) < 0 or int(row['today_volume']) < 0:
            raise ValueError('Malformed engine volume row')
        rvol = float(row['rvol'])
        if not math.isfinite(rvol) or rvol < 0:
            raise ValueError('Malformed supplied RVOL')
        # Date-only precision: bound maximum possible age from day start;
        # this is an age bound, never a latest-session/calendar inference.
        if (now - datetime.combine(day, time.min, NY)).total_seconds() > 96 * 3600:
            warn('Omitted stale dated volume for ' + ticker)
            continue
        rows.append({'ticker': ticker, 'avg_volume_30d': int(row['avg_volume_30d']),
            'today_volume': int(row['today_volume']), 'rvol': rvol,
            'volume_provenance': {'source': 'tradewave', 'schema': SCHEMA,
                'csv_sha256': proof['csv_sha256'], 'source_csv_sha256': observation['source_csv_sha256'],
                'source_commit': proof['source_commit'], 'observation_date': day.isoformat(),
                'date_precision': 'day', 'generated_at': generated.isoformat(),
                'observed_at': None, 'observation_kind': 'completed_session',
                'scope': 'prior_session', 'completion_proof': dict(authority)}})
    if set(observations) != seen:
        raise ValueError('Provenance/CSV ticker set differs')
    return rows

def build_bundle(raw_files, observations, completion_proof, source_commit, *, now):
    files = {}
    for name in FILES:
        raw = raw_files[name]
        symbols = [row['ticker'] for row in csv.DictReader(io.StringIO(raw.decode()))]
        proof = {'schema': SCHEMA, 'source': 'tradewave', 'source_commit': source_commit,
            'csv_sha256': digest(raw), 'generated_at': now.isoformat(), 'date_precision': 'day',
            'observation_kind': 'completed_session', 'completion_proof': copy.deepcopy(completion_proof),
            'observations': {symbol: copy.deepcopy(observations[symbol]) for symbol in symbols}}
        parse_feed(raw, proof, now=now)
        files[name] = {'csv': raw.decode('utf-8'), 'provenance': proof}
    bundle = {'schema': DELIVERY_SCHEMA, 'files': files}
    return {**bundle, 'delivery_id': digest(canonical(bundle))}

def validate_bundle(bundle, *, now):
    if not isinstance(bundle, dict) or set(bundle) != {'schema', 'files', 'delivery_id'} or bundle['schema'] != DELIVERY_SCHEMA:
        raise ValueError('Invalid delivery envelope')
    if set(bundle['files']) != set(FILES) or not SHA.fullmatch(bundle['delivery_id']):
        raise ValueError('Invalid delivery files/identity')
    if digest(canonical({k: v for k, v in bundle.items() if k != 'delivery_id'})) != bundle['delivery_id']:
        raise ValueError('Delivery hash mismatch')
    rows = {}
    for name, item in bundle['files'].items():
        if set(item) != {'csv', 'provenance'} or len(item['csv']) > 1_000_000:
            raise ValueError('Unexpected delivery fields/size')
        rows[name] = parse_feed(item['csv'].encode(), item['provenance'], now=now)
    first = bundle['files'][FILES[0]]['provenance']
    second = bundle['files'][FILES[1]]['provenance']
    if any(first[key] != second[key] for key in ['source_commit', 'generated_at', 'completion_proof']):
        raise ValueError('Feed generation/session identities differ')
    return rows

def verified_peer(remote, header, expected):
    """Use a verified fixed-header loopback proxy, never arbitrary forwarding."""
    try:
        actual = ipaddress.ip_address(remote); producer = ipaddress.ip_address(expected)
        if actual.is_loopback:
            actual = ipaddress.ip_address(header)
        return actual == producer
    except (TypeError, ValueError):
        return False

def atomic(path, raw):
    path = Path(path)
    with tempfile.NamedTemporaryFile(prefix=path.name+'.tmp-',dir=path.parent,delete=False) as stream:
        temp=Path(stream.name)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
    sync_directory(path.parent)

def sync_directory(path):
    if os.name != 'nt':
        descriptor=os.open(path,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

@contextmanager
def delivery_lock(directory):
    path = Path(directory) / '.delivery.lock'
    if path.is_symlink():
        raise ValueError('Unsafe delivery lock')
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0,2)
            if handle.tell()==0:
                handle.write(b'\0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

def generation_path(directory, identifier):
    if not SHA.fullmatch(identifier):
        raise ValueError('Unsafe generation identity')
    directory = Path(directory)
    path = directory / '.volume-generations' / identifier
    if directory.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise ValueError('Unsafe volume generation path')
    return path

def resolve_feed(path):
    path = Path(path); pointer = path.parent / 'current-volume-delivery.json'
    if not pointer.exists():
        return path
    if pointer.is_symlink() or path.name not in FILES:
        raise ValueError('Unsafe volume pointer')
    value = json.loads(pointer.read_text())
    generation = generation_path(path.parent, value['delivery_id'])
    bundle = json.loads((generation / 'delivery.json').read_text())
    if bundle['delivery_id'] != value['delivery_id'] or digest(canonical({k:v for k,v in bundle.items() if k!='delivery_id'})) != value['delivery_id']:
        raise ValueError('Volume pointer/generation binding mismatch')
    target = generation / path.name
    if target.is_symlink() or target.with_name(target.name + '.provenance.json').is_symlink():
        raise ValueError('Unsafe volume generation file')
    if target.read_bytes() != bundle['files'][path.name]['csv'].encode() or json.loads(target.with_name(target.name + '.provenance.json').read_text()) != bundle['files'][path.name]['provenance']:
        raise ValueError('Volume generation files changed')
    return target

def install_bundle(directory, bundle, *, now, attribution=None, failure=lambda stage: None):
    rows = validate_bundle(bundle, now=now); directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Unsafe receiver directory')
    directory.mkdir(parents=True, exist_ok=True)
    identifier = bundle['delivery_id']; target = generation_path(directory, identifier)
    pointer = directory / 'current-volume-delivery.json'
    with delivery_lock(directory):
        if pointer.is_symlink():
            raise ValueError('Unsafe receiver pointer')
        current = json.loads(pointer.read_text()) if pointer.exists() else None
        if current:
            # Resolve current binding before replacing anything.
            resolve_feed(directory / FILES[0]); resolve_feed(directory / FILES[1])
            old = json.loads((generation_path(directory,current['delivery_id'])/'delivery.json').read_text())
            previous = timestamp(old['files'][FILES[0]]['provenance']['generated_at'])
            incoming = timestamp(bundle['files'][FILES[0]]['provenance']['generated_at'])
            if incoming < previous:
                raise ValueError('Stale delivery cannot replace current generation')
            if incoming == previous and current['delivery_id'] != identifier:
                raise ValueError('Ambiguous same-clock generation')
        target.mkdir(parents=True, exist_ok=True)
        sync_directory(target.parent);sync_directory(directory)
        for name, item in bundle['files'].items():
            for suffix, raw in [('', item['csv'].encode()), ('.provenance.json',canonical(item['provenance']))]:
                path = target / (name + suffix)
                if path.is_symlink():
                    raise ValueError('Unsafe immutable generation file')
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != raw:
                        raise ValueError('Immutable generation collision')
                else:
                    atomic(path, raw)
        manifest = target / 'delivery.json'
        if manifest.is_symlink():
            raise ValueError('Unsafe delivery manifest')
        if manifest.exists():
            if manifest.is_symlink() or manifest.read_bytes() != canonical(bundle):
                raise ValueError('Immutable delivery manifest collision')
        else:
            atomic(manifest, canonical(bundle))
        receipt_path = target / 'receipt.json'
        if receipt_path.is_symlink():
            raise ValueError('Unsafe delivery receipt')
        saved=json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        accepted=saved['received_at'] if saved else now.isoformat()
        if timestamp(accepted)>now:
            raise ValueError('Future receiver receipt')
        receipt = {'status': 'verified', 'delivery_id': identifier,
            'received_at':accepted,'attribution':attribution,
            'csv_sha256': {name:item['provenance']['csv_sha256'] for name,item in bundle['files'].items()},
            # Immutable received counts, not a promise of freshness at every
            # later read. Consumer qualification applies its clock separately.
            'row_counts': {name:len(item['provenance']['observations']) for name,item in bundle['files'].items()}}
        if receipt_path.exists():
            if receipt_path.is_symlink() or receipt_path.read_bytes() != canonical(receipt):
                raise ValueError('Immutable delivery receipt collision')
        failure('before_pointer')
        atomic(pointer, canonical({'delivery_id': identifier}))
        failure('after_pointer')
        if not receipt_path.exists():
            atomic(receipt_path,canonical(receipt))
        failure('before_ack')
        return receipt
