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
        artifacts={a['name']:a for a in job['artifacts']}
        review=job.get('review',{}).get('artifact_hashes',{})
        complete=artifacts.get('briefing.mp4')
        if not complete or review.get('briefing.mp4')!=complete['sha256']:
            continue
        if job['kind']=='daily_avatar':
            receipt=artifacts.get('briefing.receipt.json')
            if not receipt or review.get(receipt['name'])!=receipt['sha256']:continue
            try:
                path=promotion_jobs.get_artifact(jobs_root,job['id'],receipt['name'])[0]
                binding=json.loads(path.read_text('utf-8'))
                if (binding.get('media_role')!='full_briefing' or binding.get('complete_narration') is not True
                        or binding.get('briefing_sha256')!=briefing['sha256']
                        or binding.get('video_sha256')!=complete['sha256']):continue
                base_id=job['inputs'].get('briefing_media_job_id')
                if not base_id or binding.get('base_job_id')!=base_id:continue
                base=json.loads(promotion_jobs._path(jobs_root,base_id).read_text('utf-8'))
                if (base.get('kind')!='daily_briefing' or base.get('status')!='reviewed'
                        or base.get('review_status')!='approved' or base.get('source_hash')!=briefing['sha256']):continue
                base_artifacts={a['name']:a for a in base['artifacts']}
                base_review=base.get('review',{}).get('artifact_hashes',{})
                if binding.get('duration_seconds',0)<=0 or binding.get('decoded') is not True:continue
                for name,key in [('narration.mp3','audio_sha256'),('captions.vtt','captions_sha256')]:
                    if (not base_artifacts.get(name) or base_review.get(name)!=base_artifacts[name]['sha256']
                            or binding.get(key)!=base_artifacts[name]['sha256']):raise ValueError('Avatar master binding differs')
                    promotion_jobs.get_artifact(jobs_root,base_id,name)

                    if not artifacts.get(name) or review.get(name)!=artifacts[name]['sha256'] or binding.get(key)!=artifacts[name]['sha256']:
                        raise ValueError('Full avatar narration/caption binding differs')
            except (OSError,ValueError,KeyError):continue
        name='briefing.mp4' if media_type=='video/mp4' else 'captions.vtt' if media_type=='text/vtt' else None
        artifact=artifacts.get(name)
        if artifact and artifact['media_type']==media_type and review.get(name)==artifact['sha256']:
            return promotion_jobs.get_artifact(jobs_root,job['id'],name)[0]
        return None  # Never pair captions from an older, different video.
    return None
