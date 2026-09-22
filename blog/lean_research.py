"""One bounded research job per subject, then deterministic source checks.

Replaces the heartbeat agent's hand-built sources.json. Terra searches the web
in a single turn and returns a commission, verbatim source excerpts and the
business-chart records. Python checks the excerpts against the live pages and
every chart value against its excerpt, then writes the sources.json entry.
"""
from __future__ import annotations

from datetime import date as Date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import json
import re
import unicodedata

from subscription_writer import load_json, save_json, prepare_job, run_job

MODEL, EFFORT = 'gpt-5.6-terra', 'high'
MAX_DERIVED_WORDS = 200
CHART_ID = 'business-context'
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36'
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')

_text = {'type': 'string', 'minLength': 1}
SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['company', 'angle', 'category', 'question', 'brief', 'hero_alt', 'sources', 'chart', 'records'],
    'properties': {
        'company': _text, 'angle': _text, 'category': _text, 'question': _text, 'brief': _text, 'hero_alt': _text,
        'sources': {'type': 'array', 'minItems': 2, 'maxItems': 5, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'title', 'url', 'date', 'excerpt'],
            'properties': {'id': _text, 'title': _text, 'url': _text, 'date': _text, 'excerpt': _text}}},
        'chart': {'type': 'object', 'additionalProperties': False,
            'required': ['title', 'subtitle', 'question', 'note', 'unit', 'rows'],
            'properties': {'title': _text, 'subtitle': _text, 'question': _text, 'note': _text, 'unit': _text,
                'rows': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {'type': 'object',
                    'additionalProperties': False, 'required': ['record_id', 'label'],
                    'properties': {'record_id': _text, 'label': _text}}}}},
        'records': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'value', 'unit', 'period', 'status', 'source_id', 'locator'],
            'properties': {'id': _text, 'value': {'type': 'number'}, 'unit': _text, 'period': _text,
                'status': {'type': 'string', 'enum': ['reported', 'previous_estimate', 'revised', 'historical']},
                'source_id': _text, 'locator': _text}}}}}

BRIEF = '''You are the research editor for Seasonal Market News. Commission one article
about {company} ({symbol}) for the {date} edition. Search the web now. Return JSON only.

The article pairs a TradeWave seasonal study with the company's current business
news. TradeWave supplies all seasonal numbers; you never calculate or restate them.
STUDY: {study}

Find 2-4 current sources. Prefer primary sources: the issuer's own press
releases, investor-relations pages, regulator decisions and filings. Use a
reputable news report only for facts no primary source gives. Refresh every
dated fact for {date}; do not rely on older articles. Never use prediction or
price-forecast sites. Leads from an earlier automated search follow; treat them
as unverified and possibly stale or about the wrong company:
{leads}

For each source give its exact page URL, publication date (YYYY-MM-DD), title
and an excerpt of 40-150 words
copied VERBATIM from that page. Code fetches the page and rejects any excerpt
that is not on it, so never paraphrase, merge passages or fix typos; use '...'
only between two verbatim passages.

Build one small business chart: 2-8 bars from ONE reported release, one unit
(for example "USD billions"), each value copied from a source excerpt. Code
checks each value against its source excerpt. Each record needs a period
(e.g. "Q2 2026"), status, source_id and a locator naming where on the page it is.
The chart question/title must say what the bars show; the note gives the basis.

Commission: angle is a short label (under 12 words) joining the business
question and the seasonal window. question is the investor's live question.
brief (80-160 words) tells the writer the concrete stakes, the dated
developments to use, and what is still unknown. category is like
"Stocks / Pharmaceuticals and seasonal timing". hero_alt describes the attached
hero illustration literally in under 20 words (it is an AI illustration; do
not name it as a real product or place).'''


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def normalize(value):
    value = unicodedata.normalize('NFKC', value)
    value = value.translate(str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"',
                                           '–': '-', '—': '-', ' ': ' '}))
    return re.sub(r'\s+', ' ', value).strip().lower()


def page_text(url, timeout=20):
    """Visible page text, or None when the page cannot be read (blocked, PDF, error)."""
    try:
        with urlopen(Request(url, headers={'User-Agent': AGENT}), timeout=timeout) as r:
            if 'html' not in r.headers.get('Content-Type', ''):
                return None
            raw = r.read(4_000_000).decode(r.headers.get_content_charset() or 'utf-8', 'replace')
    except Exception:
        return None
    p = _Text(); p.feed(raw)
    return normalize(' '.join(p.parts))


def excerpt_on_page(excerpt, text):
    parts = [normalize(x) for x in re.split(r'\.\.\.|…', excerpt)]
    parts = [x.strip(' .') for x in parts if len(x.strip(' .')) >= 20]
    return bool(parts) and all(x in text for x in parts)


def numbers(text):
    return {Decimal(n.replace(',', '')) for n in NUMBER.findall(text)}


def value_in(value, text):
    """A chart value must appear in its excerpt, allowing rounding and a thousand-fold unit change."""
    v = abs(Decimal(str(value)))
    for n in numbers(text):
        for scaled in (n, n / 1000, n * 1000):
            places = max(0, -v.as_tuple().exponent)
            if scaled.quantize(Decimal(1).scaleb(-places)) == v:
                return True
    return False


def verify(entry, edition_date, fetch=page_text):
    """Return (sources.json entry, problems). Problems hold the subject."""
    problems, sources = [], []
    ids = [s['id'] for s in entry['sources']]
    if len(ids) != len(set(ids)):
        problems.append('duplicate source ids')
    for s in entry['sources']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', s['id']):
            problems.append(f"{s['id']}: id must be letters, digits, - or _")
        if urlparse(s['url']).scheme != 'https':
            problems.append(f"{s['id']}: URL must be https")
            continue
        try:
            if Date.fromisoformat(s['date']) > Date.fromisoformat(edition_date):
                problems.append(f"{s['id']}: dated after the edition")
        except ValueError:
            problems.append(f"{s['id']}: date must be YYYY-MM-DD")
        text = fetch(s['url'])
        if text is not None and not excerpt_on_page(s['excerpt'], text):
            problems.append(f"{s['id']}: excerpt not found on {s['url']}")
        sources.append({'id': s['id'], 'title': s['title'], 'url': s['url'], 'date': s['date'],
                        'excerpt': s['excerpt'], 'max_derived_words': MAX_DERIVED_WORDS,
                        'excerpt_kind': 'verbatim', 'fetch_verified': text is not None})
    if not any(s['fetch_verified'] for s in sources):
        problems.append('no source could be read back from its page')
    by_id = {s['id']: s for s in entry['sources']}
    records = {r['id']: r for r in entry['records']}
    for r in entry['records']:
        src = by_id.get(r['source_id'])
        if src is None:
            problems.append(f"{r['id']}: unknown source {r['source_id']}")
        elif not value_in(r['value'], src['excerpt']):
            problems.append(f"{r['id']}: value {r['value']} not in excerpt of {r['source_id']}")
    rows = entry['chart']['rows']
    if any(row['record_id'] not in records for row in rows):
        problems.append('chart row uses an unknown record')
    if len({records[row['record_id']]['unit'] for row in rows if row['record_id'] in records} | {entry['chart']['unit']}) != 1:
        problems.append('chart mixes units')
    chart = {'id': CHART_ID, 'kind': 'bars', **entry['chart']}
    result = {k: entry[k] for k in ('company', 'angle', 'category', 'question', 'brief', 'hero_alt')}
    result.update(sources=sources, chart=[chart, entry['records']])
    return result, problems


def leads(audit):
    """Earlier automated research, as short unverified leads."""
    try:
        research = load_json(audit / 'research.json')
    except (OSError, ValueError):
        return 'none'
    items = [f"- {c.get('date') or 'undated'}: {c.get('headline')}" for c in research.get('catalysts', [])[:8]]
    return '\n'.join(items) or 'none'


def study(export, symbol):
    s = next(x for x in export['studies'] if x['identity']['symbol'] == symbol)
    return json.dumps({'identity': s['identity'], 'stats': s['responses'][0]['response']['stats']},
                      ensure_ascii=False, separators=(',', ':'))


def job_dir(root, symbol, date, attempt):
    return Path(root) / 'jobs' / f"{symbol}-{date.replace('-', '')}-research{'' if attempt == 1 else attempt}"


def prepare(root, date, post, export, valid_until, problems=None, attempt=1):
    root, sym = Path(root), post['symbol']
    prod = root / 'production' / sym
    hero = prod / 'assets' / Path(urlparse(post['hero_image']).path).name
    company = load_json(prod / 'engine-payload.json')['meta']['security_name']
    prompt = BRIEF.format(company=company, symbol=sym, date=date,
                          study=study(export, sym), leads=leads(prod / 'audit'))
    if problems:
        prompt += '\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED BY CODE. Fix every problem:\n' + '\n'.join('- ' + p for p in problems)
    job = job_dir(root, sym, date, attempt)
    return prepare_job(job.parent, job.name, prompt, SCHEMA, as_of=date, valid_until=valid_until,
                       evidence_sha256=export.get('captured_at', date), stage='research',
                       effort=EFFORT, model=MODEL, web_search=True, images=[hero] if hero.is_file() else ())


def research(root, date, codex, valid_until, max_attempts=2):
    """Write root/sources.json for all six subjects, or raise with the held problems."""
    root = Path(root)
    target = root / 'sources.json'
    if target.exists():
        return load_json(target)
    posts = load_json(root / 'production' / 'posts.json')
    export = load_json(root / 'production-engine-export.json')
    specs, held = {}, {}
    for post in posts:
        sym, problems = post['symbol'], None
        for attempt in range(1, max_attempts + 1):
            job = job_dir(root, sym, date, attempt)
            if not job.exists():
                prepare(root, date, post, export, valid_until, problems, attempt)
            run_job(job, codex)
            spec, problems = verify(load_json(job / 'output.json'), date)
            save_json(job.parent.parent / 'research' / f'{sym}-attempt{attempt}.json', {'spec': spec, 'problems': problems})
            if not problems:
                specs[sym] = spec
                break
        else:
            held[sym] = problems
    if held:
        raise RuntimeError('research held: ' + json.dumps(held))
    save_json(target, specs)
    return specs
