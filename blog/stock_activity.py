"""Private completed-session volume research, calculated from source rows.

The thresholds flag research candidates, not predicted returns or automatic
publication. A reviewed adapter supplies the series and exchange calendar.
No config, app imports, external calls, queues or publication writes.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from statistics import median
from urllib.parse import urlsplit

from news_selection import utc_time
from reader_promise import fingerprint


@dataclass(frozen=True)
class VolumePolicy:
    baseline_sessions: int = 20
    comparison_sessions: int = 60
    minimum_multiple: float = 2.0
    minimum_percentile: float = 95.0
    minimum_median_shares: int = 250000


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a measurement')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError('Positive finite measurement required')
        return number
    except InvalidOperation as exc:
        raise ValueError('Invalid measurement') from exc


def _rounded(number):
    return float(number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def analyze_stock_volume(packet, *, as_of, policy=None):
    """Compare the latest completed session with PRIOR sessions only.

    The baseline is median split-adjusted share volume over the prior 20
    sessions; percentile is the percentage of prior 60 volumes strictly below
    today's. Ties are not records. Price change uses dividend/split-adjusted
    closes and is labelled accordingly. No buy/sell or participant inference.
    """
    policy = policy or VolumePolicy()
    if (type(policy.baseline_sessions) is not int or type(policy.comparison_sessions) is not int
            or not 5 <= policy.baseline_sessions <= policy.comparison_sessions <= 252
            or not 1 <= policy.minimum_multiple <= 20 or not 50 <= policy.minimum_percentile <= 100
            or type(policy.minimum_median_shares) is not int or policy.minimum_median_shares <= 0):
        raise ValueError('Invalid bounded volume policy')
    result = {'status': 'held', 'publishable': False, 'research_candidate': False,
              'issues': [], 'policy': asdict(policy), 'as_of': utc_time(as_of).isoformat()}
    try:
        clock = utc_time(as_of)
        if not isinstance(packet, dict) or packet.get('interval') != 'completed_daily':
            raise ValueError('COMPLETED_DAILY_VOLUME_REQUIRED')
        identity, audit, calendar = packet['instrument'], packet['audit'], packet['calendar']
        if not all(isinstance(identity.get(k), str) and identity[k].strip()
                   for k in ('symbol', 'resource_id', 'exchange', 'provider', 'series_id')):
            raise ValueError('VOLUME_IDENTITY_MISSING')
        if identity.get('asset_class') != 'equity':
            raise ValueError('STOCK_VOLUME_ONLY')
        if (audit.get('status') != 'reviewed' or audit.get('symbol') != identity['symbol']
                or not audit.get('reviewed_by') or not audit.get('source_ref')
                or audit.get('volume_basis') != 'provider_split_adjusted_shares'
                or audit.get('price_basis') != 'provider_split_dividend_adjusted_close'
                or urlsplit(str(audit.get('source_url', ''))).scheme != 'https'
                or not 0 <= (clock - utc_time(audit['reviewed_at'])).total_seconds() <= 7 * 86400):
            raise ValueError('VOLUME_SOURCE_UNREVIEWED')
        if (calendar.get('checked_as_of') != clock.date().isoformat()
                or not calendar.get('source_ref') or not calendar.get('calendar_id')):
            raise ValueError('VOLUME_CALENDAR_UNREVIEWED')
        close = utc_time(calendar['latest_session_close'])
        latest = date.fromisoformat(calendar['expected_latest_date'])
        if close.date() != latest or not 0 <= (clock - close).total_seconds() <= 5 * 86400:
            raise ValueError('VOLUME_SESSION_INCOMPLETE_OR_STALE')
        sessions = calendar['completed_sessions']
        if (not isinstance(sessions, list) or not sessions or sessions != sorted(set(sessions))
                or sessions[-1] != str(latest) or len(sessions) < policy.comparison_sessions + 1):
            raise ValueError('VOLUME_SESSION_COVERAGE_MISSING')
        rows = packet['rows']
        if not isinstance(rows, list) or not policy.comparison_sessions + 1 <= len(rows) <= 10000:
            raise ValueError('VOLUME_HISTORY_TOO_SHORT_OR_UNBOUNDED')
        by_date = {}
        for row in rows:
            day = str(date.fromisoformat(row['date']))
            if day in by_date:
                raise ValueError('VOLUME_DUPLICATE_DATE')
            volume, adjusted_close = _number(row['volume']), _number(row['close'])
            if volume != volume.to_integral_value():
                raise ValueError('VOLUME_MUST_BE_WHOLE_SHARES')
            by_date[day] = (volume, adjusted_close)
        expected = sessions[-policy.comparison_sessions - 1:]
        if max(by_date) != str(latest) or any(d not in by_date for d in expected):
            raise ValueError('VOLUME_GAP_OR_WRONG_LATEST_SESSION')
        if any(d not in sessions for d in by_date if d >= expected[0]):
            raise ValueError('VOLUME_UNEXPECTED_SESSION')
        sample = [by_date[d][0] for d in expected[:-1]]
        typical = median(sample[-policy.baseline_sessions:])
        today, adjusted_close = by_date[str(latest)]
        multiple = today / typical
        percentile = Decimal(100) * sum(v < today for v in sample) / len(sample)
        change = Decimal(100) * (adjusted_close / by_date[expected[-2]][1] - 1)
        flag = (multiple >= Decimal(str(policy.minimum_multiple))
                and percentile >= Decimal(str(policy.minimum_percentile))
                and typical >= policy.minimum_median_shares)
        measurements = {'session_date': str(latest), 'session_close': close.isoformat(),
            'shares': int(today), 'baseline_median_shares': float(typical),
            'baseline_start': expected[-policy.baseline_sessions - 1], 'baseline_end': expected[-2],
            'baseline_sessions': policy.baseline_sessions, 'volume_multiple': _rounded(multiple),
            'comparison_start': expected[0], 'comparison_end': expected[-2],
            'comparison_sessions': len(sample), 'volume_percentile': _rounded(percentile),
            'adjusted_close_change_pct': _rounded(change),
            'interpretation': 'Relative trading activity; no inference of net buying, institutional identity or future direction.'}
        result.update(status='research_candidate' if flag else 'ordinary_activity',
            research_candidate=flag, instrument=deepcopy(identity), measurements=measurements,
            evidence_id='stock-volume:' + fingerprint({'instrument': identity, 'measurements': measurements}),
            provenance={'rows_sha256': fingerprint(rows), 'calendar_sha256': fingerprint(calendar),
                        'source_ref': audit['source_ref'], 'source_url': audit['source_url'],
                        'volume_basis': audit['volume_basis'], 'price_basis': audit['price_basis']})
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result['issues'] = [str(exc) if isinstance(exc, ValueError) else 'MALFORMED_VOLUME_INPUT']
    return result


def scan_stock_volume(packets, *, as_of, policy=None):
    if not isinstance(packets, list) or len(packets) > 1000:
        raise ValueError('Explicit stock universe of at most 1000 required')
    results = [analyze_stock_volume(p, as_of=as_of, policy=policy) for p in packets]
    identities = [r.get('evidence_id') for r in results if r.get('evidence_id')]
    for result in results:
        if result.get('evidence_id') and identities.count(result['evidence_id']) > 1:
            result.update(status='held', research_candidate=False, issues=['DUPLICATE_VOLUME_CANDIDATE'])
    flagged = sorted((r for r in results if r['research_candidate']),
                     key=lambda r: (-r['measurements']['volume_multiple'], r['instrument']['symbol']))
    return {'publishable': False, 'as_of': utc_time(as_of).isoformat(), 'results': results,
            'research_candidates': flagged, 'universe_size': len(packets),
            'scope': 'Supplied universe only. Thresholds are provisional editorial filters, not calibrated predictions.'}


def attach_activity_event(candidate, *, as_of):
    """Build the event claim from arithmetic; preserve reviewed news as context.

    A volume alert still needs an upstream editorial significance/question
    assessment. Recomputed activity never silently overrides contrary news.
    """
    item = deepcopy(candidate)
    measured = analyze_stock_volume(item.get('volume_input'), as_of=as_of)
    item['activity_gate'] = measured
    item['discovery_route'] = 'stock_volume'
    if not measured['research_candidate']:
        item['news_packet'] = {}
        return item
    m, identity = measured['measurements'], measured['instrument']
    packet = deepcopy(item.get('news_packet') or {'research': {}})
    research = packet.setdefault('research', {})
    sources = [s for s in research.get('sources', []) if s.get('id') != 'stock-volume-source']
    claims = [c for c in research.get('claims', []) if c.get('id') != 'stock-volume-fact']
    text = (f"{identity['symbol']} recorded {m['shares']:,} split-adjusted shares of volume on {m['session_date']}, "
            f"{m['volume_multiple']:.2f} times the median of the preceding {m['baseline_sessions']} sessions "
            f"({m['baseline_start']} through {m['baseline_end']}). Its volume exceeded "
            f"{m['volume_percentile']:.2f}% of the previous {m['comparison_sessions']} daily observations. "
            f"Its split/dividend-adjusted close changed {m['adjusted_close_change_pct']:+.2f}% from the prior session. "
            "Volume describes turnover and does not identify traders, buying intent, a cause or a future return.")
    sources.append({'id': 'stock-volume-source', 'title': 'TradeWave calculation from EODHD daily stock data',
        'url': measured['provenance']['source_url'], 'published_at': m['session_close'],
        'verified_at': utc_time(as_of).isoformat(), 'source_type': 'primary', 'role': 'event',
        'page_type': 'market_data', 'verified': True, 'excerpt': text,
        'provenance': measured['provenance']})
    claims.append({'id': 'stock-volume-fact', 'text': text, 'source_ids': ['stock-volume-source'],
                   'event_time': m['session_close']})
    research.update(sources=sources, claims=claims, activity_evidence=measured)
    # Editorial materiality is reviewed upstream. A flagged measurement alone
    # cannot self-award the highest priority or turn on a publisher.
    editorial = item.get('activity_editorial') or {}
    packet['event'] = {'event_id': 'stock-volume:' + identity['symbol'] + ':' + m['session_date'],
        'development_id': measured['evidence_id'], 'headline': identity['symbol'] + ' unusual share turnover',
        'event_time': m['session_close'], 'event_time_basis': 'reported_event',
        'claim_ids': ['stock-volume-fact'], 'source_ids': ['stock-volume-source'],
        'significance': editorial.get('significance'), 'audience_relevance': editorial.get('audience_relevance'),
        'significance_reason': editorial.get('significance_reason'), 'relevance_reason': editorial.get('relevance_reason'),
        'material_development': editorial.get('reviewed') is True, 'major_event': False}
    item.update(news_packet=packet, kind='news', instrument=identity, material_news=False)
    return item


def main(argv=None):
    import argparse
    from pathlib import Path
    from news_pipeline import _preview_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.packet).read_text(encoding='utf-8'))
    result = scan_stock_volume(packet['instruments'], as_of=packet['as_of'])
    target = _preview_directory(args.out) / 'stock-volume-scan.json'
    target.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'universe_size': result['universe_size'], 'flagged': len(result['research_candidates']), 'publishable': False}))


if __name__ == '__main__':
    main()
