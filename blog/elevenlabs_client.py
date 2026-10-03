"""Bounded ElevenLabs API speech generation with durable unknown-outcome holds.

Credentials are read only from a server-owned environment variable. No credential,
provider error body or signed media URL is written to a receipt or error string.
"""
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from daily_briefing import digest
from promotion_jobs import locked
from subscription_writer import load_json, save_json, sha256, utc_now


class Held(RuntimeError):
    pass


def _request(endpoint, payload=None, key_env='ELEVENLABS_API_KEY', binary=False):
    secret = os.environ.get(key_env)
    if not secret:
        raise Held('ElevenLabs credential is absent from server environment')
    req = Request('https://api.elevenlabs.io' + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'xi-api-key': secret, 'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=90) as response:
            body = response.read(32 * 1024 * 1024)
            if binary:
                return body, {key: response.headers.get(key) for key in ('request-id', 'history-item-id', 'character-cost')}
            return json.loads(body)
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
        if sum(r['quoted_credits'] for r in ledger['reservations']) + credits > min(5000, ledger['ceiling']):
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
        audio, headers = _request('/v1/text-to-speech/' + voice_id + '?output_format=mp3_44100_128',
            {'text': text, 'model_id': model_id, 'voice_settings': settings}, key_env=key_env, binary=True)
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
        return receipt
    except Exception:
        _finish(root, token, 'unknown_outcome')
        raise
