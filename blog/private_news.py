"""Private news-to-lineup adapter; no fetching, config or publication imports.

The input ``news_packet`` contains the inspected event/research contract used
by news_selection. Source inspection remains an upstream responsibility:
validate_event checks that contract, not semantic entailment or remote truth.
Never pass uninspected LLM output as a verified research packet.

Question ``answer_claim_ids`` and priority ``claim_ids`` explicitly identify
support in that packet. They become stable source evidence IDs for the lineup.
Supplied gates, eligibility booleans and supersession assertions are discarded.
The returned gate is a fresh evidence snapshot, not an authentication token.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import re

from news_selection import NewsPolicy, utc_time, validate_event


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode('utf-8')).hexdigest()


def _ids(value):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(x) for x in value
                             if isinstance(x, (str, int)) and not isinstance(x, bool) and str(x).strip()))


def _references(record, claim_field, evidence_field, claims, source_ids):
    """Map only explicitly named support; unknown references remain visible."""
    if claim_field not in record:
        return _ids(record.get(evidence_field))
    result = []
    for claim_id in _ids(record.get(claim_field)):
        if claim_id not in claims:
            result.append('unknown_news_claim:' + claim_id)
        else:
            result.extend(source_ids[sid] for sid in claims[claim_id]['source_ids'])
    return sorted(set(result))


def prepare_news_candidate(candidate: dict, *, as_of, policy: NewsPolicy | None = None) -> dict:
    """Revalidate ``candidate['news_packet']`` and derive an isolated gate.

    Macro news can supply instrument={}; seasonal measurements are independent.
    Every call rechecks the explicit replay clock. Gate validity expires at the
    earliest event/source verification deadline. Source evidence IDs do not
    depend on source numbering, retrieval time or editorial wording. Fact hashes identify text,
    not semantic novelty: this adapter never invents a supersession relation.
    """
    clock, policy = utc_time(as_of), policy or NewsPolicy()
    if policy.enabled or policy.automatic_publication:
        raise ValueError('Private news policy must remain disabled and unpublished')
    for field in ('max_event_age_hours', 'max_source_age_hours', 'max_verification_age_hours'):
        if type(getattr(policy, field)) is not int or getattr(policy, field) <= 0:
            raise ValueError('Private freshness limits must be positive whole hours')
    item = deepcopy(candidate)
    for key in ('news_gate', 'cohort_gate', 'seasonal_eligible', 'news_eligible', 'event', 'news_validation'):
        item.pop(key, None)
    packet = item.get('news_packet') or {}
    packet = packet if isinstance(packet, dict) else {}
    event, research = packet.get('event', {}), packet.get('research', {})
    checked = validate_event(event, research, now=clock, policy=policy)
    issues = list(checked['issues'])
    event = event if isinstance(event, dict) else {}
    sources = {s['id']: s for s in checked['sources']}
    claims = {c['id']: c for c in checked['claims']}
    evidence_index, source_ids = {}, {}
    for sid, source in sources.items():
        excerpt_hash = hashlib.sha256(source['excerpt'].encode('utf-8')).hexdigest()
        provenance = source.get('provenance') or {}
        body_hash = provenance.get('body_sha256') if isinstance(provenance, dict) else None
        # Actual retrieved body identity, when supplied by the trusted fetch
        # adapter, survives a shorter selected excerpt. Legacy inspected packets
        # have only an excerpt snapshot; neither identity proves a new event.
        body_hash = body_hash if isinstance(body_hash, str) and re.fullmatch('[0-9a-f]{64}', body_hash) else None
        eid = 'news_source:' + _hash({'url': source['url'], 'body_sha256': body_hash or excerpt_hash})
        source_ids[sid] = eid
        evidence_index[eid] = {'url': source['url'], 'body_sha256': body_hash,
                               'excerpt_sha256': excerpt_hash, 'published_at': source['published_at'],
                               'verified_at': source['verified_at'], 'source_id': sid}
    event_claims = [claims[cid] for cid in _ids(event.get('claim_ids')) if cid in claims]
    fact_ids = sorted({'news_fact:' + _hash({'text': ' '.join(claim['text'].split()),
                                          'source_urls': sorted(sources[s]['url'] for s in claim['source_ids'])})
                       for claim in event_claims})
    event_evidence = sorted({source_ids[s] for c in event_claims for s in c['source_ids']})
    occurred = checked.get('event_time')
    deadlines = []
    if occurred:
        occurred = utc_time(occurred)
        if occurred > clock:
            issues.append('event_time_in_future_strict_private_gate')
        deadlines.append(occurred + timedelta(hours=policy.max_event_age_hours))
        occurred = occurred.isoformat()
    for source in sources.values():
        deadlines.append(utc_time(source['verified_at']) + timedelta(hours=policy.max_verification_age_hours))
        if source.get('role') != 'context':
            deadlines.append(utc_time(source['published_at']) + timedelta(hours=policy.max_source_age_hours))
    bound_event = {'event_id': event.get('event_id'), 'development_id': event.get('development_id'),
                   'occurred_at': occurred, 'fact_ids': fact_ids, 'evidence_ids': event_evidence}
    item.update(kind='news', event=bound_event, publishable=False, policy_mode='private_v2')
    for field, claim_field, support_field in (('question', 'answer_claim_ids', 'answer_evidence_ids'),
                                            ('priority', 'claim_ids', 'evidence_ids')):
        record = item.get(field)
        if isinstance(record, dict):
            record[support_field] = _references(record, claim_field, support_field, claims, source_ids)
    gate = {**deepcopy(bound_event), 'eligible': checked['valid'] and not issues,
            'issues': issues, 'valid_until': min(deadlines).isoformat() if deadlines else clock.isoformat(),
            'evidence_ids': sorted(evidence_index), 'as_of': clock.isoformat(),
            'evidence_sha256': _hash({'event': event, 'sources': checked['sources'], 'claims': checked['claims']})}
    item['news_gate'] = gate
    item['news_validation'] = {'event': checked, 'evidence_index': evidence_index,
                               'source_evidence_ids': source_ids,
                               'supersession': 'not_inferred_requires_separate_review'}
    return item


def generate_private_news(candidate: dict, *, as_of, send=None, output_dir=None) -> dict:
    """Recheck eligibility, then run the bounded reader-reviewed private draft."""
    from market_policy import evaluate_lineup
    from news_pipeline import run_news_article
    item = prepare_news_candidate(candidate, as_of=as_of)
    lineup = evaluate_lineup([item], as_of=as_of)
    if not lineup['selected']:
        return {'status': 'hold', 'publishable': False, 'policy_mode': 'private_v2',
                'provider_calls': 0, 'selection': lineup, 'news_gate': item['news_gate']}
    packet = item['news_packet']
    result = run_news_article(packet['event'], packet['research'], send=send,
                              now=as_of, output_dir=output_dir, reader_policy='private_v2',
                              reader_question=item.get('question'))
    result.update(news_gate=item['news_gate'], policy_mode='private_v2', publishable=False)
    return result
