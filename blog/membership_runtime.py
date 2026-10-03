"""Server-owned bindings for the membership dashboard and private job queue."""
from copy import deepcopy
import json
from pathlib import Path
import threading

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
        # Editing copy never edits evidence provenance, source captures or full text.
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
    return create_job({'kind': 'derivative', 'slug': slug}, actor)


def list_jobs():
    import promotion_jobs
    return promotion_jobs.list_jobs(jobs_root())


def get_job(identifier):
    import promotion_jobs
    job = promotion_jobs.get_job(jobs_root(), identifier)
    from subscription_writer import load_json
    full = load_json(promotion_jobs._path(jobs_root(), identifier))
    job['payload_sha256'] = digest(full['inputs'])
    job['script'] = full['inputs'].get('script', '')
    return job


def create_job(body, actor):
    import promotion_jobs
    kind = body.get('kind')
    if kind in {'daily_briefing', 'daily_avatar'}:
        identifier = str(body.get('briefing_id') or '')
        if not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in identifier):
            raise ContentError('Choose a dated briefing')
        path = publication.store().root / 'briefings' / identifier / 'briefing.json'
        if not path.is_file():
            raise ContentError('A source-backed briefing must be prepared before this job can start.')
        briefing = json.loads(path.read_text('utf-8'))
        inputs = {'briefing_id': identifier, 'source_revision': digest(briefing),
                  'source_hash': digest(briefing), 'payload_sha256': digest(briefing)}
    else:
        slug = str(body.get('slug') or '')
        record = _source_record(slug)
        prepared, copy = record['prepared'], record['copy']
        provenance = prepared['provenance']
        inputs = {'article_id': provenance['article_id'], 'source_revision': provenance['revision'],
            'source_hash': provenance['article_sha256'], 'payload_sha256': digest(copy),
            'script': copy['video']['narration']['text'], 'chart_id': copy['video']['native_chart_id'], 'slug': slug}
        if body.get('channel'):
            inputs['channel'] = body['channel']
    return promotion_jobs.create(jobs_root(), kind, inputs, actor)


def _configuration():
    import os
    config_file = Path(os.environ.get('SMN_PROMOTION_CONFIG', '/etc/SMN/promotion.json'))
    configuration = json.loads(config_file.read_text('utf-8')) if config_file.is_file() else {'generation_enabled': False}
    configuration['resolve_source'] = lambda inputs: _source_record(inputs['slug'])
    return configuration


def action_job(identifier, action, body, actor):
    import promotion_jobs
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
    return get_job(result['id'])


def artifact(identifier, name):
    import promotion_jobs
    return promotion_jobs.get_artifact(jobs_root(), identifier, name)


def handlers():
    result = {name: globals()[name] for name in ('list_articles', 'get_preview', 'save_preview',
        'review_preview', 'generate_preview', 'list_jobs', 'get_job', 'create_job')}
    return dict(result, job_action=action_job, get_artifact=artifact)
