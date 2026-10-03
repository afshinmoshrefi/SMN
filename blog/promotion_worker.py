"""One private source-bound generation job; never performs external dispatch."""
from pathlib import Path

from daily_briefing import digest
import elevenlabs_client as eleven
import promotion_jobs as jobs
from public_derivative import validate_derivative
from subscription_writer import load_json, save_json, sha256
from video_render import compose,compose_briefing


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
    if not configuration.get('generation_enabled'):
        return _hold(root,job,'Private generation is disabled')
    resolver=configuration.get('resolve_source')
    if not callable(resolver):
        return _hold(root,job,'Canonical source resolver is unavailable')
    if job['kind']=='derivative':
        from public_derivative_jobs import generate
        try:
            source=resolver(dict(job['inputs']))
            return generate(root,source['prepared'],configuration['writer_settings'],configuration['codex'],job_id=identifier)
        except jobs.Conflict:
            raise
        except Exception:
            current=load_json(jobs._path(root,identifier))
            return _hold(root,current,'Derivative source custody, explicit subscription settings or writer result requires inspection')
    if job['kind'] in {'social_export','substack_export'}:
        return _export(root,job,configuration,resolver)
    if job['kind'] in {'daily_briefing','daily_avatar'}:
        return _daily(root,job,configuration,resolver)
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
               'narration.receipt.json':'application/json','source-review.json':'application/json','captions.vtt':'text/vtt'}
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


def _export(root,job,configuration,resolver):
    import substack_export
    try:
        source=resolver(dict(job['inputs']));prepared,copy=source['prepared'],source['copy']
        p=prepared['provenance'];inputs=job['inputs']
        if any(inputs.get(k)!=p[v] for k,v in [('article_id','article_id'),('source_revision','revision'),('source_hash','article_sha256')]):
            raise ValueError('Export current source differs')
        result=substack_export.export(root,prepared,copy,job['actor'],job_id=job['id'])
        exported=load_json(jobs._path(root,result['id']))
        private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
        if result['id']!=job['id']:
            import shutil
            for item in exported['artifacts']:
                path,_=jobs.get_artifact(root,result['id'],item['name'])
                shutil.copyfile(path,private/item['relative_path'])
        return jobs.update(root,job['id'],job['version'],status='generated',stage='export_review',
            generation_status='generated',review_status='pending',artifacts=exported['artifacts'],dispatch_status='disabled')
    except (KeyError,ValueError,OSError):
        return _hold(root,job,'Current validated public derivative required for private export')


def _daily(root,job,configuration,resolver):
    from daily_briefing import inspect
    try:
        source=resolver(dict(job['inputs']))
        if 'source_bundle' in source and 'briefing' not in source:
            return _write_daily(root,job,configuration,source)
        briefing=source['briefing'];review=source['review']
        if job['inputs']['source_hash']!=digest(briefing) or source.get('active_revision',job['source_revision'])!=job['source_revision']:
            raise ValueError('Daily revision changed')
        check=inspect(briefing,review)
        if check['issues'] or check['review_status']!='approved':raise ValueError('Daily source review required')
        script=' '.join(b['text'] for b in briefing['script'])
        if job['inputs'].get('script',script)!=script:raise ValueError('Daily script changed')
    except (KeyError,ValueError,OSError):return _hold(root,job,'Exact current daily source/script approval is required')
    if job['kind']=='daily_avatar':
        return _avatar_import(root,job,configuration,briefing)
    for flag in ('voice_verified','model_verified','credential_ready'):
        if not configuration.get(flag):return _hold(root,job,'Daily ElevenLabs '+flag+' is not verified')
    if not configuration.get('quote'):return _hold(root,job,'Exact daily speech credit quote required')
    if job['attempts']>=2:return _hold(root,job,'Two-attempt daily ceiling reached')
    private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
    job=jobs.update(root,job['id'],job['version'],status='running',stage='speech',generation_status='running',attempts=job['attempts']+1)
    try:
        audio=private/'narration.mp3'
        eleven.speech(root,script,configuration['voice_id'],configuration['model_id'],configuration.get('voice_settings',{}),configuration['quote'],audio)
        compose_briefing(private/'briefing.mp4',audio,briefing,review,ffmpeg=configuration.get('ffmpeg','ffmpeg'),
            ffprobe=configuration.get('ffprobe','ffprobe'),font=configuration.get('font'))
        save_json(private/'source-review.json',review);save_json(private/'briefing.json',briefing)
        files={'narration.mp3':'audio/mpeg','briefing.mp4':'video/mp4','transcript.txt':'text/plain',
            'captions.vtt':'text/vtt','briefing.receipt.json':'application/json','source-review.json':'application/json','briefing.json':'application/json'}
        files.update({p.name:'image/png' for p in private.glob('card-*.png')})
        artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),'review_status':'pending'} for n,m in files.items()]
        return jobs.update(root,job['id'],job['version'],status='generated',stage='media_review',generation_status='generated',
            review_status='pending',artifacts=artifacts,dispatch_status='disabled')
    except Exception:
        ledger=Path(root)/'elevenlabs-budget.json'
        unknown=ledger.exists() and any(r['status']=='unknown_outcome' for r in load_json(ledger)['reservations'])
        return jobs.update(root,job['id'],job['version'],status='held',stage='generation',
            generation_status='unknown_outcome' if unknown else 'failed',holds=['Inspect private daily generation receipt before retry'])


def _avatar_import(root,job,configuration,briefing):
    """Ingest actual supported UI delivery while reusable-avatar API is unqualified.

    Paths/receipt are server-owned, never client chosen. This imports a real provider
    intro/outro, not a substitute avatar and not an invented generation success.
    """
    import shutil
    from video_render import probe
    if not configuration.get('avatar_ready') or not configuration.get('likeness_verified'):
        return _hold(root,job,'Personal ElevenLabs avatar reference/route requires account verification')
    delivery=configuration.get('avatar_delivery')
    if not delivery:return _hold(root,job,'Actual private ElevenLabs avatar delivery and provider receipt are required')
    try:
        path=Path(delivery['path']);receipt=delivery['receipt'];text=receipt['script']
        script=' '.join(b['text'] for b in briefing['script'])
        ledger=load_json(Path(root)/'elevenlabs-budget.json')
        reservation=ledger['reservations'][receipt['budget_reservation']]
        if (receipt.get('provider')!='elevenlabs' or receipt.get('briefing_sha256')!=digest(briefing)
                or receipt.get('part') not in {'intro','outro'} or not text or text not in script
                or receipt.get('voice_id')!=configuration['voice_id']
                or receipt.get('reference_sha256')!=configuration['presenter_reference_sha256']
                or not receipt.get('provider_generation_id') or not receipt.get('model_id')
                or reservation['status']!='received' or reservation['request_sha256']!=receipt['request_sha256']
                or receipt['video_sha256']!=sha256(path.read_bytes())):
            raise ValueError('Avatar custody or budget binding differs')
        metadata=probe(path,configuration.get('ffprobe','ffprobe'))
        if not 0<float(metadata['format']['duration'])<=8 or not any(s['codec_type']=='video' for s in metadata['streams']):
            raise ValueError('Intro/outro asset duration differs')
        private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
        destination=private/'avatar.mp4'
        if destination.exists() and sha256(destination.read_bytes())!=receipt['video_sha256']:
            raise ValueError('Avatar revision already differs')
        if not destination.exists():shutil.copyfile(path,destination)
        save_json(private/'avatar.receipt.json',dict(receipt,review_status='pending',publish=False))
        artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),
            'review_status':'pending'} for n,m in [('avatar.mp4','video/mp4'),('avatar.receipt.json','application/json')]]
        return jobs.update(root,job['id'],job['version'],status='generated',stage='avatar_review',
            generation_status='generated',review_status='pending',artifacts=artifacts,dispatch_status='disabled')
    except (KeyError,ValueError,OSError,IndexError):
        return _hold(root,job,'Actual avatar identity, source, private delivery or credit receipt failed validation')


def _write_daily(root,job,configuration,source):
    from briefing_sources import write_draft
    bundle=source['source_bundle']
    if (job['source_hash']!=digest(bundle) or source.get('active_revision',job['source_revision'])!=job['source_revision']):
        return _hold(root,job,'Actual daily source capture bundle/current revision differs')
    if job['attempts']>=2:return _hold(root,job,'Two-attempt daily writer ceiling reached')
    if not configuration.get('codex') or not configuration.get('writer_settings'):
        return _hold(root,job,'Explicit existing subscription writer configuration is required')
    private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
    output=private/'draft'
    if output.exists():return _hold(root,job,'Inspect existing immutable daily writer attempt before retry')
    job=jobs.update(root,job['id'],job['version'],status='running',stage='writing',generation_status='running',attempts=job['attempts']+1)
    try:
        result=write_draft(bundle,output,codex=configuration['codex'],**configuration['writer_settings'])
        files={'draft/writer-job/output.json':'application/json','draft/checks.json':'application/json',
               'draft/review/review.html':'text/html','draft/writer-job/receipt.json':'application/json'}
        artifacts=[{'name':Path(n).name,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),
            'review_status':'pending'} for n,m in files.items()]
        return jobs.update(root,job['id'],job['version'],status='held' if result['issues'] else 'generated',
            stage='editorial_review',generation_status='generated',review_status='pending',
            holds=result['issues'],artifacts=artifacts,dispatch_status='disabled')
    except Exception:
        return jobs.update(root,job['id'],job['version'],status='held',stage='writing',generation_status='failed',
            holds=['Inspect actual immutable daily subscription writer receipt/validation'])
