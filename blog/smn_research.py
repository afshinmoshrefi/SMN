"""Build one sources.json entry per subject from production's already-fetched research.

The model gets production's saved news text (research_context.txt), the
production post and the TradeWave study summary. It has no tools. Code then
checks the answer: every URL must appear in the saved text, every chart number
must appear in the saved text, dates cannot be after the edition date, and the
bundle rules (units, statuses, rows) must hold. One retry with the exact
problems; then the day holds.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re

import smn_models
from subscription_writer import load_json, save_json, sha256

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['company', 'angle', 'category', 'question', 'brief', 'hero_alt', 'sources', 'chart'],
    'properties': {
        'company': {'type': 'string', 'minLength': 2},
        'angle': {'type': 'string', 'minLength': 5},
        'category': {'type': 'string', 'minLength': 5},
        'question': {'type': 'string', 'minLength': 20},
        'brief': {'type': 'string', 'minLength': 200},
        'hero_alt': {'type': 'string', 'minLength': 20},
        'sources': {'type': 'array', 'minItems': 2, 'maxItems': 5, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'title', 'url', 'date', 'excerpt'],
            'properties': {'id': {'type': 'string'}, 'title': {'type': 'string'}, 'url': {'type': 'string'},
                           'date': {'type': 'string'}, 'excerpt': {'type': 'string', 'minLength': 80}}}},
        'chart': {'type': 'object', 'additionalProperties': False, 'required': ['spec', 'records'],
            'properties': {
                'spec': {'type': 'object', 'additionalProperties': False,
                    'required': ['title', 'subtitle', 'question', 'note', 'unit', 'rows'],
                    'properties': {'title': {'type': 'string'}, 'subtitle': {'type': 'string'},
                        'question': {'type': 'string'}, 'note': {'type': 'string'}, 'unit': {'type': 'string'},
                        'rows': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {
                            'type': 'object', 'additionalProperties': False, 'required': ['record_id', 'label'],
                            'properties': {'record_id': {'type': 'string'}, 'label': {'type': 'string'}}}}}},
                'records': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['id', 'value', 'unit', 'period', 'status', 'source_id', 'locator', 'quote'],
                    'properties': {'id': {'type': 'string'}, 'value': {'type': 'number'},
                        'unit': {'type': 'string'}, 'period': {'type': 'string'},
                        'status': {'type': 'string', 'enum': ['reported', 'historical', 'revised']},
                        'source_id': {'type': 'string'}, 'locator': {'type': 'string'},
                        'quote': {'type': 'string', 'minLength': 5}}}}}},
    }}

RULES = '''You prepare the research brief for one Seasonal Market News article. You do not write the article.
Use ONLY the saved text below. Prefer the fresh primary sources at its start. Never invent a source, URL, date or number. Copy each URL exactly as it
appears after "URL:" in the saved text. Prefer primary sources (company releases, filings, official statistics)
and recent, dated news. Pick 2-4 sources.
- angle: a short UPPER_SNAKE_CASE identifier for today's story angle.
- category: like "Stocks / Business services and seasonal timing".
- question: the reader question the article answers today.
- brief: 120-250 words for the writer. State what is reported, what is forecast and what is upcoming as of
  the edition date, with dates. Say that business facts do not validate a future seasonal return.
- hero_alt: alt text describing a conceptual illustration for the story (no numbers).
- sources[].excerpt: a factual summary of what that source says (60-150 words), with its key numbers.
- chart: one small bar chart of 2-6 REPORTED business values from one or two sources (not TradeWave seasonal
  numbers, not forecasts). Every record has the exact number, a unit (for example "USD billions"), a period,
  status "reported", its source_id, a locator, and "quote": the exact phrase from the saved text that
  contains the number. A decrease is a NEGATIVE value (fell 16% -> -16) and an increase is positive.
  All rows share one unit. rows[].record_id points to records[].id.
TradeWave seasonal numbers are shown elsewhere; do not restate them as business facts.'''


def _text(path, limit=40000):
    return Path(path).read_text(encoding='utf-8', errors='replace')[:limit] if Path(path).exists() else ''


def saved_text(root, sym, limit=40000):
    """Fresh primary-source pages (root/primary/SYM.txt) first, then production's saved news."""
    primary = Path(root)/'primary'/(sym + '.txt')
    return (_text(primary, limit) + '\n' + _text(Path(root)/'production'/sym/'audit/research_context.txt', limit))


def _post(root, sym):
    return next(p for p in load_json(Path(root)/'production/posts.json') if p['symbol'] == sym)


def evidence(root, date, sym, example):
    src = Path(root)/'production'/sym
    post = {k: _post(root, sym).get(k) for k in ('symbol', 'title', 'dek', 'direction', 'pattern_start_date',
                                                  'pattern_days', 'lookback_years', 'published_date')}
    payload = load_json(src/'engine-payload.json')
    study = {'meta': payload.get('meta'), 'stats': payload.get('stats')}
    return ('EDITION DATE: ' + date + '\nPRODUCTION PICK (subject only, do not copy its prose):\n' + json.dumps(post) +
            '\nTRADEWAVE STUDY SUMMARY (context only):\n' + json.dumps(study)[:6000] +
            '\nEXAMPLE OF THE OUTPUT SHAPE (another subject, another day; do not reuse its facts):\n' +
            json.dumps(example)[:5000] +
            '\nSAVED NEWS TEXT (fresh primary sources first, then production news):\n' + saved_text(root, sym))


def _variants(value):
    v = float(value)
    out = {repr(value), str(value), ('%g' % v)}
    for d in (0, 1, 2, 3):
        out |= {f'{v:.{d}f}', f'{v:,.{d}f}'}
    return {x for x in out if x and x not in {'0', '0.0'}}


DOWN = re.compile(r'\b(fell|fall|falls|falling|declin\w*|down|drop\w*|decreas\w*|lower|shrank|shrink\w*|'
                  r'contract\w*|slump\w*|plung\w*|slid|slide\w*|los[st]|negative)\b', re.I)
UP = re.compile(r'\b(rose|rise|rises|rising|grew|grow\w*|increas\w*|up|gain\w*|climb\w*|jump\w*|higher|'
                r'surg\w*|advanc\w*)\b', re.I)


def direction_problem(record):
    """A reported change must carry its direction in the sign of the number."""
    unit = record['unit'].lower()
    if 'change' not in unit and 'growth' not in unit and '%' not in unit and 'percent' not in unit:
        return None
    down, up = bool(DOWN.search(record['quote'])), bool(UP.search(record['quote']))
    if down and not up and record['value'] > 0:
        return 'record %s: the quote reports a decrease but the value is positive; use %s' % (
            record['id'], -abs(record['value']))
    if up and not down and record['value'] < 0:
        return 'record %s: the quote reports an increase but the value is negative' % record['id']
    return None


def check(entry, root, date, sym):
    """Return a list of concrete problems (empty when the entry is usable)."""
    problems = []
    text = saved_text(root, sym, 10**7)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{4,80}', entry['angle']):
        problems.append('angle must be UPPER_SNAKE_CASE')
    ids = [s['id'] for s in entry['sources']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-z0-9-]{3,60}', i) for i in ids):
        problems.append('source ids must be unique lowercase-hyphen identifiers')
    for s in entry['sources']:
        if not s['url'].startswith('https://') or s['url'] not in text:
            problems.append('source %s URL is not an https URL from the saved news text' % s['id'])
        try:
            if datetime.strptime(s['date'], '%Y-%m-%d').date().isoformat() > date:
                problems.append('source %s is dated after the edition date' % s['id'])
        except ValueError:
            problems.append('source %s date must be YYYY-MM-DD' % s['id'])
    records = {r['id']: r for r in entry['chart']['records']}
    unit = entry['chart']['spec']['unit']
    for r in records.values():
        if r['source_id'] not in ids:
            problems.append('record %s cites an unknown source' % r['id'])
        if r['unit'] != unit:
            problems.append('record %s unit differs from the chart unit' % r['id'])
        if r['quote'] not in text:
            problems.append('record %s quote is not verbatim in the saved news text' % r['id'])
        elif not any(v in r['quote'] for v in _variants(abs(r['value']))):
            problems.append('record %s value %s does not appear in its quote' % (r['id'], r['value']))
        problem = direction_problem(r)
        if problem:
            problems.append(problem)
    for row in entry['chart']['spec']['rows']:
        if row['record_id'] not in records:
            problems.append('chart row %s has no record' % row['record_id'])
    return problems


def to_sources(entry):
    """Convert the checked answer to the sources.json shape the edition code reads."""
    spec = dict(entry['chart']['spec'], id='business-context', kind='bars')
    records = [{k: r[k] for k in ('id', 'value', 'unit', 'period', 'status', 'source_id', 'locator')}
               for r in entry['chart']['records']]
    sources = [dict(s, max_derived_words=200, excerpt_kind='verified_factual_summary') for s in entry['sources']]
    return {**{k: entry[k] for k in ('company', 'angle', 'category', 'question', 'brief', 'hero_alt')},
            'sources': sources, 'chart': [spec, records]}


def run(root, date, sym, roles, clis, example, stage='research', issues=None):
    """Prepare (if needed) and run one research job; return (entry, problems)."""
    root = Path(root)
    job = root/'jobs'/(sym + '-' + date.replace('-', '') + '-' + stage)
    if not job.exists():
        prompt = RULES + '\n' + evidence(root, date, sym, example)
        if issues:
            prompt += '\nYOUR PREVIOUS ANSWER HAD THESE PROBLEMS. Fix every one:\n' + '\n'.join(issues)
        until = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
        smn_models.prepare(roles, 'research', root/'jobs', job.name, prompt, SCHEMA, as_of=date,
                           valid_until=until, evidence_sha256=sha256(prompt.encode()), stage=stage)
    smn_models.run(job, clis)
    entry = load_json(job/'output.json')
    problems = check(entry, root, date, sym)
    save_json(job/'research-check.json', {'passed': not problems, 'problems': problems})
    return entry, problems
