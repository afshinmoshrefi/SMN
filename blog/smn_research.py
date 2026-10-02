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
from smn_primary_sources import _mutable_release_url
from subscription_writer import load_json, save_json, sha256

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['company', 'angle', 'category', 'question', 'brief', 'hero_alt', 'sources', 'chart', 'material_context'],
    'properties': {
        'company': {'type': 'string'},
        'angle': {'type': 'string'},
        'category': {'type': 'string'},
        'question': {'type': 'string'},
        'brief': {'type': 'string'},
        'hero_alt': {'type': 'string'},
        'material_context': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id','kind','source_id','quote','summary','required','event_date'],
            'properties': {'id': {'type':'string'},
                'kind': {'type':'string','enum':['cause','counterpoint','event','fact']},
                'source_id': {'type':'string'}, 'quote': {'type':'string'},
                'summary': {'type':'string'}, 'required': {'type':'boolean'},
                'event_date': {'type':'string'}}}},
        'sources': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'title', 'url', 'date', 'excerpt'],
            'properties': {'id': {'type': 'string'}, 'title': {'type': 'string'}, 'url': {'type': 'string'},
                           'date': {'type': 'string'}, 'excerpt': {'type': 'string'}}}},
        'chart': {'type': 'object', 'additionalProperties': False, 'required': ['spec', 'records'],
            'properties': {
                'spec': {'type': 'object', 'additionalProperties': False,
                    'required': ['title', 'subtitle', 'question', 'note', 'unit', 'rows'],
                    'properties': {'title': {'type': 'string'}, 'subtitle': {'type': 'string'},
                        'question': {'type': 'string'}, 'note': {'type': 'string'}, 'unit': {'type': 'string'},
                        'rows': {'type': 'array', 'items': {
                            'type': 'object', 'additionalProperties': False, 'required': ['record_id', 'label'],
                            'properties': {'record_id': {'type': 'string'}, 'label': {'type': 'string'}}}}}},
                'records': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['id', 'value', 'unit', 'period', 'status', 'source_id', 'locator', 'quote'],
                    'properties': {'id': {'type': 'string'}, 'value': {'type': 'number'},
                        'unit': {'type': 'string'}, 'period': {'type': 'string'},
                        'status': {'type': 'string', 'enum': ['reported', 'historical', 'revised']},
                        'source_id': {'type': 'string'}, 'locator': {'type': 'string'},
                        'quote': {'type': 'string'}}}}}},
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
  Distinguish the edition date, last observation, future window entry and source publication date.
  A current observation does not establish the condition at a future entry. Old news is dated background;
  an event an old source called upcoming is not still upcoming without dated confirmation after the edition.
  Preserve material causes and qualifications in primary management statements: execution failures,
  delayed deals and offsetting segment strength must not become a story solely about external demand.
  Give the writer the relevant causes, not just the most convenient headline explanation. If the saved
  evidence cannot establish a current event or cause, state that limitation instead of inventing it.
  Identify the actual instrument. A spot index level is not a directly investable security; its percentage
  changes do not establish attainable profits on futures, options or exchange-traded products.
- hero_alt: alt text describing a conceptual illustration for the story (no numbers).
- sources[].excerpt: a factual summary of what that source says (60-150 words), with its key numbers.
- material_context: 1 to 8 immutable evidence items for the writer and reviewer. Each has a unique id,
  kind cause|counterpoint|event|fact, source_id, quote (a complete verbatim primary-source statement),
  summary, required (true for information needed to prevent a materially misleading article), and
  event_date (YYYY-MM-DD when the primary source establishes the event date, otherwise an empty string).
  Preserve ALL material management explanations of the story, including execution or deal delays and
  offsetting strength, not only a convenient external-demand explanation. Causes require captured primary
  evidence; do not substitute a news summary. Keep material counterevidence and confirmed event dates.
  Do not invent missing explanations or calendar dates. Do not use this list for TradeWave calculations.
- chart: one small bar chart of 2-6 REPORTED business values from one or two sources (not TradeWave seasonal
  numbers, not forecasts). Every record has the exact number, a unit (for example "USD billions"), a period,
  status "reported", its source_id, a locator, and "quote": the exact phrase from the saved text that
  contains the number. A decrease is a NEGATIVE value (fell 16% -> -16) and an increase is positive.
  All rows share one unit. rows[].record_id points to records[].id.
TradeWave seasonal numbers are shown elsewhere; do not restate them as business facts.'''


def _text(path, limit=40000):
    return Path(path).read_text(encoding='utf-8', errors='replace')[:limit] if Path(path).exists() else ''


def saved_text(root, sym, limit=None):
    """Fresh primary-source pages (root/primary/SYM.txt) first, then production's saved news."""
    primary = Path(root)/'primary'/(sym + '.txt')
    # Four bounded primary captures plus their headers must reach the researcher intact.
    return (_text(primary, 128000 if limit is None else limit) + '\n' +
            _text(Path(root)/'production'/sym/'audit/research_context.txt', 12000 if limit is None else limit))


def _post(root, sym):
    return next(p for p in load_json(Path(root)/'production/posts.json') if p['symbol'] == sym)


def evidence(root, date, sym, example):
    src = Path(root)/'production'/sym
    post = {k: _post(root, sym).get(k) for k in ('symbol', 'title', 'dek', 'direction', 'pattern_start_date',
                                                  'pattern_days', 'lookback_years', 'published_date')}
    original = _post(root, sym)
    if original.get('source_mode') == 'selected_inputs':
        export = load_json(Path(root)/'production-engine-export.json')
        selected = next(s for s in export['studies'] if s['identity']['symbol']==sym)
        response = next(r for r in selected['responses'] if r['request']['years']==original['lookback_years'])
        study = {'meta':selected['identity'],'stats':response['response']['stats']}
    else:
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


def _scaled(record):
    """Tables often report a billions value in millions ($46,743 = 46.743 billion)."""
    if re.search(r'billion|million', record['unit'], re.I):
        return _variants(round(abs(record['value']) * 1000, 6))
    return set()


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


LENGTHS = {'company': 2, 'category': 5, 'question': 20, 'brief': 200, 'hero_alt': 20}


def _spaced(s):
    """Collapse line breaks, tabs and no-break spaces: source pages wrap sentences mid-line."""
    return re.sub(r'\s+', ' ', s).strip()


def check(entry, root, date, sym):
    """Return a list of concrete problems (empty when the entry is usable)."""
    problems = []
    text = saved_text(root, sym, 10**7)
    primary = Path(root)/'primary'/(sym + '.txt')
    receipt = Path(root)/'primary'/(sym + '.receipt.json')
    if primary.exists() or receipt.exists():
        try:
            proof = load_json(receipt)
            if (primary.is_symlink() or receipt.is_symlink() or
                    proof['edition_date'] != date or proof['symbol'] != sym or
                    proof['text_sha256'] != sha256(primary.read_bytes())):
                raise ValueError('receipt mismatch')
            primary_urls = [{item['url'], item['final_url']} for item in proof['sources']]
            if len(primary_urls) < 2:
                raise ValueError('too few captured sources')
        except (OSError, KeyError, TypeError, ValueError):
            problems.append('primary evidence receipt is missing or changed')
        else:
            cited = {s['url'] for s in entry['sources']}
            if sum(bool(cited & urls) for urls in primary_urls) < 2:
                problems.append('cite at least two captured primary sources')
    # Length and count rules live here, not in the output schema: a schema miss only tells the
    # model "invalid" and Sonnet-low burned all 5 CLI retries on it (Sept 28-29: COST, TTWO, O).
    # These messages say exactly what to fix, and the research-two retry receives them.
    for field, minimum in LENGTHS.items():
        if len(str(entry.get(field) or '').strip()) < minimum:
            problems.append('%s is %d characters; it needs at least %d'
                            % (field, len(str(entry.get(field) or '').strip()), minimum))
    counts = (('sources', entry.get('sources') or [], 2, 5),
              ('chart.spec.rows', entry['chart']['spec'].get('rows') or [], 2, 8),
              ('chart.records', entry['chart'].get('records') or [], 2, 8))
    for name, items, low, high in counts:
        if not low <= len(items) <= high:
            problems.append('%s has %d items; it needs %d-%d' % (name, len(items), low, high))
    for src in entry.get('sources') or []:
        if len(src.get('excerpt', '').strip()) < 80:
            problems.append('source %s excerpt is %d characters; it needs at least 80'
                            % (src.get('id'), len(src.get('excerpt', '').strip())))
    for rec in entry['chart'].get('records') or []:
        if len(rec.get('quote', '').strip()) < 5:
            problems.append('record %s quote is shorter than 5 characters' % rec.get('id'))
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{4,80}', entry['angle']):
        problems.append('angle must be UPPER_SNAKE_CASE')
    ids = [s['id'] for s in entry['sources']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-z0-9-]{3,60}', i) for i in ids):
        problems.append('source ids must be unique lowercase-hyphen identifiers')
    for s in entry['sources']:
        if not s['url'].startswith('https://') or s['url'] not in text:
            problems.append('source %s URL is not an https URL from the saved news text' % s['id'])
        if _mutable_release_url(s['url']):
            problems.append('source %s uses a mutable BLS release URL; find and capture a dated archive' % s['id'])
        try:
            if datetime.strptime(s['date'], '%Y-%m-%d').date().isoformat() > date:
                problems.append('source %s is dated after the edition date' % s['id'])
        except ValueError:
            problems.append('source %s date must be YYYY-MM-DD' % s['id'])
    records = {r['id']: r for r in entry['chart']['records']}
    unit = entry['chart']['spec']['unit']
    flat = _spaced(text)
    for r in records.values():
        if r['source_id'] not in ids:
            problems.append('record %s cites an unknown source' % r['id'])
        if r['unit'] != unit:
            problems.append('record %s unit differs from the chart unit' % r['id'])
        if _spaced(r['quote']) not in flat:
            problems.append('record %s quote is not verbatim in the saved news text' % r['id'])
        elif not any(v in r['quote'] for v in _variants(abs(r['value'])) | _scaled(r)):
            problems.append('record %s value %s does not appear in its quote' % (r['id'], r['value']))
        problem = direction_problem(r)
        if problem:
            problems.append(problem)
    for row in entry['chart']['spec']['rows']:
        if row['record_id'] not in records:
            problems.append('chart row %s has no record' % row['record_id'])
    problems.extend(check_material_context(entry, root, date, sym))
    return problems


def check_material_context(entry, root, date, sym):
    """Validate the prewriter contract against independently captured primary text."""
    items=entry.get('material_context')
    if not isinstance(items,list) or not items or len(items)>8:
        return ['material_context must be a list of 1 to 8 primary evidence items']
    from editorial_gate import primary_sources
    try:
        documents=primary_sources(Path(root),sym,date)
    except (OSError,KeyError,TypeError,ValueError) as exc:
        return ['material_context primary custody invalid: '+str(exc)]
    sources={s['id']:s for s in entry['sources']};problems=[];seen=set()
    for item in items:
        if not isinstance(item,dict):
            problems.append('material_context items must be objects')
            continue
        identifier=item.get('id','')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-z0-9-]{3,60}',identifier) or identifier in seen:
            problems.append('material_context ids must be unique lowercase-hyphen identifiers')
            identifier=str(identifier)
        seen.add(identifier)
        if item.get('kind') not in {'cause','counterpoint','event','fact'} or type(item.get('required')) is not bool:
            problems.append('material_context %s has invalid kind/required flag' % identifier)
        source=sources.get(item.get('source_id'))
        document=documents.get(source['url']) if source else None
        quote=item.get('quote','')
        if not document or not isinstance(quote,str) or len(quote.strip())<20 or _spaced(quote) not in _spaced(document['text']):
            problems.append('material_context %s quote is not verbatim captured primary text for its source_id' % identifier)
        if not isinstance(item.get('summary'),str) or len(item['summary'].strip())<10:
            problems.append('material_context %s needs a substantive summary' % identifier)
        event_date=item.get('event_date')
        if not isinstance(event_date,str):
            problems.append('material_context %s event_date must be YYYY-MM-DD or empty' % identifier)
        elif event_date:
            try:
                datetime.strptime(event_date,'%Y-%m-%d')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',event_date):raise ValueError('format')
            except ValueError:
                problems.append('material_context %s event_date must be YYYY-MM-DD or empty' % identifier)
    return problems


def to_sources(entry):
    """Convert the checked answer to the sources.json shape the edition code reads."""
    spec = dict(entry['chart']['spec'], id='business-context', kind='bars')
    records = [{k: r[k] for k in ('id', 'value', 'unit', 'period', 'status', 'source_id', 'locator')}
               for r in entry['chart']['records']]
    sources = [dict(s, max_derived_words=200, excerpt_kind='verified_factual_summary') for s in entry['sources']]
    return {**{k: entry[k] for k in ('company', 'angle', 'category', 'question', 'brief', 'hero_alt', 'material_context')},
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
