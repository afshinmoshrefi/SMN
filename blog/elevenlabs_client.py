"""Bounded ElevenLabs API speech generation with durable unknown-outcome holds.

Credentials are read only from a server-owned environment variable. No credential,
provider error body or signed media URL is written to a receipt or error string.
"""
import json
import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.parse import urlsplit

from daily_briefing import digest
from promotion_jobs import locked
from subscription_writer import load_json, save_json, sha256, utc_now


class Held(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Held('Unexpected vendor redirect; credential transmission stopped')


def _request(endpoint, payload=None, key_env='ELEVENLABS_API_KEY', binary=False, with_headers=False):
    secret = os.environ.get(key_env)
    if not secret:
        raise Held('ElevenLabs credential is absent from server environment')
    req = Request('https://api.elevenlabs.io' + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'xi-api-key': secret, 'Content-Type': 'application/json'})
    try:
        with build_opener(_NoRedirect()).open(req, timeout=90) as response:
            body = response.read(32 * 1024 * 1024 + 1)
            if len(body) > 32 * 1024 * 1024:
                raise Held('Provider response exceeded private media bound')
            if binary:
                return body, {key: response.headers.get(key) for key in ('request-id', 'history-item-id', 'character-cost')}
            decoded = json.loads(body)
            if with_headers:
                return decoded, {key: response.headers.get(key) for key in ('request-id', 'history-item-id', 'character-cost')}
            return decoded
    except HTTPError as exc:
        raise Held('ElevenLabs HTTP status ' + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise Held('ElevenLabs transport outcome unknown; reconcile before retry') from None


def voices(key_env='ELEVENLABS_API_KEY'):
    data = _request('/v2/voices?page_size=100', key_env=key_env)
    return {'voices': [{key: item.get(key) for key in ('voice_id', 'name', 'category')}
                       for item in data.get('voices', [])], 'has_more': data.get('has_more', False)}


def request_identity(text, voice_id, model_id, settings):
    return digest({'text': text, 'voice_id': voice_id, 'model_id': model_id, 'settings': settings})


def _reserve(root, identity, quote):
    if quote.get('request_sha256') != identity or quote.get('verified') is not True or not quote.get('verified_at'):
        raise Held('A verified exact-request credit quote is required')
    credits = quote.get('credits')
    if type(credits) not in (int, float) or not 0 < credits <= 5000:
        raise Held('Quoted credits must fit the private prototype ceiling')
    path = Path(root) / 'elevenlabs-budget.json'
    with locked(root):
        ledger = load_json(path) if path.exists() else {'ceiling': 5000, 'reservations': []}
        attempts = [r for r in ledger['reservations'] if r['request_sha256'] == identity]
        if any(r['status'] in {'submitted', 'unknown_outcome'} for r in attempts):
            raise Held('Existing provider outcome needs reconciliation')
        if len(attempts) >= 2:
            raise Held('Two-attempt generation ceiling reached')
        if sum(max(r['quoted_credits'],r.get('charged_credits',0)) for r in ledger['reservations']) + credits > min(5000, ledger['ceiling']):
            raise Held('Private prototype credit ceiling would be exceeded')
        token = len(ledger['reservations'])
        ledger['reservations'].append({'request_sha256': identity, 'quoted_credits': credits,
            'status': 'submitted', 'submitted_at': utc_now()})
        save_json(path, ledger)
    return token


def _finish(root, token, status, receipt=None):
    with locked(root):
        path = Path(root) / 'elevenlabs-budget.json'; ledger = load_json(path)
        ledger['reservations'][token].update(status=status, receipt=receipt, updated_at=utc_now())
        if receipt:
            charged=receipt.get('actual_credits',receipt.get('provider_identifiers',{}).get('character-cost'))
            try:
                charged=float(charged)
                if 0<=charged<float('inf'):ledger['reservations'][token]['charged_credits']=charged
            except (ValueError,TypeError):pass
        save_json(path, ledger)


def speech(root, text, voice_id, model_id, settings, quote, output, *, key_env='ELEVENLABS_API_KEY'):
    if not text.strip() or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', voice_id) or not model_id:
        raise Held('Verified voice, model and nonempty script are required')
    if not os.environ.get(key_env):
        raise Held('ElevenLabs credential is absent from server environment')
    identity = request_identity(text, voice_id, model_id, settings)
    output = Path(output)
    receipt_path = output.with_suffix('.receipt.json')
    if output.exists() and receipt_path.exists():
        old = load_json(receipt_path)
        if old['request_sha256'] == identity and old['audio_sha256'] == sha256(output.read_bytes()):
            return old
        raise Held('Existing audio differs; use a new private asset revision')
    if output.exists() or receipt_path.exists():
        raise Held('Partial audio outcome requires reconciliation')
    token = _reserve(root, identity, quote)
    try:
        response, headers = _request('/v1/text-to-speech/' + voice_id + '/with-timestamps?output_format=mp3_44100_128',
            {'text': text, 'model_id': model_id, 'voice_settings': settings}, key_env=key_env, with_headers=True)
        audio = base64.b64decode(response['audio_base64'], validate=True)
        if not audio:
            raise Held('Provider returned no audio')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as file:
            file.write(audio)
        receipt = {'status': 'audio_received_pending_qa', 'request_sha256': identity,
            'audio_sha256': sha256(audio), 'voice_id': voice_id, 'model_id': model_id,
            'settings': settings, 'quoted_credits': quote['credits'], 'provider_identifiers': headers,
            'created_at': utc_now(), 'review_status': 'pending', 'publish': False}
        save_json(receipt_path, receipt)
        _finish(root, token, 'received', receipt)
        # Preserve paid audio even if optional provider alignment is absent. A
        # missing timing file holds composition without silently buying a retry.
        from speech_timing import provider_timing
        try:
            timing = provider_timing(text, audio, response.get('alignment'))
            save_json(output.with_suffix('.alignment.json'), timing)
        except ValueError:
            receipt['timing_status'] = 'needs_alignment_review'
            save_json(receipt_path, receipt)
        return receipt
    except Exception:
        _finish(root, token, 'unknown_outcome')
        raise


def avatar_identity(image_hash, audio_hash, resolution):
    return digest({'model_id':'creatify-aurora','image_sha256':image_hash,
                   'audio_sha256':audio_hash,'resolution':resolution})


def avatar(root, image, audio, resolution, quote, output, *, key_env='ELEVENLABS_API_KEY'):
    """Submit once, then reconcile the same asynchronous generation on later ticks.

    Uses the documented inline media shape. No reusable-avatar API is assumed.
    Caller verifies likeness, approved speech and account capabilities beforehand.
    """
    image, audio, output = Path(image), Path(audio), Path(output)
    mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}
    if resolution not in {'480p','720p'} or image.suffix.lower() not in mime or audio.suffix.lower() not in {'.mp3','.wav'}:
        raise Held('Verified Creatify image/audio format and resolution required')
    image_bytes, audio_bytes=image.read_bytes(),audio.read_bytes()
    if not all(0<len(b)<=25*1024*1024 for b in (image_bytes,audio_bytes)):
        raise Held('Avatar inputs exceed the documented inline media bound')
    identity=avatar_identity(sha256(image_bytes),sha256(audio_bytes),resolution)
    state_path=output.with_suffix('.provider.json');receipt_path=output.with_suffix('.receipt.json')
    if receipt_path.exists() and output.exists():
        receipt=load_json(receipt_path)
        if receipt['request_sha256']==identity and receipt['video_sha256']==sha256(output.read_bytes()):return receipt
        raise Held('Existing avatar differs; use a new private revision')
    if not os.environ.get(key_env):raise Held('ElevenLabs credential is absent from server environment')
    if not state_path.exists():
        if output.exists() or receipt_path.exists():raise Held('Partial avatar delivery requires reconciliation')
        token=_reserve(root,identity,quote)
        state={'request_sha256':identity,'budget_reservation':token,'status':'submitting',
               'last_checked_at':utc_now(),'submitted_at':utc_now(),'poll_interval':10}
        output.parent.mkdir(parents=True,exist_ok=True);save_json(state_path,state)
        payload={'model_id':'creatify-aurora','resolution':resolution,
            'image':{'type':'inline_base64','mime_type':mime[image.suffix.lower()],
                     'content_base64':base64.b64encode(image_bytes).decode()},
            'audio':{'type':'inline_base64','mime_type':'audio/mpeg' if audio.suffix.lower()=='.mp3' else 'audio/wav',
                     'content_base64':base64.b64encode(audio_bytes).decode()}}
        try:
            response=_request('/v1/flows/video',payload,key_env=key_env)
            identifier=response['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier) or response.get('status')!='pending':
                raise Held('Invalid provider generation receipt')
            state.update(provider_generation_id=identifier,status='pending');save_json(state_path,state)
            return state
        except Exception:
            _finish(root,token,'unknown_outcome');raise
    state=load_json(state_path)
    if state['request_sha256']!=identity or not state.get('provider_generation_id'):
        raise Held('Avatar submission requires manual reconciliation; do not resubmit')
    if state.get('status')=='failed':raise Held('Provider avatar failed; inspect immutable receipt')
    last=datetime.fromisoformat(state['last_checked_at'].replace('Z','+00:00'))
    started=datetime.fromisoformat(state['submitted_at'].replace('Z','+00:00'))
    if (datetime.now(timezone.utc)-started).total_seconds()>1800:
        raise Held('Avatar polling deadline reached; reconcile known generation before continuing')
    if (datetime.now(timezone.utc)-last).total_seconds()<state.get('poll_interval',10):return state
    result=_request('/v1/flows/video/'+state['provider_generation_id'],key_env=key_env)
    status=result.get('status')
    if status not in {'pending','generating','completed','failed'}:raise Held('Unrecognized provider avatar state')
    state.update(status=status,last_checked_at=utc_now(),poll_interval=min(60,state.get('poll_interval',10)*2));save_json(state_path,state)
    if status=='failed':
        _finish(root,state['budget_reservation'],'failed');raise Held('Provider avatar failed; inspect immutable receipt')
    if status!='completed':return state
    url=urlsplit(result.get('content_url',''))
    if (url.scheme!='https' or url.hostname!='storage.googleapis.com' or url.username or url.password
            or url.port not in (None,443) or result.get('content_mime_type')!='video/mp4'):
        raise Held('Unqualified provider media download location or format')
    # No API credential is ever attached to the provider's signed storage URL.
    with build_opener(_NoRedirect()).open(Request(url.geturl()),timeout=90) as response:
        video=response.read(32*1024*1024+1)
    if not 0<len(video)<=32*1024*1024:raise Held('Provider avatar media exceeded private bound')
    with output.open('xb') as file:file.write(video)
    receipt={'status':'avatar_received_pending_qa','provider':'elevenlabs','model_id':'creatify-aurora',
        'request_sha256':identity,'provider_generation_id':state['provider_generation_id'],
        'budget_reservation':state['budget_reservation'],'reference_sha256':sha256(image_bytes),
        'audio_sha256':sha256(audio_bytes),'video_sha256':sha256(video),'resolution':resolution,
        'review_status':'pending','publish':False,'created_at':utc_now()}
    save_json(receipt_path,receipt);_finish(root,state['budget_reservation'],'received',receipt)
    return receipt
