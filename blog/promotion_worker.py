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


def _speech_quote(configuration,script):
    from datetime import datetime,timezone,timedelta
    identity=eleven.request_identity(script,configuration['voice_id'],configuration['model_id'],configuration.get('voice_settings',{}))
    resolver=configuration.get('resolve_speech_quote')
    if callable(resolver):
        quote=resolver({'request_sha256':identity,'text':script,'voice_id':configuration['voice_id'],
                    'model_id':configuration['model_id'],'settings':configuration.get('voice_settings',{})})
    elif configuration.get('speech_tariff_path'):
        from speech_quotes import quote as tariff_quote
        account,credential=_quote_account(configuration)
        quote=tariff_quote(configuration['speech_tariff_path'],script,configuration['voice_id'],configuration['model_id'],configuration.get('voice_settings',{}),account,credential)
    else:quote=configuration.get('quote')
    return _fresh_quote(quote,identity,configuration)


def _quote_account(configuration):
    import os,re
    account=configuration.get('elevenlabs_account_sha256')
    if not re.fullmatch('[a-f0-9]{64}',str(account)) or not os.environ.get('ELEVENLABS_API_KEY'):
        raise ValueError('Verified selected ElevenLabs account and current server credential required')
    return account,sha256(os.environ['ELEVENLABS_API_KEY'].encode())


def _fresh_quote(quote,identity,configuration):
    from datetime import datetime,timezone,timedelta
    if not isinstance(quote,dict) or quote.get('verified') is not True or quote.get('request_sha256')!=identity:
        raise ValueError('Verified exact-request speech quote required')
    account,credential=_quote_account(configuration)
    if quote.get('account_sha256')!=account or quote.get('credential_sha256')!=credential:
        raise ValueError('Credit ceiling belongs to a different account or server credential')
    stamp=datetime.fromisoformat(quote['verified_at'].replace('Z','+00:00'));now=datetime.now(timezone.utc)
    if stamp.utcoffset() is None or not now-timedelta(hours=24)<=stamp<=now+timedelta(minutes=5):
        raise ValueError('Speech quote must be freshly verified')
    if quote.get('valid_until'):
        until=datetime.fromisoformat(quote['valid_until'].replace('Z','+00:00'))
        if until.utcoffset() is None or until<=now:raise ValueError('Speech quote expired')
    return quote


def run_one(root, identifier, configuration):
    """Configuration/resolver/quotes are server-owned, never taken from HTTP input.

    resolve_source(inputs) returns prepared, copy, source_review, chart_path,
    chart_sha256, on_screen and title. It must resolve canonical current revision.
    """
    job=load_json(jobs._path(root,identifier))
    if job['status'] in {'generated','reviewed','canceled','superseded'} or jobs.is_paused(root,job['kind']):
        return jobs.summary(job)
    if (job['status'] == 'running' and not (job['kind']=='daily_avatar' and job['stage']=='avatar_provider')) or job['generation_status'] == 'unknown_outcome':
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
    if job['kind']=='article_script':
        from public_derivative_jobs import generate_script
        try:
            source=resolver(dict(job['inputs']))
            prepared,copy,review=source['prepared'],source['copy'],source['source_review']
            if review.get('status')!='approved' or review.get('copy_sha256')!=digest(copy) or review.get('prepared_sha256')!=digest(prepared):
                raise ValueError('Exact approved original copy required')
            return generate_script(root,prepared,copy,configuration['writer_settings'],configuration['codex'],job_id=identifier)
        except jobs.Conflict:raise
        except Exception:
            current=load_json(jobs._path(root,identifier))
            return _hold(root,current,'Original approved public copy, current source or immutable script writer result requires inspection')
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
        video=copy.get('video')
        script_review=review
        if inputs.get('script_job_id'):
            from public_derivative import validate_video_script
            video=source['video_script'];script_review=source['video_script_review']
            validate_video_script(video,prepared,copy)
            if (script_review.get('generation_job')!=inputs['script_job_id'] or script_review.get('script_sha256')!=digest(video)
                    or script_review.get('copy_sha256')!=digest(copy) or script_review.get('prepared_sha256')!=digest(prepared)
                    or script_review.get('provenance')!=provenance or script_review.get('status')!='approved'
                    or not script_review.get('reviewer') or not script_review.get('reviewed_at')
                    or not script_review.get('checks') or any(v is not True for v in script_review['checks'].values())):
                raise ValueError('Independent backfilled script review differs')
        if (inputs.get('payload_sha256') != digest(copy) or inputs.get('script') != video['narration']['text']
                or inputs.get('chart_id') != video['native_chart_id']
                or source['on_screen'] != video['on_screen']['text']):
            raise ValueError('Script differs from source-bound copy')
        if (review.get('status') != 'approved' or not review.get('reviewer') or not review.get('reviewed_at')
                or review.get('copy_sha256') != digest(copy) or review.get('prepared_sha256') != digest(prepared)
                or review.get('provenance') != provenance or script_review.get('chart_sha256') != source['chart_sha256']
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
    try:quote=_speech_quote(configuration,inputs['script'])
    except (ValueError,KeyError,TypeError,OSError):return _hold(root,job,'Fresh verified exact-request speech credit quote is unavailable')
    if not configuration.get('credential_ready'):
        return _hold(root,job,'ElevenLabs server credential is unavailable')
    private=jobs._folder(root)/'artifacts'/identifier
    private.mkdir(parents=True,exist_ok=True)
    job=jobs.update(root,identifier,job['version'],status='running',stage='speech',generation_status='running',
        attempts=job['attempts']+1,source_review=review,holds=[])
    try:
        audio=private/'narration.mp3'
        eleven.speech(root,inputs['script'],configuration['voice_id'],configuration['model_id'],
                      configuration.get('voice_settings',{}),quote,audio)
        save_json(private/'source-review.json',review)
        if inputs.get('script_job_id'):save_json(private/'script-review.json',script_review)
        compose(private/'pilot.mp4',audio,source['chart_path'],script=inputs['script'],
            title=source['title'],identity=source['on_screen'],chart_sha256=source['chart_sha256'],
            ffmpeg=configuration.get('ffmpeg','ffmpeg'),ffprobe=configuration.get('ffprobe','ffprobe'),
            font=configuration.get('font'))
        media={'narration.mp3':'audio/mpeg','pilot.mp4':'video/mp4','chart.png':'image/png',
               'transcript.txt':'text/plain','pilot.receipt.json':'application/json',
               'narration.receipt.json':'application/json','narration.alignment.json':'application/json','source-review.json':'application/json','captions.vtt':'text/vtt'}
        if inputs.get('script_job_id'):media['script-review.json']='application/json'
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
        return substack_export.export(root,prepared,copy,job['actor'],job_id=job['id'],
            public_origin=configuration.get('public_origin'))
    except jobs.Conflict:
        raise
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
        if configuration.get('avatar_request'):
            return _avatar_generate(root,job,configuration,briefing)
        return _avatar_import(root,job,configuration,briefing)
    for flag in ('voice_verified','model_verified','credential_ready'):
        if not configuration.get(flag):return _hold(root,job,'Daily ElevenLabs '+flag+' is not verified')
    try:quote=_speech_quote(configuration,script)
    except (ValueError,KeyError,TypeError,OSError):return _hold(root,job,'Fresh verified exact-request daily speech quote is unavailable')
    if job['attempts']>=2:return _hold(root,job,'Two-attempt daily ceiling reached')
    private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
    job=jobs.update(root,job['id'],job['version'],status='running',stage='speech',generation_status='running',attempts=job['attempts']+1)
    try:
        audio=private/'narration.mp3'
        eleven.speech(root,script,configuration['voice_id'],configuration['model_id'],configuration.get('voice_settings',{}),quote,audio)
        compose_briefing(private/'briefing.mp4',audio,briefing,review,ffmpeg=configuration.get('ffmpeg','ffmpeg'),
            ffprobe=configuration.get('ffprobe','ffprobe'),font=configuration.get('font'))
        save_json(private/'source-review.json',review);save_json(private/'briefing.json',briefing)
        files={'narration.mp3':'audio/mpeg','briefing.mp4':'video/mp4','transcript.txt':'text/plain',
            'captions.vtt':'text/vtt','narration.receipt.json':'application/json','narration.alignment.json':'application/json','briefing.receipt.json':'application/json','source-review.json':'application/json','briefing.json':'application/json'}
        files.update({p.name:'image/png' for p in private.glob('card-*.png')})
        artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),'review_status':'pending'} for n,m in files.items()]
        return jobs.update(root,job['id'],job['version'],status='generated',stage='media_review',generation_status='generated',
            review_status='pending',artifacts=artifacts,dispatch_status='disabled')
    except Exception:
        ledger=Path(root)/'elevenlabs-budget.json'
        unknown=ledger.exists() and any(r['status']=='unknown_outcome' for r in load_json(ledger)['reservations'])
        return jobs.update(root,job['id'],job['version'],status='held',stage='generation',
            generation_status='unknown_outcome' if unknown else 'failed',holds=['Inspect private daily generation receipt before retry'])


def _avatar_generate(root,job,configuration,briefing):
    from video_render import probe
    for flag in ('avatar_ready','likeness_verified','voice_verified','credential_ready','flows_plan_verified'):
        if not configuration.get(flag):return _hold(root,job,'Personal avatar '+flag+' is not verified')
    request=configuration['avatar_request'];private=jobs._folder(root)/'artifacts'/job['id']
    try:
        from briefing_media import base_media,segment_audio
        image=Path(request['image_path']);part=request.get('part','intro')
        if sha256(image.read_bytes())!=configuration['presenter_reference_sha256']:
            raise ValueError('Verified presenter reference differs')
        base=base_media(root,job['inputs'].get('briefing_media_job_id',configuration.get('briefing_media_job_id')),briefing,configuration['voice_id'],configuration.get('ffprobe','ffprobe'))
        text=request.get('script') or base['timing']['segments'][0 if part=='intro' else -1]['text']
        audio=private/'presenter-segment.wav'
        segment=segment_audio(base,part,text,audio,configuration.get('ffmpeg','ffmpeg'))
        quote_path=private/'avatar.quote.json'
        identity=eleven.avatar_identity(sha256(image.read_bytes()),segment['audio_sha256'],request['resolution'])
        if (private/'provider-avatar.provider.json').exists():
            quote=load_json(quote_path)
            if quote.get('request_sha256')!=identity:raise ValueError('Pending avatar credit reservation differs')
        else:
            quote=request.get('quote')
            if configuration.get('avatar_tariff_path'):
                from speech_quotes import avatar_quote
                account,credential=_quote_account(configuration)
                quote=avatar_quote(configuration['avatar_tariff_path'],sha256(image.read_bytes()),segment['audio_sha256'],request['resolution'],segment['end']-segment['start'],account,credential)
            quote=_fresh_quote(quote,identity,configuration)
            if quote_path.exists() and load_json(quote_path)!=quote:raise ValueError('Immutable avatar quote differs')
            save_json(quote_path,quote)
        if job['status']!='running':
            if job['attempts']>=2:raise ValueError('Avatar attempt ceiling reached')
            job=jobs.update(root,job['id'],job['version'],status='running',stage='avatar_provider',
                generation_status='running',attempts=job['attempts']+1,holds=[])
        result=eleven.avatar(root,image,audio,request['resolution'],quote,private/'provider-avatar.mp4')
        if result['status']!='avatar_received_pending_qa':return jobs.summary(job)
        result=dict(result,briefing_sha256=digest(briefing),script=text,part=part,voice_id=base['voice_id'],base_job_id=base['id'],master_audio_sha256=base['audio_sha256'])
        return _avatar_import(root,job,dict(configuration,avatar_delivery={'path':private/'provider-avatar.mp4','receipt':result}),briefing)
    except Exception:
        current=load_json(jobs._path(root,job['id']))
        ledger=Path(root)/'elevenlabs-budget.json'
        unknown=ledger.exists() and any(r['status']=='unknown_outcome' for r in load_json(ledger)['reservations'])
        return jobs.update(root,job['id'],current['version'],status='held',stage='avatar_provider',
            generation_status='unknown_outcome' if unknown else 'held',
            holds=['Inspect exact avatar account, reference, speech, quote or provider receipt before continuing'])


def _avatar_import(root,job,configuration,briefing):
    """Ingest actual supported UI delivery while reusable-avatar API is unqualified.

    Paths/receipt are server-owned, never client chosen. This imports a real provider
    intro/outro, not a substitute avatar and not an invented generation success.
    """
    import shutil,subprocess
    from video_render import probe
    from briefing_media import base_media,segment_audio,compose_avatar
    if not configuration.get('avatar_ready') or not configuration.get('likeness_verified'):
        return _hold(root,job,'Personal ElevenLabs avatar reference/route requires account verification')
    delivery=configuration.get('avatar_delivery')
    if not delivery:return _hold(root,job,'Actual private ElevenLabs avatar delivery and provider receipt are required')
    try:
        path=Path(delivery['path']);receipt=delivery['receipt'];text=receipt['script']
        script=' '.join(b['text'] for b in briefing['script'])
        base=base_media(root,job['inputs'].get('briefing_media_job_id',configuration.get('briefing_media_job_id')),briefing,configuration['voice_id'],configuration.get('ffprobe','ffprobe'))
        private=jobs._folder(root)/'artifacts'/job['id'];private.mkdir(parents=True,exist_ok=True)
        segment=segment_audio(base,receipt['part'],text,private/'presenter-segment.wav',configuration.get('ffmpeg','ffmpeg'))
        ledger=load_json(Path(root)/'elevenlabs-budget.json')
        reservation=ledger['reservations'][receipt['budget_reservation']]
        if (receipt.get('provider')!='elevenlabs' or receipt.get('briefing_sha256')!=digest(briefing)
                or receipt.get('part') not in {'intro','outro'} or not text
                or receipt.get('base_job_id')!=base['id'] or receipt.get('master_audio_sha256')!=base['audio_sha256']
                or receipt.get('audio_sha256')!=segment['audio_sha256']
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
        compose_avatar(private/'briefing.mp4',base,destination,segment,ffmpeg=configuration.get('ffmpeg','ffmpeg'),ffprobe=configuration.get('ffprobe','ffprobe'))
        files={'avatar.mp4':'video/mp4','avatar.receipt.json':'application/json','briefing.mp4':'video/mp4',
            'briefing.receipt.json':'application/json','narration.mp3':'audio/mpeg','narration.alignment.json':'application/json',
            'narration.receipt.json':'application/json','captions.vtt':'text/vtt','transcript.txt':'text/plain','presenter-segment.segment.json':'application/json'}
        artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),
            'review_status':'pending'} for n,m in files.items()]
        return jobs.update(root,job['id'],job['version'],status='generated',stage='avatar_review',
            generation_status='generated',review_status='pending',artifacts=artifacts,dispatch_status='disabled')
    except (KeyError,ValueError,OSError,IndexError,subprocess.CalledProcessError):
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
