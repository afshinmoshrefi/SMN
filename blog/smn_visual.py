"""Cheap vision checks: article screenshots, hero image text, live landing pages.

Each check is one tool-less job with the images sent inline. Code writes the
record the publisher requires (visual-checks.json / live-landing-visual-checks.json)
only from a passing answer, with the exact SHA-256 of every image the model saw.
The hero check is report-only for now (hero-check.json); it never blocks.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib

import smn_models
from subscription_writer import load_json, save_json, sha256, validate_schema

PAGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['passed', 'defects'],
    'properties': {'passed': {'type': 'boolean'}, 'defects': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['image', 'severity', 'problem'],
        'properties': {'image': {'type': 'string'}, 'problem': {'type': 'string'},
                       'severity': {'type': 'string', 'enum': ['minor', 'major']}}}}}}
HERO_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['visible_text', 'misspelled_or_garbled', 'passed'],
    'properties': {'visible_text': {'type': 'array', 'items': {'type': 'string'}},
                   'misspelled_or_garbled': {'type': 'array', 'items': {'type': 'string'}},
                   'passed': {'type': 'boolean'}}}

PAGE_RULES = ('You check screenshots of one news article page (desktop 1440px and mobile 390px). The images are '
    'in this order: {names}. Report only real presentation defects: text cut off or overlapping, a chart or '
    'image missing or broken, labels colliding, content running off the screen, empty areas where content '
    'should be, unreadable text. Do not judge writing quality or financial content. A major defect is one a '
    'reader would notice. passed is true only when there is no major defect.')
LANDING_RULES = ('You check screenshots of a news site edition landing page (desktop and mobile): {names}. '
    'It should show a header and {count} article cards, each with an image, a symbol label, a headline and a '
    'summary. Report broken or missing images, overlapping or cut-off text, missing cards, or layout that '
    'runs off the screen. passed is true only when there is no major defect.')
HERO_RULES = ('This is the hero illustration for a financial news article about {company} ({symbol}). List all '
    'visible text in the image exactly as drawn (letters, words, logos, numbers). List any word that is '
    'misspelled, garbled or looks like fake lettering, and any company name that is wrong. passed is true '
    'when there is no visible text, or all visible text is correctly spelled and appropriate.')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tiles(image, folder, height=2000):
    """Cut a tall page capture into readable pieces (the model limit is about 8000px)."""
    from PIL import Image
    with Image.open(image) as im:
        if im.height <= height:
            return [image]
        folder.mkdir(exist_ok=True)
        pieces = []
        for n, top in enumerate(range(0, im.height, height)):
            piece = folder/(image.stem + '-part%02d.png' % n)
            im.crop((0, top, im.width, min(im.height, top + height))).save(piece)
            pieces.append(piece)
    return pieces


def _verify_inputs(job, prompt, schema, images):
    """Compare an immutable vision assignment with the surfaces checked now."""
    job=Path(job); manifest=load_json(job/'job.json')
    expected={'prompt.txt':_sha(job/'prompt.txt'),'schema.json':_sha(job/'schema.json')}
    if (job/'prompt.txt').read_text(encoding='utf-8')!=prompt or load_json(job/'schema.json')!=schema:
        raise ValueError('Visual assignment changed; preserve old jobs and prepare a fresh visual stage')
    for index,image in enumerate(images):
        image=Path(image); expected['images/'+str(index)+'-'+image.name]=_sha(image)
    if (not images or manifest.get('input_hashes')!=expected or
            manifest.get('evidence_sha256')!=sha256((prompt+''.join(_sha(i) for i in images)).encode())):
        raise ValueError('Visual inspected images changed; preserve old jobs and prepare a fresh visual stage')
    for name,digest in expected.items():
        path=job/name
        if path.is_symlink() or _sha(path)!=digest:
            raise ValueError('Immutable visual job input changed: '+name)
    return manifest


def _verify_answer(job, manifest):
    receipt=load_json(job/'receipt.json');answer=load_json(job/'output.json')
    if (receipt.get('job_id')!=manifest.get('job_id') or receipt.get('stage')!=manifest.get('stage') or
            receipt.get('model_requested')!=manifest.get('model') or
            receipt.get('effort_requested')!=manifest.get('effort') or
            receipt.get('input_hashes')!=manifest.get('input_hashes') or
            receipt.get('evidence_sha256')!=manifest.get('evidence_sha256') or
            receipt.get('output_sha256')!=_sha(job/'output.json') or
            receipt.get('api_fallback') is not False or receipt.get('billing_source')!='subscription'):
        raise ValueError('Visual output differs from its immutable subscription receipt')
    validate_schema(answer,load_json(job/'schema.json'))
    return answer,receipt


def _layout(out):
    layout=load_json(out/'layout-checks.json')
    if (layout.get('passed') is not True or
            layout.get('article_html_sha256')!=_sha(out/'article.html')):
        raise ValueError('Layout capture is missing, failed or stale; recapture the current article before visual review')
    return layout


def _job(root, date, name, stage, prompt, schema, images, roles, clis, role, run_job=None):
    job = Path(root)/'jobs'/(name + '-' + date.replace('-', '') + '-' + stage)
    if not job.exists():
        until = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
        digest = sha256((prompt + ''.join(_sha(i) for i in images)).encode())
        smn_models.prepare(roles, role, Path(root)/'jobs', job.name, prompt, schema, as_of=date,
                           valid_until=until, evidence_sha256=digest, stage=stage, images=images)
    manifest=_verify_inputs(job,prompt,schema,images)
    if not (job/'receipt.json').exists():
        if run_job is not None:run_job(job)
        else:smn_models.run(job,clis)
    answer,receipt=_verify_answer(job,manifest)
    return answer, receipt, job


def article(root, date, sym, roles, clis, run_job=None):
    out = Path(root)/'results'/sym
    layout = _layout(out)
    images = sorted(out.glob('qa-*.png'))
    if not images:
        raise ValueError('No layout screenshots for ' + sym)
    html_sha = _sha(out/'article.html')
    seen = [t for i in images for t in _tiles(i, out/'vision-tiles')]
    names = ', '.join(i.name for i in seen)
    answer, receipt, job = _job(root, date, sym, 'visual', PAGE_RULES.format(names=names), PAGE_SCHEMA,
                                seen, roles, clis, 'visual', run_job)
    major = [d for d in answer['defects'] if d['severity'] == 'major']
    passed = answer['passed'] is True and not major
    record = {'passed': passed, 'article_html_sha256': html_sha,
              'inspected_images': {i.name: _sha(i) for i in images},
              'inspected_job_images': {i.relative_to(out).as_posix():_sha(i) for i in seen},
              'inspector': {'model': receipt['model_requested'], 'effort': receipt['effort_requested'],
                            'job_id': receipt['job_id'], 'output_sha256': receipt['output_sha256']},
              'defects': answer['defects']}
    if passed:
        save_json(out/'visual-checks.json', record)
    else:
        save_json(out/'visual-checks-failed.json', record)
    return record


def verify_article_visual(root, sym):
    """Fail closed on stale visual approvals, including cached completed jobs."""
    root=Path(root);out=root/'results'/sym;_layout(out)
    record=load_json(out/'visual-checks.json');images=sorted(out.glob('qa-*.png'))
    expected={i.name:_sha(i) for i in images}
    if (record.get('passed') is not True or record.get('article_html_sha256')!=_sha(out/'article.html') or
            not expected or record.get('inspected_images')!=expected or
            any(d.get('severity') in ('major','blocker') for d in record.get('defects',[]))):
        raise ValueError('Article visual approval is missing, failed or stale')
    inspector=record.get('inspector') or {};job_id=inspector.get('job_id','')
    if not job_id or Path(job_id).name!=job_id or not job_id.startswith(sym+'-'):
        raise ValueError('Article visual inspector job binding missing')
    job=root/'jobs'/job_id;manifest=load_json(job/'job.json')
    if manifest.get('stage')!='visual' or manifest.get('job_id')!=job_id:
        raise ValueError('Article visual inspector uses a different job')
    seen=[]
    for name in manifest.get('input_hashes',{}):
        if not name.startswith('images/'):continue
        filename=Path(name).name.split('-',1)[-1]
        source=out/filename if (out/filename).is_file() else out/'vision-tiles'/filename
        if source.is_symlink() or not source.is_file():
            raise ValueError('Inspected visual image is missing')
        seen.append(source)
    seen.sort(key=lambda p:next(int(Path(n).name.split('-',1)[0]) for n in manifest['input_hashes']
                              if n.startswith('images/') and Path(n).name.split('-',1)[-1]==p.name))
    actual_seen={p.relative_to(out).as_posix():_sha(p) for p in seen}
    if record.get('inspected_job_images',actual_seen)!=actual_seen:
        raise ValueError('Article visual tile binding changed')
    prompt=PAGE_RULES.format(names=', '.join(i.name for i in seen))
    _verify_inputs(job,prompt,PAGE_SCHEMA,seen);answer,receipt=_verify_answer(job,manifest)
    if (answer.get('passed') is not True or answer.get('defects')!=record.get('defects') or
            any(d.get('severity') in ('major','blocker') for d in answer.get('defects',[])) or
            inspector.get('output_sha256')!=receipt['output_sha256'] or
            inspector.get('model')!=receipt['model_requested'] or
            inspector.get('effort')!=receipt['effort_requested']):
        raise ValueError('Article visual approval differs from the inspected answer')
    return record


def hero(root, date, sym, roles, clis, run_job=None):
    """Report-only hero text check. Never blocks publication (owner: regenerate later)."""
    out = Path(root)/'results'/sym
    asset = load_json(out/'hero-asset.json')
    image = Path(asset['path']) if Path(asset.get('path', '')).is_file() else out/asset['url']
    company = load_json(Path(root)/'sources.json')[sym]['company']
    try:
        answer, receipt, _ = _job(root, date, sym, 'hero-check', HERO_RULES.format(company=company, symbol=sym),
                                  HERO_SCHEMA, [image], roles, clis, 'hero_check', run_job)
        record = {'report_only': True, 'image_sha256': _sha(image), **answer,
                  'model': receipt['model_requested']}
    except Exception as exc:
        if exc.__class__.__name__ == 'Hold':
            raise
        record = {'report_only': True, 'image_sha256': _sha(image), 'error': str(exc)[:300]}
    save_json(out/'hero-check.json', record)
    return record


def landing(root, date, roles, clis, run_job=None):
    root = Path(root)
    images = [root/'live-edition-desktop.png', root/'live-edition-mobile.png']
    seen = [t for i in images for t in _tiles(i, root/'vision-tiles')]
    answer, receipt, _ = _job(root, date, 'EDITION', 'landing-visual',
                              LANDING_RULES.format(names=', '.join(i.name for i in seen),
                              count=len(load_json(root/'publication-package/entries.json'))), PAGE_SCHEMA,
                              seen, roles, clis, 'visual', run_job)
    major = [d for d in answer['defects'] if d['severity'] == 'major']
    record = {'passed': bool(answer['passed']) and not major,
              'inspected_images': {i.name: _sha(i) for i in images},
              'inspector': {'model': receipt['model_requested'], 'job_id': receipt['job_id']},
              'defects': answer['defects']}
    save_json(root/('live-landing-visual-checks.json' if record['passed'] else 'live-landing-visual-failed.json'), record)
    return record
