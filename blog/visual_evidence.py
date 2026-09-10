"""Numeric inputs for private editorial graphics. No model-supplied plot values.

Upstream adapters own source extraction and verification. A digest detects
changes; it does not authenticate the source or prove an extracted fact true.
The independent editorial review must compare the ledger with source excerpts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import re
from statistics import median
from urllib.parse import urlparse


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Chart values must be finite numbers, not inferred missing observations')
    return value


def validate_bundle(bundle):
    """Reject ambiguous units, duplicate identities, unsafe links and stale input."""
    b = deepcopy(bundle)
    if b.get('schema_version') != 1:
        raise ValueError('Unknown visual evidence version')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', b.get('story_id', '')):
        raise ValueError('Invalid story identity')
    datetime.fromisoformat(b['as_of'].replace('Z', '+00:00'))
    for key in ('sources', 'records', 'charts'):
        items = b.get(key)
        if not isinstance(items, list) or not items:
            raise ValueError('Missing visual ' + key)
        ids = [i['id'] for i in items]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-zA-Z0-9_-]+', str(i)) for i in ids):
            raise ValueError('Invalid or duplicate ' + key + ' identity')
    sources = {s['id']: s for s in b['sources']}
    for s in sources.values():
        url = s.get('url', '')
        derived = (s.get('source_type') in {'derived','engine_export'} and url == 'evidence/' + s['id'] + '.json'
                   and isinstance(s.get('payload'), dict) and s.get('payload_sha256') == digest(s['payload']))
        if (urlparse(url).scheme != 'https' and not derived) or not s.get('excerpt') or not s.get('title'):
            raise ValueError('Source needs HTTPS attribution or bound local derivation and inspected evidence')
    records = {r['id']: r for r in b['records']}
    for r in records.values():
        finite(r['value'])
        if r.get('source_id') not in sources or not all(r.get(k) for k in ('unit', 'period', 'locator')):
            raise ValueError('Every number needs its source, period, unit and extraction locator')
        if r.get('status') not in {'reported', 'previous_estimate', 'revised', 'historical'}:
            raise ValueError('Unsupported status: forecast ranges require a separate range adapter')
    for c in b['charts']:
        if c.get('kind') not in {'bars', 'volume'} or not all(c.get(k) for k in ('title', 'subtitle', 'question', 'note', 'unit')):
            raise ValueError('Chart needs an answer, units, period and methodology')
        rows = c.get('rows') or []
        if not 2 <= len(rows) <= 65 or c['kind'] == 'bars' and len(rows) > 8:
            raise ValueError('Chart would be empty or unreadable')
        seen = set()
        for row in rows:
            if set(row) - {'record_id', 'label'} or not row.get('label') or row.get('record_id') not in records:
                raise ValueError('Charts select existing records; no inline model values')
            r = records[row['record_id']]
            if row['record_id'] in seen or r['unit'] != c['unit']:
                raise ValueError('Duplicate point or incompatible units')
            seen.add(row['record_id'])
        if c['kind'] == 'volume':
            if len(rows) != 21 or c['unit'] != 'shares':
                raise ValueError('Volume view requires the latest session and exactly 20 prior sessions')
            dates = [records[r['record_id']]['period'] for r in rows]
            for date in dates:
                datetime.strptime(date, '%Y-%m-%d')
            if dates != sorted(set(dates)) or dates[-1] > b['as_of'][:10]:
                raise ValueError('Volume sessions must be complete, ordered and unique')
            if any(records[r['record_id']]['value'] < 0 for r in rows):
                raise ValueError('Negative share volume')
            if c.get('adjustment') != 'provider_split_adjusted_volume':
                raise ValueError('Volume adjustment basis required')
    if len(b['charts']) > 5 or b.get('primary_chart_id') not in {c['id'] for c in b['charts']}:
        raise ValueError('A bounded catalog and primary comparison are required')
    required = b.get('required_chart_ids', [b['primary_chart_id']])
    if (not 1 <= len(required) <= 2 or len(required) != len(set(required))
            or set(required) - {c['id'] for c in b['charts']} or b['primary_chart_id'] not in required):
        raise ValueError('Required charts must include the primary and fit the two-chart limit')
    expected = b.pop('evidence_sha256', None)
    actual = digest(b)
    if expected != actual:
        raise ValueError('Visual evidence has changed since preparation')
    b['evidence_sha256'] = actual
    return b


def chart_data(chart, bundle):
    records = {r['id']: r for r in bundle['records']}
    rows = [{**records[r['record_id']], 'label': r['label']} for r in chart['rows']]
    result = {'rows': rows, 'unit': chart['unit']}
    if chart['kind'] == 'volume':
        baseline = median(r['value'] for r in rows[:-1])
        result.update(prior_median=baseline,
                      relative_volume=rows[-1]['value'] / baseline if baseline > 0 else None,
                      baseline_start=rows[0]['period'], baseline_end=rows[-2]['period'])
    return result


def format_value(value, unit):
    if unit == 'weight_percent':
        return f'{Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP):.2f}%'
    if unit == 'percent':
        rounded = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if rounded == 0:
            rounded = abs(rounded)
        return f'{rounded:+.2f}'.rstrip('0').rstrip('.') + '%'
    if unit == 'shares':
        rounded = (Decimal(str(value)) / 1_000_000).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        return f'{rounded:.2f}m'
    if unit == 'thousand_jobs':
        rounded = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        return f'{rounded:+,.2f}'.rstrip('0').rstrip('.') if rounded else '0'
    rounded = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return f'{rounded:,.2f}'.rstrip('0').rstrip('.')
