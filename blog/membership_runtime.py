"""Server-owned bindings for the membership dashboard and private job queue."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import re

import article_index
import pin_store
import membership_publication as publication
from article_content_store import ContentError
from visual_evidence import digest


def _post(slug):
    post = next((p for p in article_index.load_posts() if pin_store.article_slug(p) == slug), None)
    if post is None:
        raise ContentError('Published article was not found')
    return post


def _draft_file(slug):
    return publication.store().root / 'preview-drafts' / (publication.sha(slug.encode()) + '.json')


def list_articles():
    return [{'slug': pin_store.article_slug(p), 'title': p['title'], 'symbol': p.get('symbol'),
             'revision': p.get('membership_revision'), 'url': p.get('url')}
            for p in article_index.load_posts()]


def get_preview(slug):
    post = _post(slug)
    raw, manifest = publication.source(post)
    value = {'slug': slug, 'canonical': manifest['canonical_path'], 'revision': manifest['revision'],
        'active_revision': manifest['revision'], 'preview': manifest['preview'], 'review_status': 'approved',
        'full_html': raw}
    path = _draft_file(slug)
    if path.is_file():
        draft = json.loads(path.read_text('utf-8'))
        if draft['base_revision'] == manifest['revision']:
            value.update(revision=draft['revision'], preview=draft['preview'], review_status=draft['review_status'])
    value['payload_sha256'] = digest(value['preview'])
    return value


def save_preview(slug, body, actor):
    with article_index.posts_lock():
        publication.recover()
        previous = get_preview(slug)
        if body.get('expected_revision') != previous['revision']:
            raise ContentError('Preview changed. Reload before saving.')
        content = deepcopy(body.get('content'))
        if not isinstance(content, dict):
            raise ContentError('Public copy is required')
        statements = [content.get('headline'), *(content.get('preview') or []),
                      content.get('full_article_value'), content.get('qualification')]
        if (not 1 <= len(content.get('preview', [])) <= 3
                or any(not isinstance(s, dict) or not isinstance(s.get('text'), str)
                       or not s['text'].strip() or len(s['text']) > 3000
                       or '<' in s['text'] or '>' in s['text'] for s in statements)):
            raise ContentError('Use plain text and one to three preview paragraphs, with a qualification.')
        # References and non-form fields are always taken from the saved source.
        original = previous['preview']['content']
        retained = deepcopy(original)
        for key in ('headline', 'full_article_value', 'qualification'):
            retained[key]['text'] = content[key]['text'].strip()
        retained['preview'] = [dict(deepcopy(original['preview'][min(i, len(original['preview']) - 1)]),
                                    text=item['text'].strip()) for i, item in enumerate(content['preview'])]
        content = retained
        preview = deepcopy(previous['preview'])
        preview['content'] = content
        revision = 'draft-' + digest({'base': previous['active_revision'], 'content': content})[:32]
        preview['provenance'].update(revision=revision, mode='summary')
        draft = {'base_revision': previous['active_revision'], 'revision': revision, 'preview': preview,
                 'review_status': 'pending', 'actor': actor, 'updated_at': publication.now()}
        publication._write(_draft_file(slug), draft)
        return get_preview(slug)


def review_preview(slug, body, actor):
    with article_index.posts_lock():
        publication.recover()
        current = get_preview(slug)
        data = body.get('data') or body
        if (body.get('expected_revision') != current['revision']
                or data.get('payload_sha256') != current['payload_sha256']):
            raise ContentError('The reviewed preview changed. Reload and review again.')
        draft_file = _draft_file(slug)
        if not draft_file.is_file():
            raise ContentError('There is no draft preview to review')
        draft = json.loads(draft_file.read_text('utf-8'))
        if data.get('passed') is False:
            draft.update(review_status='rejected', reviewed_by=actor, reviewed_at=publication.now())
            publication._write(draft_file, draft)
            return get_preview(slug)
        if data.get('passed') is not True:
            raise ContentError('An explicit review decision is required')
        post = _post(slug)
        raw, manifest = publication.source(post)
        updated, prepared = publication.prepare(post, raw, preview=draft['preview'],
                                                reviewer=actor, previous=manifest)
        publication.commit_post(article_index.load_posts(), post, updated, prepared)
        qualified = publication.store().root / 'qualified-sources' / (publication.sha(slug.encode()) + '.json')
        if qualified.is_file():
            record = json.loads(qualified.read_text('utf-8'))
            if record.get('active_revision') == manifest['revision']:
                record['active_revision'] = prepared['revision']
                if record.get('copy') != draft['preview']['content']:
                    record.pop('copy', None)
                    record.pop('source_review', None)
                publication._write(qualified, record)
        draft_file.unlink()
        return get_preview(slug)


def jobs_root():
    return publication.store().root / 'promotion'


def _source_record(slug):
    path = publication.store().root / 'qualified-sources' / (publication.sha(slug.encode()) + '.json')
    if not path.is_file():
        raise ContentError('This article needs its original approved study and source bundle before automated promotion.')
    record = json.loads(path.read_text('utf-8'))
    post = _post(slug)
    manifest = publication.store().resolve(publication.path_for(post))
    if record['active_revision'] != manifest['revision']:
        raise ContentError('Article changed. Requalify its derivative before generating new promotion.')
    return record


def generate_preview(slug, body, actor):
    if body.get('expected_revision') != get_preview(slug)['revision']:
        raise ContentError('Preview changed. Reload before generating.')
    return create_job({'kind': 'derivative', 'slug': slug}, actor)


def list_jobs():
    import promotion_jobs
    return [get_job(job['id']) for job in promotion_jobs.list_jobs(jobs_root())]


def get_job(identifier):
    import promotion_jobs
    job = promotion_jobs.get_job(jobs_root(), identifier)
    from subscription_writer import load_json
    full = load_json(promotion_jobs._path(jobs_root(), identifier))
    job['payload_sha256'] = digest(full['inputs'])
    job['script'] = full['inputs'].get('script', '')
    job['imported_draft'] = full.get('imported_draft')
    return job


def create_job(body, actor):
    import promotion_jobs
    kind = body.get('kind')
    if kind in {'daily_briefing', 'daily_avatar'}:
        identifier = str(body.get('briefing_id') or '')
        source = _briefing_record(identifier)
        briefing = source.get('briefing', source.get('source_bundle'))
        if kind == 'daily_avatar' and 'briefing' not in source:
            raise ContentError('Write and approve the daily briefing before making its avatar.')
        inputs = {'briefing_id': identifier, 'source_revision': digest(briefing),
                  'source_hash': digest(briefing), 'payload_sha256': digest(briefing)}
    else:
        slug = str(body.get('slug') or '')
        record = _source_record(slug)
        prepared = record['prepared']
        provenance = prepared['provenance']
        inputs = {'article_id': provenance['article_id'], 'source_revision': provenance['revision'],
            'source_hash': provenance['article_sha256'], 'payload_sha256': digest(prepared)}
        if kind != 'derivative':
            copy = record.get('copy')
            if not copy:
                raise ContentError('Generate and approve this article\'s public copy first.')
            inputs['payload_sha256'] = digest(copy)
            if kind == 'article_video':
                if not copy.get('video'):
                    raise ContentError('This approved copy has no video script.')
                inputs.update(script=copy['video']['narration']['text'], chart_id=copy['video']['native_chart_id'])
        if body.get('channel'):
            inputs['channel'] = body['channel']
    return promotion_jobs.create(jobs_root(), kind, inputs, actor)


def _configuration():
    import os
    config_file = Path(os.environ.get('SMN_PROMOTION_CONFIG', '/etc/SMN/promotion.json'))
    configuration = json.loads(config_file.read_text('utf-8')) if config_file.is_file() else {'generation_enabled': False}
    configuration['resolve_source'] = _resolve_source
    return configuration


def _briefing_record(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise ContentError('Choose a dated briefing')
    folder = publication.store().root / 'briefings' / identifier
    if folder.is_symlink():
        raise ContentError('Briefing storage requires inspection.')
    if (folder / 'briefing.json').is_file():
        briefing = json.loads((folder / 'briefing.json').read_text('utf-8'))
        review = json.loads((folder / 'review.json').read_text('utf-8')) if (folder / 'review.json').is_file() else {}
        return {'briefing': briefing, 'review': review, 'active_revision': digest(briefing)}
    if (folder / 'sources.json').is_file():
        bundle = json.loads((folder / 'sources.json').read_text('utf-8'))
        return {'source_bundle': bundle, 'active_revision': digest(bundle)}
    raise ContentError('A source-backed briefing must be prepared before this job can start.')


def list_briefings():
    rows = []
    root = publication.store().root / 'briefings'
    for folder in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            continue
        source = _briefing_record(folder.name)
        value = source.get('briefing', source.get('source_bundle'))
        rows.append({'briefing_id': folder.name, 'date': value.get('edition_date', folder.name),
            'title': value.get('title', 'Daily market briefing'), 'active_revision': source['active_revision'],
            'review_status': source.get('review', {}).get('status', 'pending'),
            'kind': 'briefing' if 'briefing' in source else 'source_bundle'})
    return rows


def _resolve_source(inputs):
    if inputs.get('briefing_id'):
        source = _briefing_record(inputs['briefing_id'])
        if source['active_revision'] != inputs['source_revision']:
            raise ContentError('The daily source changed. Create a new job.')
        return source
    for post in article_index.load_posts():
        slug = pin_store.article_slug(post)
        path = publication.store().root / 'qualified-sources' / (publication.sha(slug.encode()) + '.json')
        if path.is_file():
            record = json.loads(path.read_text('utf-8'))
            if record.get('prepared', {}).get('provenance', {}).get('article_id') == inputs.get('article_id'):
                current = _source_record(slug)
                provenance = current['prepared']['provenance']
                if provenance['revision'] != inputs['source_revision'] or provenance['article_sha256'] != inputs['source_hash']:
                    raise ContentError('The article source changed. Create a new job.')
                return dict(current, slug=slug)
    raise ContentError('The original approved article source is unavailable.')


def get_controls():
    import promotion_jobs
    configuration = _configuration()
    return {'all': promotion_jobs.is_paused(jobs_root(), 'all'),
        'kinds': {kind: promotion_jobs.is_paused(jobs_root(), kind) for kind in promotion_jobs.KINDS},
        'providers': {
            'Codex': {'enabled': bool(configuration.get('generation_enabled') and configuration.get('codex')),
                      'reason': 'Uses the configured subscription writer.'},
            'ElevenLabs': {'enabled': bool(configuration.get('credential_ready')),
                          'reason': 'A verified server credential, voice, model and credit quote are required.'},
            'Distribution': {'enabled': False, 'reason': 'Reviewable exports are available; connected posting accounts are required.'}}}


def set_controls(body, actor):
    import promotion_jobs
    promotion_jobs.pause(jobs_root(), body.get('scope'), body.get('paused'), actor)
    return get_controls()


def _import_job(identifier, expected_version, actor):
    import promotion_jobs
    job = json.loads(promotion_jobs._path(jobs_root(), identifier).read_text('utf-8'))
    if job['version'] != expected_version or job['status'] not in {'generated', 'reviewed'}:
        raise ContentError('Import requires the current generated revision.')
    source = _resolve_source(job['inputs'])
    if job['kind'] == 'derivative':
        from public_derivative import validate_derivative
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'copy.json')
        copy = json.loads(path.read_text('utf-8'))
        validate_derivative(copy, source['prepared'])
        with article_index.posts_lock():
            publication.recover()
            slug = source['slug']
            current = get_preview(slug)
            # An import may not silently discard another editor's saved draft.
            if _draft_file(slug).is_file():
                raise ContentError('A preview draft already exists. Review it before importing another.')
            revision = 'draft-' + digest({'base': current['active_revision'], 'content': copy})[:32]
            preview = {'provenance': dict(source['prepared']['provenance'], revision=revision, mode='summary'), 'content': copy}
            publication._write(_draft_file(slug), {'base_revision': current['active_revision'],
                'revision': revision, 'preview': preview, 'review_status': 'pending',
                'actor': actor, 'updated_at': publication.now(), 'generation_job': identifier})
            result = {'slug': slug, 'revision': revision, 'review_status': 'pending'}
            promotion_jobs.update(jobs_root(), identifier, expected_version, imported_draft=result,
                                  status='generated', review_status='pending')
            return result
    if job['kind'] == 'daily_briefing' and 'source_bundle' in source:
        from daily_briefing import inspect
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'output.json')
        briefing = json.loads(path.read_text('utf-8'))
        if inspect(briefing)['issues']:
            raise ContentError('The daily draft has unresolved source or timing checks.')
        folder = publication.store().root / 'briefings' / job['inputs']['briefing_id']
        publication._write(folder / 'briefing.json', briefing)
        publication._write(folder / 'review.json', {'status': 'pending', 'briefing_sha256': digest(briefing),
                                                   'generation_job': identifier})
        result = {'briefing_id': job['inputs']['briefing_id'], 'revision': digest(briefing), 'review_status': 'pending'}
        promotion_jobs.update(jobs_root(), identifier, expected_version, imported_draft=result,
                              status='generated', review_status='pending')
        return result
    raise ContentError('Only generated article copy and daily text drafts can be imported.')


def action_job(identifier, action, body, actor):
    import promotion_jobs
    if action == 'import':
        return _import_job(identifier, body.get('expected_version'), actor)
    if action == 'generate':
        job = get_job(identifier)
        if body.get('expected_version') != job['version']:
            raise ContentError('Job changed. Reload before generating.')
        # The worker obtains a durable version claim before making a provider call.
        def run():
            import promotion_worker
            promotion_worker.run_one(jobs_root(), identifier, _configuration())
        threading.Thread(target=run, daemon=True, name='smn-promotion-' + identifier).start()
        return get_job(identifier)
    result = promotion_jobs.transition(jobs_root(), identifier, action, body.get('expected_version'), actor, body.get('data'))
    if action == 'review' and result['kind'] == 'derivative' and body['data']['decision'] == 'approved':
        from public_derivative import validate_derivative
        full = json.loads(promotion_jobs._path(jobs_root(), identifier).read_text('utf-8'))
        source = _resolve_source(full['inputs'])
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'copy.json')
        copy = json.loads(path.read_text('utf-8'))
        validate_derivative(copy, source['prepared'])
        source['copy'] = copy
        review = {'status': 'approved', 'reviewer': actor, 'reviewed_at': publication.now(),
                  'copy_sha256': digest(copy), 'prepared_sha256': digest(source['prepared']),
                  'provenance': source['prepared']['provenance'], 'checks': {'editor_inspected_exact_copy': True}}
        if copy.get('video'):
            chart = source.get('native_charts', {}).get(copy['video']['native_chart_id'])
            if not chart or publication.sha(Path(chart['path']).read_bytes()) != chart['sha256']:
                raise ContentError('The approved native chart requires verification before video generation.')
            source.update(chart_path=chart['path'], chart_sha256=chart['sha256'],
                          on_screen=copy['video']['on_screen']['text'])
            review['chart_sha256'] = chart['sha256']
        source['source_review'] = review
        qualified = publication.store().root / 'qualified-sources' / (publication.sha(source.pop('slug').encode()) + '.json')
        publication._write(qualified, source)
    if action == 'review' and result['kind'] == 'daily_briefing':
        full = json.loads(promotion_jobs._path(jobs_root(), identifier).read_text('utf-8'))
        folder = publication.store().root / 'briefings' / full['inputs']['briefing_id']
        review_path = folder / 'review.json'
        existing = json.loads(review_path.read_text('utf-8')) if review_path.is_file() else {}
        if existing.get('generation_job') == identifier:
            briefing = json.loads((folder / 'briefing.json').read_text('utf-8'))
            path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'output.json')
            if digest(briefing) != digest(json.loads(path.read_text('utf-8'))):
                raise ContentError('Imported briefing changed after generation.')
            publication._write(review_path, dict(existing, status=body['data']['decision'], reviewer=actor,
                reviewed_at=publication.now(), briefing_sha256=digest(briefing)))
    return get_job(result['id'])


def artifact(identifier, name):
    import promotion_jobs
    return promotion_jobs.get_artifact(jobs_root(), identifier, name)


def handlers():
    result = {name: globals()[name] for name in ('list_articles', 'get_preview', 'save_preview',
        'review_preview', 'generate_preview', 'list_jobs', 'get_job', 'create_job',
        'list_briefings', 'get_controls', 'set_controls')}
    return dict(result, job_action=action_job, get_artifact=artifact)
