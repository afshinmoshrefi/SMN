"""Discover and capture fresh official pages for a daily SMN research brief.

Discovery is a bounded subscription model job with web access. This module makes
no model or search API calls itself; it validates and captures the returned URLs.
"""
from datetime import date as Date, datetime, timedelta, timezone
from html.parser import HTMLParser
from http.client import HTTPSConnection
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler
import ipaddress
import json
import re
import socket
import os

import smn_models
from subscription_writer import load_json, sha256

MAX_SOURCES = 4
MAX_PAGE_BYTES = 2_000_000
MAX_TEXT_CHARS = 8_500  # Four complete source sections fit smn_research.saved_text's 40k cap.
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['sources'], 'properties': {'sources': {
              'type': 'array', 'minItems': 2, 'maxItems': MAX_SOURCES,
              'items': {'type': 'object', 'additionalProperties': False,
                        'required': ['title', 'url', 'date', 'publisher', 'reason'],
                        'properties': {k: {'type': 'string'} for k in
                                       ('title', 'url', 'date', 'publisher', 'reason')}}}}}


class Held(RuntimeError):
    """Primary evidence is missing or unsafe; research must stop."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _PublicHTTPSConnection(HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            raise Held('primary fetch cannot use a proxy tunnel')
        addresses = {item[4][0] for item in socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise Held('primary connection resolves to a non-public address')
        # Connect to the checked address, retaining the hostname for TLS validation.
        self.sock = socket.create_connection((sorted(addresses)[0], 443), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'time' and attributes.get('datetime'):
            self.parts.append(' ' + attributes['datetime'] + ' ')
        if tag == 'meta' and attributes.get('content') and (
                attributes.get('property', '').lower() in ('article:published_time', 'datepublished') or
                attributes.get('name', '').lower() in ('date', 'pubdate', 'datepublished')):
            self.parts.append(' ' + attributes['content'] + ' ')
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        elif tag in ('p', 'h1', 'h2', 'h3', 'li', 'tr', 'article', 'section'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg') and self.skip:
            self.skip -= 1
        elif tag in ('p', 'h1', 'h2', 'h3', 'li', 'tr', 'article', 'section'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _safe_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise Held('invalid primary URL')
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError as exc:
        raise Held('invalid primary URL port') from exc
    if p.scheme != 'https' or not p.hostname or p.username or p.password or port is not None or p.fragment:
        raise Held('primary URL must be public HTTPS without credentials, port or fragment')
    host = p.hostname.rstrip('.').lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise Held('primary URL must name an official public domain')
    if '.' not in host or host.endswith(('.local', '.internal', '.localhost', '.test')):
        raise Held('primary URL host is not public')
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise Held('primary URL host could not be resolved') from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise Held('primary URL resolves to a non-public address')
    return url


def fetch_page(url):
    """Fetch bounded public HTML, checking every redirect target."""
    opener = build_opener(ProxyHandler({}), _NoRedirect(), _PublicHTTPSHandler())
    current = url
    for _ in range(4):
        _safe_url(current)
        req = Request(current, headers={'User-Agent': 'Mozilla/5.0 (compatible; SMNResearch/1.0; +https://seasonalmarketnews.com)',
                                        'Accept': 'text/html,application/xhtml+xml'})
        try:
            response = opener.open(req, timeout=15)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308) or not exc.headers.get('Location'):
                raise Held('primary page fetch failed: HTTP %s' % exc.code) from exc
            current = urljoin(current, exc.headers['Location'])
            continue
        except (URLError, TimeoutError, OSError) as exc:
            raise Held('primary page network fetch failed: ' + type(exc).__name__) from exc
        with response:
            kind = response.headers.get_content_type()
            if kind not in ('text/html', 'application/xhtml+xml'):
                raise Held('primary page is not HTML')
            raw = response.read(MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES:
                raise Held('primary page exceeds byte limit')
            charset = response.headers.get_content_charset() or 'utf-8'
        parser = _Text()
        parser.feed(raw.decode(charset, errors='replace'))
        page = re.sub(r'[ \t]+', ' ', ''.join(parser.parts))
        page = re.sub(r'\n\s*\n+', '\n', page).strip()[:MAX_TEXT_CHARS]
        if len(page) < 400:
            raise Held('primary page has insufficient readable text')
        return current, page
    raise Held('primary page redirected too many times')


def _validate_sources(sources, edition):
    if not isinstance(sources, list) or not 2 <= len(sources) <= MAX_SOURCES:
        raise Held('primary discovery must return 2-4 pages')
    seen = set()
    cutoff = Date.fromisoformat(edition) - timedelta(days=120)
    for row in sources:
        if not isinstance(row, dict) or set(row) != {'title', 'url', 'date', 'publisher', 'reason'}:
            raise Held('primary discovery has an incomplete source')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in row):
            raise Held('primary discovery has a blank field')
        try:
            published = Date.fromisoformat(row['date'])
        except ValueError as exc:
            raise Held('primary discovery has an invalid published date') from exc
        if not cutoff <= published <= Date.fromisoformat(edition):
            raise Held('primary source is stale or dated after the edition')
        url = row['url']
        if url in seen:
            raise Held('primary discovery returned duplicate URLs')
        seen.add(url)


def _existing(primary, receipt, edition, sym):
    if not primary.exists() and not receipt.exists():
        return False
    if primary.is_symlink() or receipt.is_symlink() or not primary.exists() or not receipt.exists():
        raise Held('incomplete or unsafe primary evidence for ' + sym)
    proof = load_json(receipt)
    if (proof.get('edition_date') != edition or proof.get('symbol') != sym or
            proof.get('text_sha256') != sha256(primary.read_bytes()) or
            not 2 <= len(proof.get('sources', [])) <= MAX_SOURCES):
        raise Held('primary evidence receipt mismatch for ' + sym)
    return True


def _save_cache(path, cache):
    if path.is_symlink():
        raise Held('unsafe primary fetch cache')
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)
    os.replace(temporary, path)


def _capture_rows(rows, cache, path):
    for row in rows:
        url = row['url']
        if url in cache['pages'] or url in cache['failed_urls']:
            continue
        if len(cache['pages']) >= 2:
            break
        try:
            final_url, page = fetch_page(url)
            published = Date.fromisoformat(row['date'])
            date_clues = (row['date'], published.strftime('%B %d, %Y').replace(' 0', ' '),
                          published.strftime('%b %d, %Y').replace(' 0', ' '),
                          published.strftime('%B %d %Y').replace(' 0', ' '),
                          published.strftime('%b. %d, %Y').replace(' 0', ' '),
                          published.strftime('%d %B %Y').lstrip('0'),
                          published.strftime('%m/%d/%Y'), published.strftime('%m/%d/%y'),
                          published.strftime('%Y/%m/%d'))
            if not any(clue.lower() in page.lower() for clue in date_clues):
                raise Held('published date not visible in fetched page')
            title_words = {word.lower() for word in re.findall(r'[A-Za-z]{5,}', row['title'])}
            if title_words and not title_words.intersection(re.findall(r'[a-z]{5,}', page.lower())):
                raise Held('source title not supported by fetched page')
            if any(item['final_url'] == final_url for item in cache['pages'].values()):
                raise Held('primary URLs resolve to the same page')
            cache['pages'][url] = {'source': row, 'final_url': final_url, 'page': page,
                                   'page_text_sha256': sha256(page.encode()),
                                   'fetched_utc': datetime.now(timezone.utc).isoformat()}
        except Held as exc:
            cache['failed_urls'][url] = str(exc)[:300]
        _save_cache(path, cache)


def _discover(job, prompt, root, edition, roles, run_job):
    if not job.exists():
        smn_models.prepare(roles, 'research', root/'jobs', job.name, prompt, SCHEMA,
                           as_of=edition, valid_until=(datetime.now(timezone.utc) +
                           timedelta(hours=20)).isoformat(), evidence_sha256=sha256(prompt.encode()),
                           stage='primary-discovery', web_search=True)
    run_job(job)
    sources = load_json(job/'output.json')['sources']
    _validate_sources(sources, edition)
    return sources


def collect(root, edition, symbols, roles, clis, run_job):
    """Capture fresh primary pages; run_job is the controller's budgeted runner.

    Writes primary/SYM.txt and primary/SYM.receipt.json once per subject. A
    validated existing pair is reused without another model job or HTTP fetch.
    """
    root = Path(root)
    Date.fromisoformat(edition)
    posts = {p['symbol']: p for p in load_json(root/'production/posts.json')}
    primary_dir = root/'primary'
    primary_dir.mkdir(exist_ok=True)
    for sym in symbols:
        if sym not in posts or not re.fullmatch(r'[A-Z0-9]{1,12}', sym):
            raise Held('unknown primary subject')
        target = primary_dir/(sym + '.txt')
        receipt = primary_dir/(sym + '.receipt.json')
        if _existing(target, receipt, edition, sym):
            continue
        cache_path = primary_dir/(sym + '.fetch-cache.json')
        if cache_path.exists():
            if cache_path.is_symlink():
                raise Held('unsafe primary fetch cache')
            cache = load_json(cache_path)
            if cache.get('edition_date') != edition or cache.get('symbol') != sym:
                raise Held('primary fetch cache belongs to another edition')
            try:
                if any(item['source']['url'] != url or
                       item['page_text_sha256'] != sha256(item['page'].encode())
                       for url, item in cache['pages'].items()):
                    raise Held('primary fetch cache content changed')
            except (KeyError, TypeError, AttributeError) as exc:
                raise Held('primary fetch cache is malformed') from exc
        else:
            cache = {'edition_date': edition, 'symbol': sym, 'pages': {}, 'failed_urls': {}, 'jobs': []}
        job = root/'jobs'/(sym + '-' + edition.replace('-', '') + '-primary-discovery')
        post = posts[sym]
        leads_path = root/'production'/sym/'audit/research_context.txt'
        leads = leads_path.read_text(encoding='utf-8', errors='replace')[:7000] if leads_path.exists() else ''
        prompt = ('Find 2-4 current, dated, official primary-source HTML pages for a Seasonal Market News '
                  'article dated ' + edition + ' about ' + sym + '. Use web search. Search beyond the saved '
                  'production news leads; they are leads only and may be stale or incomplete. Choose company '
                  'investor releases, filings, regulator or government data, and other first-party evidence. '
                  'Each page must concern this subject, contain useful reported business facts, be published '
                  'within 120 days through the edition date, and have a public HTTPS URL. Exclude news '
                  'aggregators, analyst summaries, homepages, undated pages and PDFs. Return exact source '
                  'titles, URLs, publication dates, publishers, and why each page is relevant. Never invent '
                  'a URL or publication date.\nSUBJECT: ' + json.dumps({k: post.get(k) for k in
                  ('symbol', 'title', 'dek', 'published_date')}) + '\nPRODUCTION LEADS:\n' + leads)
        sources = _discover(job, prompt, root, edition, roles, run_job)
        if job.name not in cache['jobs']:
            cache['jobs'].append(job.name)
            _save_cache(cache_path, cache)
        _capture_rows(sources, cache, cache_path)
        if len(cache['pages']) < 2:
            retry_job = root/'jobs'/(sym + '-' + edition.replace('-', '') + '-primary-discovery-two')
            retry_prompt = (prompt + '\nRETRY: The first discovery did not yield two accessible primary pages. '
                            'Find different official HTML pages. Do not return any of these already tried URLs: ' +
                            json.dumps(sorted(set(cache['pages']) | set(cache['failed_urls']))) +
                            '\nFetch failures: ' + json.dumps(cache['failed_urls']))
            retry_sources = _discover(retry_job, retry_prompt, root, edition, roles, run_job)
            if retry_job.name not in cache['jobs']:
                cache['jobs'].append(retry_job.name)
                _save_cache(cache_path, cache)
            _capture_rows(retry_sources, cache, cache_path)
        if len(cache['pages']) < 2:
            raise Held('fewer than two accessible primary pages for %s after one discovery retry: %s' %
                       (sym, json.dumps(cache['failed_urls'], sort_keys=True)))
        fetched = datetime.now(timezone.utc).isoformat()
        sections, records = [], []
        for item in cache['pages'].values():
            row, final_url, page = item['source'], item['final_url'], item['page']
            section = ('URL: ' + final_url + '\nDISCOVERED_URL: ' + row['url'] +
                       '\nTITLE: ' + row['title'] + '\nPUBLISHER: ' +
                       row['publisher'] + '\nDATE: ' + row['date'] + '\nFETCHED_UTC: ' +
                       item['fetched_utc'] +
                       '\nTEXT_SHA256: ' + sha256(page.encode()) + '\nTEXT:\n' + page + '\n')
            sections.append(section)
            records.append({**row, 'final_url': final_url, 'page_text_sha256': sha256(page.encode()),
                            'page_text_chars': len(page)})
        blob = ('\n\n'.join(sections) + '\n').encode()
        proof = {'edition_date': edition, 'symbol': sym, 'fetched_utc': fetched,
                 'discovery_jobs': cache['jobs'], 'text_sha256': sha256(blob), 'sources': records,
                 'failed_urls': cache['failed_urls']}
        # A retry after either exclusive write holds so evidence cannot be replaced.
        with target.open('xb') as f:
            f.write(blob)
        with receipt.open('x', encoding='utf-8') as f:
            json.dump(proof, f, indent=2)
    return {'status': 'captured', 'symbols': list(symbols)}
