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
from subscription_writer import load_json, save_json, sha256

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
    im = Image.open(image)
    if im.height <= height:
        return [image]
    folder.mkdir(exist_ok=True)
    pieces = []
    for n, top in enumerate(range(0, im.height, height)):
        piece = folder/(image.stem + '-part%02d.png' % n)
        im.crop((0, top, im.width, min(im.height, top + height))).save(piece)
        pieces.append(piece)
    return pieces


def _job(root, date, name, stage, prompt, schema, images, roles, clis, role):
    job = Path(root)/'jobs'/(name + '-' + date.replace('-', '') + '-' + stage)
    if not job.exists():
        until = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
        digest = sha256((prompt + ''.join(_sha(i) for i in images)).encode())
        smn_models.prepare(roles, role, Path(root)/'jobs', job.name, prompt, schema, as_of=date,
                           valid_until=until, evidence_sha256=digest, stage=stage, images=images)
    receipt = smn_models.run(job, clis)
    return load_json(job/'output.json'), receipt, job


def article(root, date, sym, roles, clis):
    out = Path(root)/'results'/sym
    layout = load_json(out/'layout-checks.json')
    images = sorted(out.glob('qa-*.png'))
    if not images:
        raise ValueError('No layout screenshots for ' + sym)
    html_sha = _sha(out/'article.html')
    seen = [t for i in images for t in _tiles(i, out/'vision-tiles')]
    names = ', '.join(i.name for i in seen)
    answer, receipt, job = _job(root, date, sym, 'visual', PAGE_RULES.format(names=names), PAGE_SCHEMA,
                                seen, roles, clis, 'visual')
    major = [d for d in answer['defects'] if d['severity'] == 'major']
    passed = bool(answer['passed']) and not major and layout.get('layout_passed', True) is not False
    record = {'passed': passed, 'article_html_sha256': html_sha,
              'inspected_images': {i.name: _sha(i) for i in images},
              'inspector': {'model': receipt['model_requested'], 'effort': receipt['effort_requested'],
                            'job_id': receipt['job_id'], 'output_sha256': receipt['output_sha256']},
              'defects': answer['defects']}
    if passed:
        save_json(out/'visual-checks.json', record)
    else:
        save_json(out/'visual-checks-failed.json', record)
    return record


def hero(root, date, sym, roles, clis):
    """Report-only hero text check. Never blocks publication (owner: regenerate later)."""
    out = Path(root)/'results'/sym
    asset = load_json(out/'hero-asset.json')
    image = Path(asset['path']) if Path(asset.get('path', '')).is_file() else out/asset['url']
    company = load_json(Path(root)/'sources.json')[sym]['company']
    try:
        answer, receipt, _ = _job(root, date, sym, 'hero-check', HERO_RULES.format(company=company, symbol=sym),
                                  HERO_SCHEMA, [image], roles, clis, 'hero_check')
        record = {'report_only': True, 'image_sha256': _sha(image), **answer,
                  'model': receipt['model_requested']}
    except Exception as exc:
        record = {'report_only': True, 'image_sha256': _sha(image), 'error': str(exc)[:300]}
    save_json(out/'hero-check.json', record)
    return record


def landing(root, date, roles, clis):
    root = Path(root)
    images = [root/'live-edition-desktop.png', root/'live-edition-mobile.png']
    seen = [t for i in images for t in _tiles(i, root/'vision-tiles')]
    answer, receipt, _ = _job(root, date, 'EDITION', 'landing-visual',
                              LANDING_RULES.format(names=', '.join(i.name for i in seen),
                              count=len(load_json(root/'publication-package/entries.json'))), PAGE_SCHEMA,
                              seen, roles, clis, 'visual')
    major = [d for d in answer['defects'] if d['severity'] == 'major']
    record = {'passed': bool(answer['passed']) and not major,
              'inspected_images': {i.name: _sha(i) for i in images},
              'inspector': {'model': receipt['model_requested'], 'job_id': receipt['job_id']},
              'defects': answer['defects']}
    save_json(root/('live-landing-visual-checks.json' if record['passed'] else 'live-landing-visual-failed.json'), record)
    return record
