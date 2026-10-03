"""Server-owned bindings for the membership dashboard and private job queue."""
from copy import deepcopy
import json
from pathlib import Path
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
    if (record.get('canonical') != publication.path_for(post)
            or record.get('prepared', {}).get('provenance', {}).get('article_id') != post['url']):
        raise ContentError('The qualified source does not belong to this canonical article.')
    _verify_retained(record)
    return record


def _verify_retained(record):
    from public_derivative import validate_prepared
    hashes = record.get('retained_input_hashes')
    if not isinstance(hashes, dict) or not hashes:
        raise ContentError('The complete retained article and review capsule must be qualified first.')
    for name, expected in hashes.items():
        path = Path(name)
        if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
                or not path.is_file() or publication.sha(path.read_bytes()) != expected):
            raise ContentError('Retained source evidence changed. Requalify before generating or reviewing.')
    validate_prepared(record['prepared'])


def _approved_copy(record):
    from public_derivative import validate_derivative
    copy, review = record.get('copy'), record.get('source_review') or {}
    prepared = record['prepared']
    if (not copy or review.get('status') != 'approved' or not review.get('reviewer')
            or not review.get('reviewed_at') or review.get('copy_sha256') != digest(copy)
            or review.get('prepared_sha256') != digest(prepared)
            or review.get('provenance') != prepared['provenance']
            or not review.get('checks') or any(value is not True for value in review['checks'].values())):
        raise ContentError('Generate and explicitly approve this article\'s exact public copy first.')
    validate_derivative(copy, prepared)
    job = _reviewed_job(review)
    provenance = prepared['provenance']
    if (job['kind'] != 'derivative' or job['inputs'].get('payload_sha256') != digest(prepared)
            or any(job['inputs'].get(key) != provenance[field] for key, field in
                   (('article_id','article_id'),('source_revision','revision'),('source_hash','article_sha256')))):
        raise ContentError('The approved public copy belongs to a different canonical source revision.')
    if copy.get('video'):
        chart = record.get('native_charts', {}).get(copy['video']['native_chart_id'])
        if (not chart or record.get('retained_input_hashes', {}).get(chart['path']) != chart['sha256']
                or publication.sha(Path(chart['path']).read_bytes()) != chart['sha256']
                or review.get('chart_sha256') != chart['sha256']):
            raise ContentError('The approved native chart changed. Requalify its exact source and script.')
    return copy


def _reviewed_job(review):
    if not review.get('generation_job'):
        raise ContentError('An exact generated-copy review receipt is required.')
    import promotion_jobs
    job = json.loads(promotion_jobs._path(jobs_root(), review['generation_job']).read_text('utf-8'))
    receipt = job.get('review') or {}
    if (job['status'] != 'reviewed' or job['review_status'] != 'approved'
            or receipt.get('payload_sha256') != digest(job['inputs'])
            or receipt.get('actor') != review.get('reviewer') or receipt.get('at') != review.get('reviewed_at')
            or receipt.get('artifact_hashes') != {item['name']:item['sha256'] for item in job['artifacts']}):
        raise ContentError('The exact generated-copy review is not committed yet.')
    for artifact in job['artifacts']:
        promotion_jobs.get_artifact(jobs_root(), job['id'], artifact['name'])
    name, expected = (('script.json', review['script_sha256']) if review.get('script_sha256') else
                      ('copy.json', review['copy_sha256']) if review.get('copy_sha256') else
                      ('output.json', review.get('briefing_sha256')))
    artifact, _ = promotion_jobs.get_artifact(jobs_root(), job['id'], name)
    if not expected or digest(json.loads(artifact.read_text('utf-8'))) != expected:
        raise ContentError('The approved copy differs from its exact generated artifact.')
    return job


def _approved_video_script(record, identifier):
    from public_derivative import validate_video_script
    copy = _approved_copy(record)
    script, review = record.get('video_script'), record.get('video_script_review') or {}
    prepared = record['prepared']
    if (not script or review.get('status') != 'approved' or review.get('generation_job') != identifier
            or review.get('script_sha256') != digest(script) or review.get('copy_sha256') != digest(copy)
            or review.get('prepared_sha256') != digest(prepared) or review.get('provenance') != prepared['provenance']
            or not review.get('checks') or any(v is not True for v in review['checks'].values())):
        raise ContentError('The separate narration requires exact current copy and script approval.')
    validate_video_script(script, prepared, copy)
    job = _reviewed_job(review)
    if job['kind'] != 'article_script' or job['inputs'].get('payload_sha256') != digest(copy):
        raise ContentError('The reviewed script differs from this approved public copy.')
    if any(job['inputs'].get(k) != prepared['provenance'][v] for k, v in
           (('article_id', 'article_id'), ('source_revision', 'revision'), ('source_hash', 'article_sha256'))):
        raise ContentError('The script belongs to a different source revision.')
    chart = _native_script_chart(record, script)
    if review.get('chart_sha256') != chart['sha256']:
        raise ContentError('The separately approved native chart changed.')
    return script


def _native_script_chart(record, script):
    chart = record.get('native_charts', {}).get(script['native_chart_id'])
    if (not chart or record.get('retained_input_hashes', {}).get(chart['path']) != chart['sha256']
            or publication.sha(Path(chart['path']).read_bytes()) != chart['sha256']):
        raise ContentError('The retained native chart requires exact verification before narration review.')
    return chart


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
    job['automatic_followup'] = full.get('automation_successor')
    if job['kind'] == 'article_script' and any(a['name'] == 'script.json' for a in job['artifacts']):
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'script.json')
        job['script_payload'] = load_json(path)
    if job['kind'] == 'derivative' and (job['imported_draft'] or {}).get('slug'):
        try:
            _, manifest = publication.source(_post(job['imported_draft']['slug']))
            copy_path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'copy.json')
            if digest(manifest['preview']['content']) == digest(load_json(copy_path)):
                job['imported_draft'] = dict(job['imported_draft'], review_status='published',
                                             revision=manifest['revision'])
        except (ContentError, OSError, ValueError, KeyError):
            pass  # Keep the saved import receipt when its live binding is unavailable.
    inputs=full['inputs']
    if inputs.get('briefing_id'):
        identifier=inputs['briefing_id']
        job['subject_label']='Daily briefing'
        if re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier):
            job['subject_label']='Daily briefing · '+identifier
            try:
                source=_briefing_record(identifier)
                value=source.get('briefing',source.get('source_bundle'))
                job['subject_label']=value.get('title') or (value.get('edition_date',identifier)+' · '+value.get('label','Daily briefing'))
            except ContentError:
                pass
    else:
        matches=[post for post in article_index.load_posts() if post.get('url')==inputs.get('article_id')]
        job['subject_label']=matches[0].get('title') or 'Research article' if len(matches)==1 else 'Research article unavailable'
    job['subject_label']=' '.join(str(job['subject_label']).split())[:300]
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
        if kind == 'daily_avatar' and body.get('briefing_media_job_id'):
            inputs['briefing_media_job_id'] = body['briefing_media_job_id']
    else:
        slug = str(body.get('slug') or '')
        record = _source_record(slug)
        prepared = record['prepared']
        provenance = prepared['provenance']
        inputs = {'article_id': provenance['article_id'], 'source_revision': provenance['revision'],
            'source_hash': provenance['article_sha256'], 'payload_sha256': digest(prepared)}
        if kind != 'derivative':
            copy = _approved_copy(record)
            inputs['payload_sha256'] = digest(copy)
            if kind == 'article_video':
                script = copy.get('video')
                if not script:
                    identifier = (record.get('video_script_review') or {}).get('generation_job')
                    script = _approved_video_script(record, identifier)
                    inputs['script_job_id'] = identifier
                inputs.update(script=script['narration']['text'], chart_id=script['native_chart_id'])
            if kind == 'article_script' and copy.get('video'):
                raise ContentError('This approved copy already has narration; its video is queued after approval.')
        if body.get('channel'):
            inputs['channel'] = body['channel']
    return promotion_jobs.create(jobs_root(), kind, inputs, actor)


def _configuration():
    import os
    config_file = Path(os.environ.get('SMN_PROMOTION_CONFIG', '/etc/SMN/promotion.json'))
    configuration = json.loads(config_file.read_text('utf-8')) if config_file.is_file() else {'generation_enabled': False}
    configuration['public_origin'] = os.environ.get('SMN_PUBLIC_ORIGIN') or configuration.get('public_origin') or os.environ.get('SMN_SITE_BASE')
    configuration['resolve_source'] = _worker_source
    return configuration


def _worker_source(inputs):
    with article_index.posts_lock():
        publication.recover()
        recover_operations()
        return _resolve_source(inputs)


def _briefing_record(identifier, source_revision=None):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise ContentError('Choose a dated briefing')
    folder = publication.store().root / 'briefings' / identifier
    if any(p.is_symlink() for p in (folder, *folder.parents)) or any((folder/name).is_symlink() for name in ('sources.json','briefing.json','review.json')):
        raise ContentError('Briefing storage requires inspection.')
    bundle = json.loads((folder / 'sources.json').read_text('utf-8')) if (folder / 'sources.json').is_file() else None
    if bundle is not None and source_revision == digest(bundle):
        return {'source_bundle': bundle, 'active_revision': digest(bundle)}
    if (folder / 'briefing.json').is_file():
        briefing = json.loads((folder / 'briefing.json').read_text('utf-8'))
        review = json.loads((folder / 'review.json').read_text('utf-8')) if (folder / 'review.json').is_file() else {}
        if review.get('source_bundle_sha256') and (bundle is None or digest(bundle) != review['source_bundle_sha256']):
            raise ContentError('The daily source bundle changed. Rebuild and review its briefing.')
        if review.get('status') == 'approved' and review.get('generation_job'):
            job = _reviewed_job(review)
            if (job['kind'] != 'daily_briefing' or job['inputs'].get('briefing_id') != identifier
                    or job['inputs'].get('source_hash') != review.get('source_bundle_sha256')):
                raise ContentError('The approved briefing belongs to a different dated source bundle.')
        return {'briefing': briefing, 'review': review, 'active_revision': digest(briefing)}
    if (folder / 'sources.json').is_file():
        return {'source_bundle': bundle, 'active_revision': digest(bundle)}
    raise ContentError('A source-backed briefing must be prepared before this job can start.')


def list_briefings():
    rows = []
    root = publication.store().root / 'briefings'
    for folder in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            continue
        capture_path = folder / 'capture-status.json'
        capture = json.loads(capture_path.read_text('utf-8')) if capture_path.is_file() else {}
        if not (folder / 'sources.json').is_file() and not (folder / 'briefing.json').is_file():
            if capture:
                rows.append({'briefing_id': folder.name, 'date': capture.get('edition_date', folder.name),
                    'title': capture.get('label', 'Daily source capture'), 'active_revision': None,
                    'review_status': 'pending', 'kind': 'source_bundle',
                    'capture_status': capture.get('status', 'failed'), 'holds': capture.get('holds', [])})
            continue
        try:
            source = _briefing_record(folder.name)
        except ContentError as exc:
            rows.append({'briefing_id':folder.name,'date':folder.name,'title':'Daily briefing requires inspection',
                         'active_revision':None,'review_status':'stale','kind':'briefing' if (folder/'briefing.json').is_file() else 'source_bundle',
                         'capture_status':'held','holds':[str(exc)]})
            continue
        value = source.get('briefing', source.get('source_bundle'))
        row = {'briefing_id': folder.name, 'date': value.get('edition_date', folder.name),
            'title': value.get('title', 'Daily market briefing'), 'active_revision': source['active_revision'],
            'review_status': source.get('review', {}).get('status', 'pending'),
            'kind': 'briefing' if 'briefing' in source else 'source_bundle'}
        if capture:
            bundle = json.loads((folder / 'sources.json').read_text('utf-8')) if (folder / 'sources.json').is_file() else None
            bound = bundle is not None and capture.get('source_bundle_sha256') == digest(bundle)
            row.update(capture_status=capture.get('status') if bound else 'stale',
                       holds=capture.get('holds', []) if bound else ['Capture receipt differs from its saved source bundle.'])
        rows.append(row)
    return rows


def _resolve_source(inputs):
    if inputs.get('briefing_id'):
        source = _briefing_record(inputs['briefing_id'], inputs['source_revision'])
        if source['active_revision'] != inputs['source_revision'] or inputs.get('source_hash') != source['active_revision']:
            raise ContentError('The daily source changed. Create a new job.')
        return source
    matches = [post for post in article_index.load_posts() if post.get('url') == inputs.get('article_id')]
    if len(matches) != 1:
        raise ContentError('The exact canonical article source is unavailable or duplicated.')
    slug = pin_store.article_slug(matches[0])
    current = _source_record(slug)
    provenance = current['prepared']['provenance']
    if provenance['revision'] != inputs['source_revision'] or provenance['article_sha256'] != inputs['source_hash']:
        raise ContentError('The article source changed. Create a new job.')
    payload = inputs.get('payload_sha256')
    if payload and payload != digest(current['prepared']):
        if payload != digest(_approved_copy(current)):
            raise ContentError('The approved public copy changed. Create a new job.')
    if inputs.get('script_job_id'):
        script = _approved_video_script(current, inputs['script_job_id'])
        if (inputs.get('script') != script['narration']['text'] or inputs.get('chart_id') != script['native_chart_id']):
            raise ContentError('Video inputs differ from the exact reviewed narration.')
        chart = _native_script_chart(current, script)
        current = dict(current, chart_path=chart['path'], chart_sha256=chart['sha256'],
                       on_screen=script['on_screen']['text'])
    return dict(current, slug=slug)


def _queue_article_followup(job):
    """Idempotent private chain; caller holds posts_lock, no provider call here."""
    import promotion_jobs
    from subscription_writer import load_json
    current = load_json(promotion_jobs._path(jobs_root(), job['id']))
    successor = current.get('automation_successor')
    if successor:
        child = promotion_jobs.get_job(jobs_root(), successor['id'])
        kind = child['kind']
        if child['status'] != 'draft' and not (child['status'] == 'held' and child['generation_status'] == 'provider_disabled'):
            return child
    else:
        source = _resolve_source(job['inputs'])
        if job['kind'] == 'daily_briefing':
            identifier = job['inputs']['briefing_id']
            if 'source_bundle' in source:
                approved = _briefing_record(identifier)
                if 'briefing' not in approved or approved.get('review', {}).get('generation_job') != job['id']:
                    raise ContentError('The exact approved daily text is unavailable.')
                kind = 'daily_briefing'
                child = create_job({'kind': kind, 'briefing_id': identifier}, 'approved-source-automation')
            else:
                required = {'briefing.mp4', 'narration.mp3', 'narration.alignment.json', 'narration.receipt.json',
                            'captions.vtt', 'transcript.txt', 'briefing.receipt.json'}
                if not required.issubset({item['name'] for item in current['artifacts']}):
                    raise ContentError('Approve a complete daily narration before its presenter segment.')
                receipt = current.get('review') or {}
                if (receipt.get('payload_sha256') != digest(current['inputs']) or
                        receipt.get('artifact_hashes') != {a['name']: a['sha256'] for a in current['artifacts']}):
                    raise ContentError('The daily media approval no longer matches its exact artifacts.')
                for item in current['artifacts']:
                    promotion_jobs.get_artifact(jobs_root(), current['id'], item['name'])
                kind = 'daily_avatar'
                child = create_job({'kind': kind, 'briefing_id': identifier,
                                    'briefing_media_job_id': job['id']}, 'approved-source-automation')
        else:
            copy = _approved_copy(source)
            if job['kind'] == 'derivative' and source['source_review']['generation_job'] != job['id']:
                raise ContentError('A newer public copy superseded this automation source.')
            if job['kind'] == 'article_script':
                _approved_video_script(source, job['id'])
                kind = 'article_video'
            else:
                kind = 'article_video' if copy.get('video') else 'article_script'
            child = create_job({'kind': kind, 'slug': source['slug']}, 'approved-source-automation')
        promotion_jobs.update(jobs_root(), current['id'], current['version'],
                              automation_successor={'id': child['id'], 'kind': kind})
    try:
        configuration = _configuration()
    except (OSError, ValueError, TypeError):
        configuration = {}
    if kind == 'article_script':
        ready = all(configuration.get(k) for k in ('generation_enabled', 'writer_settings', 'codex'))
        reason = 'Subscription narration writing is unavailable; enable the configured Codex writer.'
    elif kind == 'daily_avatar':
        request = configuration.get('avatar_request')
        request = request if isinstance(request, dict) else {}
        ready = bool(configuration.get('generation_enabled') and all(configuration.get(k) for k in
                     ('avatar_ready', 'likeness_verified', 'voice_verified', 'credential_ready', 'flows_plan_verified'))
                     and (request.get('quote') or configuration.get('avatar_tariff_path')))
        reason = 'Presenter video is held: verified likeness, voice, credential, supported plan and an authorized credit quote are required.'
    else:
        ready = bool(configuration.get('generation_enabled') and all(configuration.get(k) for k in
                     ('credential_ready', 'voice_verified', 'model_verified')) and
                     any(configuration.get(k) for k in ('quote', 'speech_tariff_path', 'resolve_speech_quote')))
        reason = ('Daily narration' if kind == 'daily_briefing' else 'Article video') + ' is held: a verified ElevenLabs credential, voice, model and authorized credit quote are required.'
    if child['status'] == 'draft' or (child['status'] == 'held' and child['generation_status'] == 'provider_disabled' and ready):
        child = promotion_jobs.update(jobs_root(), child['id'], child['version'],
                    status='queued' if ready else 'held', stage='queued' if ready else 'capability',
                    generation_status='queued' if ready else 'provider_disabled', holds=[] if ready else [reason])
    return child


def reconcile_article_media():
    """Resume committed approvals and capability holds without repeating charges."""
    import promotion_jobs
    results = []
    for summary in promotion_jobs.list_jobs(jobs_root()):
        if summary['kind'] not in {'derivative', 'article_script', 'daily_briefing'} or summary['review_status'] != 'approved' or summary['status'] != 'reviewed':
            continue
        try:
            job = json.loads(promotion_jobs._path(jobs_root(), summary['id']).read_text('utf-8'))
            results.append(_queue_article_followup(job))
        except (ContentError, promotion_jobs.Conflict, OSError, ValueError):
            # Source changes invalidate followups; durable approvals remain intact.
            continue
    return results


def _operation_path(job):
    return publication.store().root / 'promotion-operations' / (job['id'] + '-' + str(job['version']) + '.json')


def _apply_operation(path, intent):
    import promotion_jobs
    from subscription_writer import save_json
    # Source identity is checked again on recovery, before committing approval.
    _resolve_source(intent['old_job']['inputs'])
    with promotion_jobs.locked(jobs_root()):
        job_path = promotion_jobs._path(jobs_root(), intent['old_job']['id'])
        current = json.loads(job_path.read_text('utf-8'))
        if current not in (intent['old_job'], intent['new_job']):
            raise ContentError('Interrupted promotion operation conflicts with a newer job.')
        for artifact in intent['old_job']['artifacts']:
            promotion_jobs.get_artifact(jobs_root(), current['id'], artifact['name'])
        for item in intent['files']:
            target = Path(item['path'])
            if (publication.store().root.resolve() not in target.resolve().parents
                    or any(p.is_symlink() for p in (target, *target.parents))):
                raise ContentError('Private promotion operation storage requires inspection.')
            actual = json.loads(target.read_text('utf-8')) if target.is_file() else None
            if actual not in (item['old'], item['new']):
                raise ContentError('Interrupted promotion operation conflicts with a newer saved draft or review.')
        for item in intent['files']:
            publication._write(Path(item['path']), item['new'])
        save_json(job_path, intent['new_job'])  # Status is durable only after source receipts.
    path.unlink()


def _job_operation(job, actor, files, **fields):
    from subscription_writer import utc_now
    new = dict(deepcopy(job), **fields, actor=actor, version=job['version'] + 1, updated_at=utc_now())
    changes = [{'path': str(path), 'old': json.loads(path.read_text('utf-8')) if path.is_file() else None,
                'new': value} for path, value in files]
    intent = {'old_job': job, 'new_job': new, 'files': changes}
    path = _operation_path(job)
    if path.exists():
        raise ContentError('Recover the interrupted promotion operation before another action.')
    publication._write(path, intent)
    _apply_operation(path, intent)
    return get_job(job['id'])


def recover_operations():
    """Caller holds posts_lock; exact private source/job receipts recover together."""
    root = publication.store().root / 'promotion-operations'
    for path in sorted(root.glob('*.json')):
        _apply_operation(path, json.loads(path.read_text('utf-8')))


def get_controls():
    import promotion_jobs
    configuration = _configuration()
    return {'all': promotion_jobs.is_paused(jobs_root(), 'all'),
        'kinds': {kind: promotion_jobs.is_paused(jobs_root(), kind) for kind in promotion_jobs.KINDS},
        'providers': {
            'Codex': {'enabled': bool(configuration.get('generation_enabled') and configuration.get('codex') and configuration.get('writer_settings')),
                      'reason': 'Uses the configured subscription writer.'},
            'ElevenLabs': {'enabled': bool(configuration.get('generation_enabled') and all(configuration.get(flag) for flag in
                                          ('credential_ready', 'voice_verified', 'model_verified', 'quote'))),
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
        slug = source['slug']
        current = get_preview(slug)
        if _draft_file(slug).is_file():
            raise ContentError('A preview draft already exists. Review it before importing another.')
        revision = 'draft-' + digest({'base': current['active_revision'], 'content': copy})[:32]
        preview = {'provenance': dict(source['prepared']['provenance'], revision=revision, mode='summary'), 'content': copy}
        draft = {'base_revision': current['active_revision'], 'revision': revision, 'preview': preview,
                 'review_status': 'pending', 'actor': actor, 'updated_at': publication.now(), 'generation_job': identifier}
        result = {'slug': slug, 'revision': revision, 'review_status': 'pending'}
        _job_operation(job, actor, [(_draft_file(slug), draft)], imported_draft=result,
                       status='generated', review_status='pending')
        return result
    if job['kind'] == 'daily_briefing' and 'source_bundle' in source:
        from daily_briefing import inspect
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'output.json')
        briefing = json.loads(path.read_text('utf-8'))
        if inspect(briefing)['issues']:
            raise ContentError('The daily draft has unresolved source or timing checks.')
        folder = publication.store().root / 'briefings' / job['inputs']['briefing_id']
        if (folder / 'briefing.json').is_file():
            raise ContentError('A dated briefing draft already exists. Review it before importing another.')
        review = {'status': 'pending', 'briefing_sha256': digest(briefing),
                  'generation_job': identifier, 'source_bundle_sha256': job['inputs']['source_hash']}
        result = {'briefing_id': job['inputs']['briefing_id'], 'revision': digest(briefing), 'review_status': 'pending'}
        _job_operation(job, actor, [(folder / 'briefing.json', briefing), (folder / 'review.json', review)],
                       imported_draft=result, status='generated', review_status='pending')
        return result
    raise ContentError('Only generated article copy and daily text drafts can be imported.')


def action_job(identifier, action, body, actor):
    with article_index.posts_lock():
        publication.recover()
        recover_operations()
        return _action_job(identifier, action, body, actor)


def _action_job(identifier, action, body, actor):
    import promotion_jobs
    job = json.loads(promotion_jobs._path(jobs_root(), identifier).read_text('utf-8'))
    if job['version'] != body.get('expected_version'):
        raise ContentError('Job changed. Reload before continuing.')
    if action == 'import':
        return _import_job(identifier, job['version'], actor)
    if action == 'generate':
        if job['status'] != 'draft':
            raise ContentError('Generation requires a fresh draft or an explicit reconciled retry.')
        _resolve_source(job['inputs'])
        # The daemon owns provider calls. A dashboard restart cannot lose this request.
        return promotion_jobs.update(jobs_root(), identifier, job['version'], status='queued',
                                     stage='queued', generation_status='queued', actor=actor)
    if action != 'review':
        result = promotion_jobs.transition(jobs_root(), identifier, action, job['version'], actor, body.get('data'))
        return get_job(result['id'])
    data = body.get('data') or {}
    if (job['status'] != 'generated' or data.get('payload_sha256') != digest(job['inputs'])
            or data.get('decision') not in {'approved', 'rejected'} or not actor):
        raise ContentError('Review requires the exact generated revision and a named explicit decision.')
    for artifact in job['artifacts']:
        promotion_jobs.get_artifact(jobs_root(), identifier, artifact['name'])
    source = _resolve_source(job['inputs'])
    files, fields = [], {}
    now = publication.now()
    if job['kind'] == 'derivative' and data['decision'] == 'approved':
        from public_derivative import validate_derivative
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'copy.json')
        copy = json.loads(path.read_text('utf-8'))
        validate_derivative(copy, source['prepared'])
        if job['inputs'].get('payload_sha256') != digest(source['prepared']):
            raise ContentError('The reviewed derivative belongs to a different source preparation.')
        if source.get('copy') != copy:
            source.pop('video_script', None)
            source.pop('video_script_review', None)
        source['copy'] = copy
        review = {'status': 'approved', 'reviewer': actor, 'reviewed_at': now,
                  'copy_sha256': digest(copy), 'prepared_sha256': digest(source['prepared']),
                  'provenance': source['prepared']['provenance'], 'generation_job': identifier,
                  'checks': {'editor_inspected_exact_copy': True}}
        if copy.get('video'):
            chart = source.get('native_charts', {}).get(copy['video']['native_chart_id'])
            if (not chart or source.get('retained_input_hashes', {}).get(chart['path']) != chart['sha256']
                    or publication.sha(Path(chart['path']).read_bytes()) != chart['sha256']):
                raise ContentError('The approved native chart requires verification before video generation.')
            source.update(chart_path=chart['path'], chart_sha256=chart['sha256'],
                          on_screen=copy['video']['on_screen']['text'])
            review['chart_sha256'] = chart['sha256']
        else:
            for key in ('chart_path', 'chart_sha256', 'on_screen'): source.pop(key, None)
        source['source_review'] = review
        qualified = publication.store().root / 'qualified-sources' / (publication.sha(source.pop('slug').encode()) + '.json')
        files.append((qualified, source))
    if job['kind'] == 'article_script' and data['decision'] == 'approved':
        from public_derivative import validate_video_script
        copy = _approved_copy(source)
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'script.json')
        script = json.loads(path.read_text('utf-8'))
        validate_video_script(script, source['prepared'], copy)
        if job['inputs'].get('payload_sha256') != digest(copy):
            raise ContentError('The script was generated for a different public copy.')
        chart = _native_script_chart(source, script)
        source['video_script'] = script
        source['video_script_review'] = {'status': 'approved', 'reviewer': actor, 'reviewed_at': now,
            'script_sha256': digest(script), 'copy_sha256': digest(copy),
            'prepared_sha256': digest(source['prepared']), 'provenance': source['prepared']['provenance'],
            'chart_sha256': chart['sha256'], 'generation_job': identifier,
            'checks': {'editor_inspected_exact_source_script_caption': True}}
        qualified = publication.store().root / 'qualified-sources' / (publication.sha(source.pop('slug').encode()) + '.json')
        files.append((qualified, source))
    if job['kind'] == 'daily_briefing' and 'source_bundle' in source:
        from daily_briefing import inspect
        folder = publication.store().root / 'briefings' / job['inputs']['briefing_id']
        if not job.get('imported_draft') or not (folder / 'review.json').is_file():
            raise ContentError('Save the dated briefing draft before its explicit review.')
        existing = json.loads((folder / 'review.json').read_text('utf-8'))
        briefing = json.loads((folder / 'briefing.json').read_text('utf-8'))
        path, _ = promotion_jobs.get_artifact(jobs_root(), identifier, 'output.json')
        binding = digest(briefing)
        if (existing.get('generation_job') != identifier or binding != digest(json.loads(path.read_text('utf-8')))
                or existing.get('briefing_sha256') != binding or job['imported_draft']['revision'] != binding
                or existing.get('source_bundle_sha256') != job['inputs']['source_hash']):
            raise ContentError('The imported briefing or its exact source changed after generation.')
        review = dict(existing, status=data['decision'], reviewer=actor, reviewed_at=now)
        if data['decision'] == 'approved' and inspect(briefing, review)['issues']:
            raise ContentError('Resolve the briefing source and timing checks before approval.')
        files.append((folder / 'review.json', review))
        fields['imported_draft'] = dict(job['imported_draft'], review_status=data['decision'])
    receipt = {'actor': actor, 'at': now, 'payload_sha256': data['payload_sha256'],
               'artifact_hashes': {item['name']: item['sha256'] for item in job['artifacts']}}
    result = _job_operation(job, actor, files, status='reviewed' if data['decision'] == 'approved' else 'held',
                            review_status=data['decision'], review=receipt, **fields)
    if data['decision'] == 'approved' and job['kind'] in {'derivative', 'article_script', 'daily_briefing'}:
        try:
            result['automatic_followup'] = _queue_article_followup(dict(job, status='reviewed', review_status='approved'))
        except (ContentError, promotion_jobs.Conflict, OSError, ValueError):
            # A committed approval survives a temporary queue/configuration failure.
            result['automatic_followup'] = {'status': 'pending_reconciliation'}
        result['version'] = promotion_jobs.get_job(jobs_root(), identifier)['version']
    return result


def artifact(identifier, name):
    import promotion_jobs
    return promotion_jobs.get_artifact(jobs_root(), identifier, name)


def handlers():
    result = {name: globals()[name] for name in ('list_articles', 'get_preview', 'save_preview',
        'review_preview', 'generate_preview', 'list_jobs', 'get_job', 'create_job',
        'list_briefings', 'get_controls', 'set_controls')}
    return dict(result, job_action=action_job, get_artifact=artifact)
