"""Optional TradeWave volume context; missing provenance never holds selection."""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


def read_verified_volume_csv(path, *, now=None, warn=print):
    """Read engine values only with a dated, byte-bound sidecar.

    The trusted producer must write ``<csv>.provenance.json`` with schema
    ``tradewave-volume-v1``, source ``tradewave``, csv_sha256, observation_date
    (US market date), and timezone-aware observed_at. File mtime is no proof.
    Prior observations need observation_kind=completed_session and remain dated
    context for at most 96 hours. They never populate legacy current RVOL.
    No engine calculation or supplied metric is recomputed here.
    """
    path = Path(path)
    try:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('verification clock must include its timezone')
        raw = path.read_bytes()
        proof = json.loads(Path(str(path) + '.provenance.json').read_text(encoding='utf-8'))
        if not isinstance(proof, dict) or proof.get('schema') != 'tradewave-volume-v1' or proof.get('source') != 'tradewave':
            raise ValueError('missing TradeWave provenance identity')
        if str(proof.get('csv_sha256', '')).lower() != hashlib.sha256(raw).hexdigest():
            raise ValueError('volume bytes do not match provenance')
        market_day = now.astimezone(ZoneInfo('America/New_York')).date()
        day = datetime.fromisoformat(proof['observation_date']).date()
        if proof['observation_date'] != day.isoformat() or day > market_day:
            raise ValueError('invalid or future observation date')
        observed = datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed.astimezone(ZoneInfo('America/New_York')).date() != day:
            raise ValueError('observation time is unverified or from a different session')
        kind = proof.get('observation_kind', 'intraday')
        if kind not in {'intraday', 'completed_session'} or (day < market_day and kind != 'completed_session'):
            raise ValueError('prior volume needs an explicitly completed observation')
        maximum_age = (24 if day == market_day else 96) * 60 * 60
        if not 0 <= (now - observed).total_seconds() <= maximum_age:
            raise ValueError('observation time is future or stale')
        provenance = {'source': 'tradewave', 'csv_sha256': proof['csv_sha256'].lower(),
                      'observation_date': day.isoformat(), 'observed_at': observed.isoformat(),
                      'observation_kind': kind, 'scope': 'current_session' if day == market_day else 'prior_session'}
        rows = []
        for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
            try:
                item = {'ticker': row['ticker'].upper().strip(),
                        'avg_volume_30d': int(row['avg_volume_30d']),
                        'today_volume': int(row['today_volume']),
                        'rvol': float(row['rvol']), 'volume_provenance': dict(provenance)}
                if item['ticker'] and math.isfinite(item['rvol']):
                    rows.append(item)
            except (KeyError, ValueError, TypeError):
                continue
        return rows
    except (OSError, ValueError, TypeError, KeyError, AttributeError, csv.Error) as error:
        warn(f'  Warning: omitted optional volume context {path.name}: {error}')
        return []


def volume_lookup(rows):
    """Choose the freshest supplied observation per ticker, retaining its proof."""
    result = {}
    for row in rows:
        proof = row.get('volume_provenance')
        if not proof or proof.get('scope') not in {'current_session', 'prior_session'}:
            continue
        key = (datetime.fromisoformat(proof['observed_at']), row['rvol'])
        old = result.get(row['ticker'])
        if old is None or key > (datetime.fromisoformat(old['volume_provenance']['observed_at']), old['rvol']):
            result[row['ticker']] = row
    return result


def volume_context(row):
    if not row:
        return None
    return {**row['volume_provenance'], **{k: row[k] for k in ('avg_volume_30d', 'today_volume', 'rvol')}}


def bind_news_evidence(items, news_results, earnings_results):
    """Canonical news lead comes from supplied news, not model/volume prose.

    Preserve the model's original reason as unverified extraction evidence;
    only an exact supplied title enters ranking/export as the news reason.
    This is a source lead, not a primary-source factcheck or article approval.
    """
    titles = {' '.join(str(row.get('title', '')).split()): row
              for row in list(news_results) + list(earnings_results) if row.get('title')}
    for item in items:
        item['unverified_extraction'] = {k: item.get(k) for k in ('news_reason', 'rvol', 'volume_reason')}
        key = ' '.join(str(item.get('news_headline') or '').split())
        source = titles.get(key)
        item['news_reason'] = str(source['title']) if source else ''
        item['news_headline'] = str(source['title']) if source else ''
        item['news_source_url'] = source.get('url', '') if source else ''
        item['rvol'] = None
        item.pop('volume_reason', None)
    return items


def bind_verified_volume(items, volume_map):
    """Volume stays separate; news, earnings and sales-volume prose are untouched."""
    for item in items:
        row = volume_map.get(item.get('ticker', '').upper().strip())
        item['volume_context'] = volume_context(row)
        item['rvol'] = (row['rvol'] if row and row['volume_provenance']['scope'] == 'current_session' else None)
    return items


def merge_volume_candidates(items, volume_map):
    """Add qualified volume subjects mechanically, outside news extraction."""
    seen = {str(item.get('ticker', '')).upper().strip() for item in items}
    for ticker in volume_map:
        if ticker not in seen:
            items.append({'ticker': ticker, 'company': ticker, 'asset_type': 'stock',
                          'news_reason': '', 'news_headline': '', 'news_date': '', 'news_sentiment': '',
                          'earnings_date': None, 'earnings_type': None})
    return bind_verified_volume(items, volume_map)
