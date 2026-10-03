"""Public projections of exact reviewed daily text and media; source captures stay private."""
import json
from pathlib import Path
import re

from daily_briefing import inspect, digest
from article_content_store import ContentError


def load(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise ContentError('Briefing is unavailable')
    folder = Path(root) / 'briefings' / identifier
    if folder.is_symlink():
        raise ContentError('Briefing is unavailable')
    try:
        value = json.loads((folder / 'briefing.json').read_text('utf-8'))
        review = json.loads((folder / 'review.json').read_text('utf-8'))
        check = inspect(value, review)
    except (OSError, ValueError, KeyError):
        raise ContentError('Briefing is unavailable') from None
    if check['issues'] or check['review_status'] != 'approved':
        raise ContentError('Briefing is unavailable')
    return {'id': identifier, 'title': value['title'], 'date': value['edition_date'],
            'label': value['label'], 'cutoff': value['cutoff'], 'narrative': value['narrative'],
            'headlines': [group['headline'] for group in value['groups'] if group['selected']],
            'sources': [{key: source[key] for key in ('title', 'url', 'publisher')} for source in value['sources']],
            'sha256': digest(value)}


def listing(root):
    result = []
    for folder in (Path(root) / 'briefings').glob('*'):
        if not folder.is_dir():
            continue
        try:
            result.append(load(root, folder.name))
        except ContentError:
            continue
    return sorted(result, key=lambda row: (row['date'], row['id']), reverse=True)


def video(root, briefing, media_type='video/mp4'):
    import promotion_jobs
    jobs_root = Path(root) / 'promotion'
    for summary in promotion_jobs.list_jobs(jobs_root):
        if summary['kind'] not in {'daily_briefing', 'daily_avatar'} or summary['review_status'] != 'approved':
            continue
        job = json.loads(promotion_jobs._path(jobs_root, summary['id']).read_text('utf-8'))
        if job['source_hash'] != briefing['sha256'] or job['status'] != 'reviewed':
            continue
        if not any(a['media_type'] == 'video/mp4' for a in job['artifacts']):
            continue
        for artifact in job['artifacts']:
            if artifact['media_type'] == media_type and job.get('review', {}).get('artifact_hashes', {}).get(artifact['name']) == artifact['sha256']:
                return promotion_jobs.get_artifact(jobs_root, job['id'], artifact['name'])[0]
        return None  # Never pair captions from an older, different video.
    return None
