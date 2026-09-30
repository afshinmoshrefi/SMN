"""Source-grounded review contract and fail-closed completion gate.

No model calls or financial calculations. Cohort checks compare observation
identities from TradeWave; all financial values remain engine-owned.
"""
from datetime import date, datetime
from pathlib import Path
import json
import re

from subscription_writer import load_json, save_json, sha256, validate_schema
from visual_evidence import digest

VERSION = 1
MARKER = 'EDITORIAL_CONTEXT_SHA256: '
CAUSAL = re.compile(r'\b(blam\w*|attribut\w*|due to|driven by|caused by|resulted from)\b', re.I)
FUTURE = re.compile(r'\b(upcoming|next\s+(?:\w+\s+){0,3}(?:tests?|meeting|decision|release|earnings|report|week|month))\b', re.I)
COUNT = re.compile(r'\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:midterm\s+)?years?\s+(?:appear|overlap|are shared|in common)', re.I)
CALENDAR_DATE = re.compile(r'\b(?:20\d{2}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2})\b', re.I)
NUMBERS = {word: n for n, word in enumerate('zero one two three four five six seven eight nine ten'.split())}


def spaced(value):
    return re.sub(r'\s+', ' ', value).strip()


def primary_sources(root, symbol, edition):
    """Read every captured primary page, validating exact per-page and file hashes."""
    folder = Path(root)/'primary'
    path, proof_path = folder/(symbol+'.txt'), folder/(symbol+'.receipt.json')
    proof = load_json(proof_path)
    if path.is_symlink() or proof_path.is_symlink() or proof.get('text_sha256') != sha256(path.read_bytes()):
        raise ValueError('Primary evidence changed or missing; refresh research in a new revision')
    if proof.get('edition_date') != edition or proof.get('symbol') != symbol:
        raise ValueError('Primary evidence belongs to another edition/subject')
    raw = path.read_text(encoding='utf-8')
    docs = {}
    for item in proof['sources']:
        marker = 'TEXT_SHA256: '+item['page_text_sha256']+'\nTEXT:\n'
        if raw.count(marker) != 1:
            raise ValueError('Primary page boundary missing or ambiguous')
        page = raw.split(marker, 1)[1][:item['page_text_chars']]
        if sha256(page.encode()) != item['page_text_sha256'] or item['date'] > edition:
            raise ValueError('Primary page bytes/date differ from captured evidence')
        document = {'text': page, 'date': item['date'], 'url': item['final_url'],
                    'sha256': item['page_text_sha256'],
                    'capture_may_be_truncated': item.get('truncated', len(page) >= 8500)}
        docs[item['url']] = docs[item['final_url']] = document
    if len({v['url'] for v in docs.values()}) < 2:
        raise ValueError('Two independently captured primary pages required')
    return docs


def units(article):
    rows = [{'id': k, 'text': article[k], 'kind': 'fact'} for k in ('title','dek')]
    for i, takeaway in enumerate(article.get('takeaways', [])):
        rows.append({'id': 'takeaways.'+str(i), 'text': takeaway['text'], 'kind': 'fact'})
    for i, section in enumerate(article['sections']):
        if section.get('heading'):
            rows.append({'id': f'sections.{i}.heading', 'text': section['heading'], 'kind': 'fact'})
        for j, p in enumerate(section['paragraphs']):
            rows.append({'id': f'sections.{i}.paragraphs.{j}', 'text': p['text'], 'kind': p['kind']})
    return rows


def requirements(article, card):
    result = []
    samples = {str(c['request']['years']): [r['year'] for r in c['per_year']]
               for c in card['engine_results']['comparisons']}
    for unit in units(article):
        kinds = []
        if CAUSAL.search(unit['text']): kinds.append('causal')
        if FUTURE.search(unit['text']): kinds.append('upcoming')
        match = COUNT.search(unit['text'])
        if match: kinds.append('cohort_overlap')
        if not kinds: continue
        row = {**unit, 'kinds': kinds}
        if match:
            value = match[1].lower()
            row['claimed_count'] = int(value) if value.isdigit() else NUMBERS[value]
            explicit = [key for key in samples if re.search(r'\b'+re.escape(key)+r'[- ]year', unit['text'], re.I)]
            row['samples'] = explicit if explicit and not re.search(r'\bboth\b|\beach\b',unit['text'],re.I) else list(samples)
        result.append(row)
    return result


def context(root, symbol, edition):
    root = Path(root); out = root/'results'/symbol
    article, bundle = load_json(out/'article.json'), load_json(out/'bundle.json')
    native = load_json(out/'seasonal-manifest.json')
    evidence = load_json(out/'writer-evidence.json')
    contract = evidence.get('material_context')
    if not isinstance(contract, list) or not contract:
        raise ValueError('Missing pre-writer material-source contract; refresh research/evidence before review')
    docs = primary_sources(root, symbol, edition)
    sources = {s['id']: s for s in bundle['sources']}
    ids = set()
    for item in contract:
        source = sources.get(item['source_id'], {})
        doc = docs.get(source.get('url'))
        if item['id'] in ids or not doc or len(spaced(item['quote'])) < 12 or spaced(item['quote']) not in spaced(doc['text']):
            raise ValueError('Material-source contract is duplicated or not grounded in captured primary evidence')
        ids.add(item['id'])
    return {'version': VERSION, 'symbol': symbol, 'edition': edition,
            'article_sha256': digest(article), 'evidence_sha256': bundle['evidence_sha256'],
            'inputs': {name: sha256((out/name).read_bytes()) for name in
                       ('bundle.json','writer-evidence.json','source.json','seasonal-manifest.json','chart-manifest.json','hero-asset.json')},
            'primary_sources': docs, 'material_context': contract,
            'requirements': requirements(article,native['card']),
            'primary_years': native['card']['engine_results']['cohort']['years'],
            'comparison_years': {str(c['request']['years']): [r['year'] for r in c['per_year']]
                                 for c in native['card']['engine_results']['comparisons']}}


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def review_schema(schema):
    string = {'type':'string'}
    coverage = object_schema({'item_id':string,'status':{'type':'string','enum':['covered','missing','uncertain']},
                              'article_quote':string,'reason':string})
    cohort = object_schema({'sample':string,'shared_years':{'type':'array','items':{'type':'integer'}}})
    claim = object_schema({'unit_id':string,'status':{'type':'string','enum':['supported','qualified_analysis','unsupported','uncertain']},
                           'source_url':string,'source_quote':string,'event_date':string,
                           'cohorts':{'type':'array','items':cohort},'reason':string})
    schema['properties']['editorial_audit'] = object_schema({
        'coverage': {'type':'array','items':coverage}, 'claims':{'type':'array','items':claim}})
    schema['required'].append('editorial_audit')
    issue = schema['properties']['issues']['items']
    issue['properties']['category'] = {'type':'string','enum':['style','factual','temporal','instrument','coverage','numeric','other']}
    issue['required'].append('category')
    return schema


RULES = '''Mandatory source audit (editorial_audit): raw captured primary pages below outrank prepared summaries.
For EVERY material_context item provide its item_id, covered/missing/uncertain and an exact quote from this
article that communicates the fact/qualification. Required omissions fail even if they are called minor.
Read management explanations for all material causes and counterevidence, not only the selected headline.
Truncated captured pages do not prove that a management explanation is complete; require the relevant primary
account for causal claims or hold for source repair. Secondary attribution alone cannot establish causality.
For EVERY requirements unit provide one claims entry, even if unsupported. For causal claims cite an exact
primary-source quote; qualified_analysis is allowed only for explicitly conditional analysis, not reported causes.
For factual upcoming events supply a dated primary quotation containing the exact future event date. An old
source's upcoming event is not current confirmation. Generic conditional analysis about what a future event
might mean may use qualified_analysis only without a concrete calendar date; it does not confirm a schedule.
Otherwise, if evidence is unavailable, request removal or hold.
For each required cohort sample list ALL shared observed year identities from the supplied lists. Never compute
financial returns/statistics. An overlap assertion covering both samples must be true separately for both.
Use empty strings/lists for unavailable fields, report unsupported/uncertain, and fail the relevant check.
Classify each issue: style alone may be nonblocking; factual, temporal, instrument, coverage and numeric
corrections block regardless of severity. Do not mark a factual correction minor to allow completion.
This audit does not authorize guessing missing facts or declaring unverified claims safe.'''


def problems(article, bundle, ctx, review):
    from seasonal_edition import check_temporal_instrument_copy
    issues = []
    for unit in units(article):
        try: check_temporal_instrument_copy([unit['text']], bundle)
        except ValueError as exc: issues.append(unit['id']+': '+str(exc))
    for item in review.get('issues', []):
        if item.get('severity') in {'major','blocker'} or item.get('category') != 'style' or re.search(r'\b(incorrect|inaccurate|unsupported|misstates|contradicts)\b',item.get('problem',''),re.I):
            issues.append('Unresolved '+item.get('category','unclassified')+' correction: '+item.get('problem',''))
    audit = review.get('editorial_audit') or {}
    coverage = audit.get('coverage', [])
    if len({r.get('item_id') for r in coverage}) != len(coverage) or {r.get('item_id') for r in coverage} != {r['id'] for r in ctx['material_context']}:
        issues.append('Material-source coverage ledger missing or incomplete')
    texts = [spaced(u['text']) for u in units(article)]
    by_id = {r.get('item_id'):r for r in coverage}
    for item in ctx['material_context']:
        row = by_id.get(item['id'], {})
        quote = spaced(row.get('article_quote',''))
        if item['required'] and (row.get('status') != 'covered' or len(quote)<12 or not any(quote in t for t in texts)):
            issues.append('Missing material context '+item['id']+': '+item['summary'])
    claims = audit.get('claims', [])
    if len({r.get('unit_id') for r in claims}) != len(claims) or {r.get('unit_id') for r in claims} != {r['id'] for r in ctx['requirements']}:
        issues.append('Factual/temporal claim ledger missing or incomplete')
    by_unit = {r.get('unit_id'):r for r in claims}
    for req in ctx['requirements']:
        row = by_unit.get(req['id'], {})
        prefix = req['id']+': '
        if row.get('status') not in {'supported','qualified_analysis'}:
            issues.append(prefix+'claim unsupported or uncertain; revise or supply primary evidence')
            continue
        doc = ctx['primary_sources'].get(row.get('source_url'))
        quote = spaced(row.get('source_quote',''))
        conditional = (row.get('status')=='qualified_analysis' and req['kind']=='analysis' and
                       re.search(r'\b(if|may|might|could|would)\b',req['text'],re.I))
        if 'causal' in req['kinds'] and not conditional:
            if not doc or len(quote)<12 or quote not in spaced(doc['text']):
                issues.append(prefix+'causal explanation lacks a direct captured primary quotation')
        if 'upcoming' in req['kinds'] and not (row.get('status')=='qualified_analysis' and
                req['kind']=='analysis' and not CALENDAR_DATE.search(req['text']) and
                re.search(r'\b(if|may|might|could|would|whether)\b',req['text'],re.I)):
            try:
                event = date.fromisoformat(row.get('event_date',''))
                forms = [event.isoformat(), event.strftime('%B %d, %Y').replace(' 0',' ')]
                if doc and event.year == date.fromisoformat(ctx['edition']).year == date.fromisoformat(doc['date']).year:
                    forms.append(event.strftime('%B %d').replace(' 0',' '))
                if event <= date.fromisoformat(ctx['edition']) or not doc or len(quote)<12 or quote not in spaced(doc['text']) or not any(f.lower() in quote.lower() for f in forms):
                    raise ValueError()
            except ValueError:
                issues.append(prefix+'upcoming event has no explicit future date supported by captured primary evidence')
        if 'cohort_overlap' in req['kinds']:
            cohorts = row.get('cohorts', [])
            if len(cohorts)!=len(req['samples']) or {c['sample'] for c in cohorts} != set(req['samples']):
                issues.append(prefix+'each asserted comparison sample needs its own observed-year check')
            for cohort in cohorts:
                # Identity comparison only, never a recomputed financial statistic.
                expected = set(ctx['primary_years']) & set(ctx['comparison_years'].get(cohort['sample'], []))
                years = cohort['shared_years']
                if len(years)!=len(set(years)) or set(years)!=expected or len(years)!=req['claimed_count']:
                    issues.append(prefix+'overlap count is not true for '+cohort['sample']+'; use the supplied year identities')
    return issues


def completed_job(job):
    """Verify completed immutable inputs/output, including the receipt's input binding."""
    job = Path(job); manifest = load_json(job/'job.json'); receipt = load_json(job/'receipt.json')
    if (receipt.get('job_id') != manifest.get('job_id') or manifest.get('job_id') != job.name or
            receipt.get('stage') != manifest.get('stage') or receipt.get('billing_source') != 'subscription' or
            receipt.get('input_hashes') != manifest.get('input_hashes') or receipt.get('evidence_sha256') != manifest.get('evidence_sha256')):
        raise ValueError('Model receipt belongs to different inputs/evidence')
    for name, expected in manifest['input_hashes'].items():
        path = job/name
        if path.is_symlink() or job.resolve() not in path.resolve().parents or sha256(path.read_bytes()) != expected:
            raise ValueError('Model reviewed input changed: '+name)
    if not {'prompt.txt','schema.json'} <= set(manifest['input_hashes']):
        raise ValueError('Model reviewed inputs unavailable')
    if receipt.get('output_sha256') != sha256((job/'output.json').read_bytes()) or receipt.get('api_fallback') is not False:
        raise ValueError('Model output/receipt changed or fallback used')
    if receipt.get('model_requested') != manifest.get('model') or receipt.get('effort_requested') != manifest.get('effort'):
        raise ValueError('Model settings differ from prepared review')
    if datetime.fromisoformat(receipt['finished_utc'].replace('Z','+00:00')) > datetime.fromisoformat(manifest['valid_until'].replace('Z','+00:00')):
        raise ValueError('Model completed after its evidence assignment expired')
    output = load_json(job/'output.json'); validate_schema(output,load_json(job/'schema.json'))
    return output


def verify_review(result, review_path):
    result = Path(result); root = result.parent.parent
    article, bundle = load_json(result/'article.json'), load_json(result/'bundle.json')
    job = Path(review_path).parent
    review = completed_job(job)
    ctx = load_json(job/'editorial-context.json')
    if ctx != context(root,result.name,ctx['edition']):
        raise ValueError('Review article/source/render inputs changed; fresh review required')
    if MARKER+digest(ctx)+'\n' not in (job/'prompt.txt').read_text(encoding='utf-8'):
        raise ValueError('Editorial context is not bound to the immutable reviewer prompt')
    from subscription_publication import CHECKS
    errors = problems(article,bundle,ctx,review)
    if review.get('passed') is not True or set(review.get('checks',{})) != CHECKS or any(r.get('passed') is not True for r in review.get('checks',{}).values()):
        errors.append('Independent reviewer did not pass every required check')
    if errors: raise ValueError('; '.join(errors))
    return {'version':VERSION,'article_sha256':digest(article),'context_sha256':digest(ctx),
            'review_sha256':sha256(Path(review_path).read_bytes()),'passed':True}


def verify_complete(result):
    result = Path(result); root = result.parent.parent
    binding = load_json(result/'review-binding.json')
    stage = binding.get('review_stage','')
    if not re.fullmatch('[a-z-]+',stage): raise ValueError('Final review stage missing')
    edition = load_json(root/'smn-daily-state.json')['date'] if (root/'smn-daily-state.json').exists() else load_json(root/'daily-state.json')['date']
    path = root/'jobs'/(result.name+'-'+edition.replace('-','')+'-'+stage)/'output.json'
    proof = verify_review(result,path)
    if binding.get('editorial_audit') != proof or binding.get('article_html_sha256') != sha256((result/'article.html').read_bytes()):
        raise ValueError('Final editorial binding or rendered content changed')
    m = load_json(result/'mechanical-checks.json')
    if m.get('passed') is not True or m.get('article_sha256') != proof['article_sha256'] or m.get('evidence_sha256') != load_json(result/'bundle.json')['evidence_sha256']:
        raise ValueError('Mechanical approval missing or stale')
    from engine_seasonal import verify_assets
    verify_assets(load_json(result/'seasonal-manifest.json'),result)
    hero=load_json(result/'hero-asset.json')
    if sha256((result/hero['url']).read_bytes())!=hero['sha256']:
        raise ValueError('Reviewed hero asset changed')
    from smn_visual import verify_article_visual
    verify_article_visual(root,result.name)
    return proof
