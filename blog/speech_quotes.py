"""Exact-request credit ceilings from an explicitly verified private account tariff.

Model token_cost_factor or a subscription balance alone is not a verified rate.
An operator must bind the selected account/model/voice/settings to retained pricing
and multiplier evidence. No defaults, live account calls, purchases or ledger reset.
"""
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_CEILING,InvalidOperation
from pathlib import Path
import re

from daily_briefing import digest,timestamp
from elevenlabs_client import request_identity
from subscription_writer import load_json,sha256


def _account_evidence(item,account,credential,verified):
    receipt=load_json(item['path'])
    observed=timestamp(receipt['observed_at'])
    if (receipt.get('authenticated') is not True or receipt.get('provider')!='elevenlabs'
            or receipt.get('account_sha256')!=account or receipt.get('credential_sha256')!=credential
            or not verified-timedelta(hours=24)<=observed<=verified):
        raise ValueError('Retained authenticated account evidence must bind the selected credential')


def quote(path,text,voice_id,model_id,settings,account_sha256,credential_sha256):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)) or path.stat().st_size>256*1024:
        raise ValueError('Verified private tariff file required')
    tariff=load_json(path);now=datetime.now(timezone.utc)
    verified=timestamp(tariff['verified_at']);until=timestamp(tariff['valid_until'])
    if (tariff.get('version')!=1 or tariff.get('provider')!='elevenlabs' or tariff.get('verified') is not True
            or not tariff.get('verified_by') or tariff.get('unit')!='credits'
            or tariff.get('billing_basis')!='input_unicode_codepoints'
            or tariff.get('model_id')!=model_id or tariff.get('voice_id')!=voice_id
            or tariff.get('settings_sha256')!=digest(settings)
            or not re.fullmatch('[a-f0-9]{64}',str(tariff.get('account_sha256','')))
            or tariff.get('account_sha256')!=account_sha256 or tariff.get('credential_sha256')!=credential_sha256
            or not re.fullmatch('[a-f0-9]{64}',str(credential_sha256))
            or not verified<=now<until or until-verified>timedelta(hours=24)):
        raise ValueError('Fresh selected-account/model/voice/settings tariff binding required')
    evidence=tariff['evidence']
    if set(evidence)!={'account_pricing','model','voice'}:raise ValueError('Account price, model and voice multiplier evidence required')
    for item in evidence.values():
        source=Path(item['path'])
        if (not source.is_absolute() or any(p.is_symlink() for p in (source,*source.parents))
                or source.stat().st_size>2*1024*1024 or sha256(source.read_bytes())!=item['sha256']):
            raise ValueError('Verified account pricing evidence changed')
    _account_evidence(evidence['account_pricing'],account_sha256,credential_sha256,verified)
    try:rate=Decimal(str(tariff['credits_per_character_upper_bound']))
    except InvalidOperation:raise ValueError('Verified credit ceiling required') from None
    if not rate.is_finite() or rate<=0 or not text:raise ValueError('Positive verified credit ceiling and script required')
    credits=int((rate*len(text)).to_integral_value(rounding=ROUND_CEILING))
    if not 0<credits<=5000:raise ValueError('Request exceeds private credit allowance')
    return {'request_sha256':request_identity(text,voice_id,model_id,settings),'credits':credits,
        'verified':True,'verified_at':tariff['verified_at'],'valid_until':tariff['valid_until'],
        'tariff_sha256':digest(tariff),'account_sha256':tariff['account_sha256'],'credential_sha256':credential_sha256,
        'billing_basis':'input_unicode_codepoints','input_characters':len(text),'rounding':'ceiling',
        'pricing_evidence_sha256':{k:v['sha256'] for k,v in evidence.items()}}


def avatar_quote(path,image_sha256,audio_sha256,resolution,duration,account_sha256,credential_sha256):
    """Verified API model/resolution ceiling; UI prices are not assumed API prices."""
    from elevenlabs_client import avatar_identity
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)) or path.stat().st_size>256*1024:raise ValueError('Private avatar tariff required')
    tariff=load_json(path);now=datetime.now(timezone.utc)
    verified=timestamp(tariff['verified_at']);until=timestamp(tariff['valid_until'])
    if (tariff.get('version')!=1 or tariff.get('provider')!='elevenlabs' or tariff.get('verified') is not True
            or not tariff.get('verified_by') or tariff.get('unit')!='credits'
            or tariff.get('billing_basis')!='ceil_input_audio_seconds' or tariff.get('api_route')!='/v1/flows/video'
            or tariff.get('model_id')!='creatify-aurora' or tariff.get('resolution')!=resolution
            or tariff.get('reference_sha256')!=image_sha256
            or not re.fullmatch('[a-f0-9]{64}',str(tariff.get('account_sha256','')))
            or tariff.get('account_sha256')!=account_sha256 or tariff.get('credential_sha256')!=credential_sha256
            or not re.fullmatch('[a-f0-9]{64}',str(credential_sha256))
            or not verified<=now<until or until-verified>timedelta(hours=24)):
        raise ValueError('Fresh selected-account/model/resolution avatar ceiling required')
    evidence=tariff['evidence']
    if set(evidence)!={'account_pricing','model'}:raise ValueError('Verified API account/model credit evidence required')
    for item in evidence.values():
        source=Path(item['path'])
        if (not source.is_absolute() or any(p.is_symlink() for p in (source,*source.parents))
                or source.stat().st_size>2*1024*1024 or sha256(source.read_bytes())!=item['sha256']):raise ValueError('Avatar price evidence changed')
    _account_evidence(evidence['account_pricing'],account_sha256,credential_sha256,verified)
    try:rate=Decimal(str(tariff['credits_per_second_upper_bound']));seconds=Decimal(str(duration))
    except InvalidOperation:raise ValueError('Verified bounded avatar rate required') from None
    if not rate.is_finite() or rate<=0 or not seconds.is_finite() or not 0<seconds<=8:raise ValueError('Verified brief avatar ceiling required')
    credits=int((rate*seconds.to_integral_value(rounding=ROUND_CEILING)).to_integral_value(rounding=ROUND_CEILING))
    if not 0<credits<=5000:raise ValueError('Avatar credit ceiling exceeds private allowance')
    return {'request_sha256':avatar_identity(image_sha256,audio_sha256,resolution),'credits':credits,
        'verified':True,'verified_at':tariff['verified_at'],'valid_until':tariff['valid_until'],
        'tariff_sha256':digest(tariff),'account_sha256':tariff['account_sha256'],'credential_sha256':credential_sha256,'rounding':'ceil_seconds_then_ceil_credits',
        'pricing_evidence_sha256':{k:v['sha256'] for k,v in evidence.items()}}
