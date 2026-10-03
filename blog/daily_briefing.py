"""Offline daily market briefing validation and private review packaging.

No discovery, model, media generation or publication calls. Evidence metadata is
an operator assertion; hashing and quote checks do not certify editorial truth.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import jsonschema
from subscription_writer import load_json, save_json, sha256

SCHEMA_PATH = Path(__file__).parent / 'schemas/daily_briefing.schema.json'
PREFERRED_SOURCES = ('CNBC', 'Bloomberg', 'Reuters', 'NYT Business', 'WSJ', 'FT')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8'))


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp needs an explicit UTC offset')
    return parsed


def _flat(text):
    return re.sub(r'\s+', ' ', text).strip()


def _https(value):
    url = urlsplit(value)
    return url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password


def inspect(briefing, review=None):
    """Return mechanical findings; approval requires a separately bound review.

    Capture excerpts must have been taken from an actually accessed full article
    or primary release. A search result is retained as a lead, never fact evidence.
    """
    issues, warnings = [], []
    schema = load_json(SCHEMA_PATH)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(briefing),
                    key=lambda e: str(list(e.path)))
    if errors:
        return {'status': 'held', 'issues': [f'{list(e.path)}: {e.message}' for e in errors],
                'warnings': [], 'review_status': 'pending', 'generation_status': 'disabled'}
    binding = digest(briefing)

    def time(value, label):
        try:
            return timestamp(value)
        except ValueError as exc:
            issues.append(label + ': ' + str(exc))
            return None

    cutoff = time(briefing['cutoff'], 'cutoff')
    try:
        edition = date.fromisoformat(briefing['edition_date'])
    except ValueError:
        issues.append('edition_date must be a real ISO date')
        edition = None
    if cutoff and edition and cutoff.astimezone(ZoneInfo('America/New_York')).date() != edition:
        issues.append('cutoff must fall on edition_date in America/New_York')

    def index(items, label):
        result = {item['id']: item for item in items}
        if len(result) != len(items):
            issues.append(label + ' IDs must be unique')
        return result

    sources = index(briefing['sources'], 'source')
    groups = index(briefing['groups'], 'group')
    claims = index(briefing['claims'], 'claim')
    event_keys = [g['event_key'] for g in groups.values()]
    if len(event_keys) != len(set(event_keys)):
        issues.append('duplicate underlying event_key: merge repeated story groups')
    urls = [s['url'] for s in sources.values()]
    if len(urls) != len(set(urls)):
        issues.append('duplicate source URL: reuse its source ID')
    for sid, source in sources.items():
        if not _https(source['url']):
            issues.append(sid + ': source URL must be HTTPS without credentials')
        if source.get('timestamp_status') == 'uncertain' or not source['published_at']:
            issues.append(sid + ': publication timestamp is uncertain; cutoff qualification held')
        published = time(source['published_at'], sid + ' published_at') if source['published_at'] else None
        retrieved = time(source['retrieved_at'], sid + ' retrieved_at')
        updated = time(source['updated_at'], sid + ' updated_at') if source.get('updated_at') else published
        if published and updated and updated < published:
            issues.append(sid + ': update precedes publication')
        if updated and cutoff and updated > cutoff:
            issues.append(sid + ': source version is after cutoff')
        if updated and retrieved and updated > retrieved:
            issues.append(sid + ': retrieved before this source version')
        capture = source.get('capture')
        if source['access'] in {'full_text', 'primary_release'}:
            if not capture or not capture['text'].strip():
                issues.append(sid + ': accessed evidence needs captured text')
            elif capture['text_sha256'] != sha256(capture['text'].encode('utf-8')):
                issues.append(sid + ': captured text hash mismatch')
        if retrieved and cutoff and retrieved > cutoff:
            warnings.append(sid + ': retrospectively retrieved after cutoff; verify historical version')

    selected = {gid for gid, group in groups.items() if group['selected']}
    if not selected:
        issues.append('select at least one supported narrative')
    coverage = {}
    for gid, group in groups.items():
        unknown = set(group['source_ids']) - sources.keys()
        if unknown:
            issues.append(gid + ': unknown source refs ' + ', '.join(sorted(unknown)))
        origins = {sources[s]['origin_id'] for s in group['source_ids'] if s in sources}
        coverage[gid] = {'distinct_reporting_origins': len(origins), 'origin_ids': sorted(origins)}
        if group['selected'] and not group['source_ids']:
            issues.append(gid + ': selected narrative needs evidence')

    for cid, claim in claims.items():
        group = groups.get(claim['group_id'])
        if not group:
            issues.append(cid + ': unknown group ref')
        when = time(claim['event_at'], cid + ' event_at')
        if when and cutoff:
            if claim['event_status'] == 'upcoming' and when <= cutoff:
                issues.append(cid + ': upcoming event has already passed cutoff')
            elif claim['event_status'] in {'reported', 'background'} and when > cutoff:
                issues.append(cid + ': future event cannot be reported as a result')
            elif claim['event_status'] == 'reported' and edition and when.astimezone(ZoneInfo('America/New_York')).date() != edition:
                issues.append(cid + ': older event must be labeled background')
        for support in claim['supports']:
            source = sources.get(support['source_id'])
            if not source:
                issues.append(cid + ': unknown support source')
                continue
            if group and source['id'] not in group['source_ids']:
                issues.append(cid + ': support source is outside its event group')
            if source['access'] not in {'full_text', 'primary_release'}:
                issues.append(cid + ': snippets, metadata and failed access cannot support claims')
            if not _flat(support['quote']) or _flat(support['quote']) not in _flat((source.get('capture') or {}).get('text', '')):
                issues.append(cid + ': evidence quote is not in its captured source text')
        if claim['kind'] == 'interpretation' and not claim.get('attribution'):
            issues.append(cid + ': interpretation needs attribution')

    used_groups = set()
    for surface in ('narrative', 'script', 'storyboard'):
        for block in briefing[surface]:
            refs = set(block['claim_ids'])
            if refs - claims.keys():
                issues.append(surface + ': unknown claim refs')
            for cid in refs & claims.keys():
                gid = claims[cid]['group_id']
                if gid not in selected:
                    issues.append(surface + ': references an excluded group')
                if surface == 'narrative':
                    used_groups.add(gid)
            if surface == 'storyboard' and block['visual_kind'] == 'source_visual' and not block.get('source_id'):
                issues.append('storyboard: source visual needs a source ID')
            if surface == 'storyboard' and block.get('source_id'):
                src = sources.get(block['source_id'])
                if not src or src['access'] not in {'full_text', 'primary_release'}:
                    issues.append('storyboard: visual needs an accessed source')
    if selected - used_groups:
        issues.append('selected groups missing from narrative: ' + ', '.join(sorted(selected - used_groups)))

    scan = {item['publisher']: item for item in briefing['scan']}
    for publisher in PREFERRED_SOURCES:
        if publisher not in scan or scan[publisher]['status'] != 'accessed':
            warnings.append(publisher + ': preferred-source coverage incomplete')
    approved = False
    review_status = 'pending'
    if review:
        reviewed_at = time(str(review.get('reviewed_at', '')), 'reviewed_at')
        if review.get('briefing_sha256') != binding:
            issues.append('review binding is stale or belongs to another briefing')
            review_status = 'stale'
        elif review.get('status') == 'approved' and review.get('reviewer') and reviewed_at:
            approved = True
            for source in sources.values():
                captured_at = time(source['retrieved_at'], source['id'] + ' retrieved_at')
                if captured_at and reviewed_at < captured_at:
                    issues.append('editorial review predates its source capture')
        elif review.get('status') == 'rejected':
            issues.append('editorial review rejected this revision')
            review_status = 'rejected'
        elif review.get('status') != 'pending':
            issues.append('review needs status, named reviewer and timestamp')
    return {'status': 'held' if issues else ('reviewed' if approved else 'pending_editorial_review'),
            'briefing_sha256': binding, 'issues': issues, 'warnings': warnings,
            'review_status': 'approved' if approved and not issues else review_status,
            'generation_status': 'disabled', 'event_coverage': coverage}


def package(briefing, output, review=None, allow_held=False):
    result = inspect(briefing, review)
    if result['issues'] and (not allow_held or 'briefing_sha256' not in result):
        raise ValueError('\n'.join(result['issues']))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'briefing.json', briefing)
    save_json(output / 'validation.json', result)
    if review:
        save_json(output / 'editorial-review.json', review)
    e = html.escape
    sections = [f'<h1>{e(briefing["title"])}</h1>',
                f'<p>{e(briefing["edition_date"])} / {e(briefing["label"])} / cutoff {e(briefing["cutoff"])}</p>',
                f'<p>Private review artifact: {e(result["status"])}. No media generated.</p>']
    for surface in ('narrative', 'script', 'storyboard'):
        sections.append(f'<h2>{surface.title()}</h2>')
        for block in briefing[surface]:
            sections.append(f'<p>{e(block["text"])} <small>Claims: {e(", ".join(block["claim_ids"]))}</small></p>')
    sections.append('<h2>Selection and evidence</h2>')
    for group in briefing['groups']:
        sections.append(f'<p>{e(group["headline"])} / {"selected" if group["selected"] else "excluded"}: {e(group["reason"])}</p>')
    for source in briefing['sources']:
        title = e(source['title'])
        if _https(source['url']):
            title = f'<a href="{e(source["url"], quote=True)}">{title}</a>'
        sections.append(f'<p>{title} / {e(source["publisher"])} / origin {e(source["origin_id"])}</p>')
    sections.append('<h2>Coverage gaps</h2>')
    sections.extend(f'<p>HELD: {e(issue)}</p>' for issue in result['issues'])
    sections.extend(f'<p>{e(warning)}</p>' for warning in result['warnings'])
    sections.append('<h2>Claim map</h2>')
    for claim in briefing['claims']:
        sections.append(f'<p>{e(claim["id"])} / {e(claim["kind"])} / {e(claim["event_status"])}: {e(claim["text"])}</p>')
        for support in claim['supports']:
            sections.append(f'<p><small>{e(support["source_id"])} / {e(support["locator"])}: {e(support["quote"])}</small></p>')
    sections.append('<h2>Scan notes</h2>')
    sections.extend(f'<p>{e(item["publisher"])} / {e(item["status"])}: {e(item["note"])}</p>' for item in briefing['scan'])
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>SMN briefing review</title><body>' + ''.join(sections) + '</body></html>'
    (output / 'review.html').write_text(document, encoding='utf-8')
    save_json(output / 'receipt.json', {'status': result['status'], 'briefing_sha256': result['briefing_sha256'],
        'created_at': datetime.now(timezone.utc).isoformat(), 'publish': False, 'media_generated': False,
        'files': {p.name: sha256(p.read_bytes()) for p in output.iterdir() if p.is_file()}})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'package'])
    parser.add_argument('--input', required=True)
    parser.add_argument('--review')
    parser.add_argument('--output')
    parser.add_argument('--allow-held', action='store_true', help='Save a held private review package, never an approval')
    args = parser.parse_args()
    briefing = load_json(args.input)
    review = load_json(args.review) if args.review else None
    if args.action == 'package' and not args.output:
        parser.error('package requires --output (a new private directory)')
    try:
        result = package(briefing, args.output, review, args.allow_held) if args.action == 'package' else inspect(briefing, review)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, indent=2))
    return 1 if result['issues'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
