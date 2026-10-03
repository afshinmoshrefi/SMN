"""One private source-bound generation job; never performs external dispatch."""
from pathlib import Path

from daily_briefing import digest
import elevenlabs_client as eleven
import promotion_jobs as jobs
from public_derivative import validate_derivative
from subscription_writer import load_json, save_json, sha256
from video_render import compose


def _hold(root, job, reason):
    return jobs.update(root,job['id'],job['version'],status='held',stage='readiness',holds=[reason],
                       generation_status='held',dispatch_status='disabled')


def run_one(root, identifier, configuration):
    """Configuration/resolver/quotes are server-owned, never taken from HTTP input.

    resolve_source(inputs) returns prepared, copy, source_review, chart_path,
    chart_sha256, on_screen and title. It must resolve canonical current revision.
    """
    job=load_json(jobs._path(root,identifier))
    if job['status'] in {'generated','reviewed','canceled','superseded'} or jobs.is_paused(root,job['kind']):
        return jobs.summary(job)
    if job['status'] == 'running' or job['generation_status'] == 'unknown_outcome':
        raise jobs.Conflict('Provider result requires reconciliation before another attempt')
    if job['kind'] != 'article_video':
        return _hold(root,job,'This generation kind is not configured')
    if not configuration.get('generation_enabled'):
        return _hold(root,job,'Private generation is disabled')
    resolver=configuration.get('resolve_source')
    if not callable(resolver):
        return _hold(root,job,'Canonical source resolver is unavailable')
    try:
        source=resolver(dict(job['inputs']))
        prepared, copy, review=source['prepared'],source['copy'],source['source_review']
        inputs=job['inputs']; provenance=prepared['provenance']
        if (inputs['article_id'] != provenance['article_id'] or inputs['source_revision'] != provenance['revision']
                or inputs['source_hash'] != provenance['article_sha256']):
            raise ValueError('Canonical source revision changed')
        for name,expected in prepared['input_hashes'].items():
            if sha256(Path(name).read_bytes()) != expected:
                raise ValueError('Retained source input changed')
        validate_derivative(copy,prepared)
        if (inputs.get('payload_sha256') != digest(copy) or inputs.get('script') != copy['video']['narration']['text']
                or inputs.get('chart_id') != copy['video']['native_chart_id']
                or source['on_screen'] != copy['video']['on_screen']['text']):
            raise ValueError('Script differs from source-bound copy')
        if (review.get('status') != 'approved' or not review.get('reviewer') or not review.get('reviewed_at')
                or review.get('copy_sha256') != digest(copy) or review.get('prepared_sha256') != digest(prepared)
                or review.get('provenance') != provenance or review.get('chart_sha256') != source['chart_sha256']
                or not review.get('checks') or any(v is not True for v in review['checks'].values())):
            raise ValueError('Independent exact source/script/chart review is required before generation')
        if sha256(Path(source['chart_path']).read_bytes()) != source['chart_sha256']:
            raise ValueError('Approved native chart changed')
    except (KeyError, ValueError, OSError):
        return _hold(root,job,'Current source, script, chart or independent approval binding failed')
    if not configuration.get('voice_verified') or not configuration.get('model_verified'):
        return _hold(root,job,'Existing voice and model must be verified in this account')
    if job['attempts'] >= 2:
        return _hold(root,job,'Two-attempt asset ceiling reached')
    if not configuration.get('quote'):
        return _hold(root,job,'Verified exact-request credit quote is missing')
    if not configuration.get('credential_ready'):
        return _hold(root,job,'ElevenLabs server credential is unavailable')
    private=jobs._folder(root)/'artifacts'/identifier
    private.mkdir(parents=True,exist_ok=True)
    job=jobs.update(root,identifier,job['version'],status='running',stage='speech',generation_status='running',
        attempts=job['attempts']+1,source_review=review,holds=[])
    try:
        audio=private/'narration.mp3'
        eleven.speech(root,inputs['script'],configuration['voice_id'],configuration['model_id'],
                      configuration.get('voice_settings',{}),configuration['quote'],audio)
        save_json(private/'source-review.json',review)
        compose(private/'pilot.mp4',audio,source['chart_path'],script=inputs['script'],
            title=source['title'],identity=source['on_screen'],chart_sha256=source['chart_sha256'],
            ffmpeg=configuration.get('ffmpeg','ffmpeg'),ffprobe=configuration.get('ffprobe','ffprobe'),
            font=configuration.get('font'))
        media={'narration.mp3':'audio/mpeg','pilot.mp4':'video/mp4','chart.png':'image/png',
               'transcript.txt':'text/plain','pilot.receipt.json':'application/json',
               'narration.receipt.json':'application/json','source-review.json':'application/json'}
        artifacts=[{'name':name,'relative_path':name,'media_type':mime,
            'sha256':sha256((private/name).read_bytes()),'review_status':'pending'} for name,mime in media.items()]
        return jobs.update(root,identifier,job['version'],status='generated',stage='media_review',
            generation_status='generated',review_status='pending',artifacts=artifacts,dispatch_status='disabled')
    except Exception:
        # A response may already exist. Never treat an unknown provider result as safe to repeat.
        ledger=Path(root)/'elevenlabs-budget.json'
        unknown=ledger.exists() and any(r['status']=='unknown_outcome' for r in load_json(ledger)['reservations'])
        return jobs.update(root,identifier,job['version'],status='held',stage='generation',
            generation_status='unknown_outcome' if unknown else 'failed',
            holds=['Inspect private generation receipt and media outcome before retry'],dispatch_status='disabled')
