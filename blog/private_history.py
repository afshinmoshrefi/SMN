"""Private, pure derivation of inclusive annual windows from adjusted OHLC.

The trusted adapter supplies a reviewed exchange-session calendar and adjusted
series identity. This function neither downloads data nor authenticates calendar
or provider declarations. Output is percentages, dates and hashes, never prices.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException
import hashlib
import json
import re

from article_evidence import finite_number, inclusive_window, round_percent
from cohort_policy import instrument_key


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('ISO calendar date required')
    return date.fromisoformat(value)


def derive_history_panel(ohlc, session_manifest, *, instrument, anchor_date,
                         days, as_of, dataset_sha256, source_ref, requested_years=40):
    """Derive fixed annual rows using first/last sessions *inside* each window.

    Entry is adjusted CLOSE of the first session on/after the inclusive start;
    exit is adjusted CLOSE of the last session on/before the inclusive end.
    Excursions inspect subsequent sessions only, including exit day. Entry's
    zero return is an actual boundary value, so MFE>=0 and MAE<=0. A one-session
    window returns zero with no inferred intraday opportunity after its close.

    session_manifest requires calendar_id/start_date/end_date/sessions,
    expected_latest_date/source_ref. The adapter must independently generate
    the complete calendar, including exchange closures and the as-of close.
    Hashing its dates records inputs; it is not calendar authentication.
    """
    result={'schema_version':1,'mode':'private_preview','publishable':False,
            'status':'held','observations':[], 'coverage':{},'issues':[],
            'accounting':{'entry':'adjusted_close_first_session_within_inclusive_window',
                'exit':'adjusted_close_last_session_within_inclusive_window',
                'excursions':'entry zero plus high/low of sessions strictly after entry through exit',
                'return_basis':'provider_adjusted_price','costs_included':False,
                'extrema_timing_known':False}}
    def issue(code, **detail):
        result['issues'].append({'code':code,**detail})
    try:
        now=datetime.fromisoformat(as_of.replace('Z','+00:00')) if isinstance(as_of,str) else as_of
        if not isinstance(now,datetime) or now.tzinfo is None: raise ValueError('aware audit clock required')
        now=now.astimezone(timezone.utc)
        anchor=_date(anchor_date); cutoff=min(anchor,now.date())
        window=inclusive_window(anchor_date,days)
        if (anchor.month,anchor.day)==(2,29):raise ValueError('explicit leap-anniversary rule required')
        if type(requested_years) is not int or not 20<=requested_years<=250:raise ValueError('bounded fixed history required')
        if not re.fullmatch('[0-9a-f]{64}',str(dataset_sha256)) or not isinstance(source_ref,str) or not source_ref.strip():raise ValueError('dataset provenance required')
        if not isinstance(instrument,dict) or any(not str(instrument.get(k) or '').strip() for k in ('resource_id','provider','exchange','symbol','series_id')):raise ValueError('full series identity required')
        if not isinstance(ohlc,list) or not ohlc or len(ohlc)>100000:raise ValueError('bounded nonempty OHLC required')
        if not isinstance(session_manifest,dict) or not str(session_manifest.get('calendar_id') or '').strip() or not str(session_manifest.get('source_ref') or '').strip():raise ValueError('reviewed calendar required')
        sessions=[_date(v) for v in session_manifest['sessions']]
        if not sessions or len(sessions)>100000 or sessions!=sorted(set(sessions)):raise ValueError('unique ordered sessions required')
        cal_start,cal_end=_date(session_manifest['start_date']),_date(session_manifest['end_date'])
        latest=_date(session_manifest['expected_latest_date'])
        if latest not in sessions or latest>now.date() or cal_end<now.date() or cal_start>sessions[0] or cal_end<sessions[-1]:raise ValueError('calendar coverage/freshness invalid')
        # In the supported US exchange scope, a session before today's UTC
        # date has completed. A supplied older boundary cannot hide it.
        prior=[d for d in sessions if d<now.date()]
        if prior and latest<max(prior):raise ValueError('stale completed-session boundary')
        if now.date() in sessions:
            # Audit-day close must be supplied even if the caller claims the
            # previous session is latest. Otherwise it can conceal today's
            # completed but missing data by claiming an older boundary.
            close_text=session_manifest.get('audit_session_close',session_manifest.get('expected_latest_close'))
            close=datetime.fromisoformat(close_text.replace('Z','+00:00'))
            if close.tzinfo is None or close.astimezone(timezone.utc).date()!=now.date():raise ValueError('audit-day close is unknown')
            expected_boundary=now.date() if close.astimezone(timezone.utc)<=now else max(prior)
            if latest!=expected_boundary:raise ValueError('incorrect as-of completed-session boundary')
        last=cutoff.year
        while _date(inclusive_window(date(last,anchor.month,anchor.day).isoformat(),days)['end_date'])>=cutoff:last-=1
        first=last-requested_years+1
        required_start=date(first,anchor.month,anchor.day)
        if first<1 or cal_start>required_start:raise ValueError('calendar does not cover requested windows')
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        issue('INVALID_HISTORY_INPUT_OR_CALENDAR');return result
    rows={}; counts=Counter(); invalid=[]
    for r in ohlc:
        try:
            d=_date(r['date']); counts[d]+=1
            vals={k:finite_number(r.get(k)) for k in ('open','high','low','close')}
            if any(v is None or v<=0 for v in vals.values()):raise ValueError('nonfinite/nonpositive')
            if vals['high']<max(vals['open'],vals['close'],vals['low']) or vals['low']>min(vals['open'],vals['close']):raise ValueError('OHLC ordering')
            rows[d]={k:Decimal(str(r[k])) for k in vals}
        except (ValueError,KeyError,TypeError,AttributeError):
            invalid.append(str(r.get('date')) if isinstance(r,dict) else None)
    if invalid:issue('INVALID_OHLC_VALUES',dates=invalid)
    duplicate=sorted(d.isoformat() for d,n in counts.items() if n>1)
    if duplicate:issue('DUPLICATE_OHLC_DATES',dates=duplicate)
    if not rows:issue('NO_VALID_OHLC');return result
    first_source=min(rows); last_source=max(rows)
    expected_set=set(sessions)
    unexpected=sorted(d.isoformat() for d in rows if cal_start<=d<=cal_end and d not in expected_set)
    if unexpected:issue('UNEXPECTED_SESSION_DATES',dates=unexpected)
    if last_source!=latest:issue('HISTORY_FRESHNESS_MISMATCH',last_observation_date=str(last_source),expected_latest_date=str(latest))
    # Full retained period is checked, not merely entry/exit endpoints.
    gaps=sorted(d.isoformat() for d in sessions if max(first_source,required_start)<=d<=latest and d not in rows)
    if gaps:issue('UNEXPLAINED_SESSION_GAPS',dates=gaps)
    output=[]; windows=[]; missing={}
    for y in range(first,last+1):
        annual=inclusive_window(date(y,anchor.month,anchor.day).isoformat(),days)
        start,end=_date(annual['start_date']),_date(annual['end_date'])
        expected=[d for d in sessions if start<=d<=end]
        if not expected:
            issue('WINDOW_HAS_NO_SESSIONS',year=y);continue
        if end<first_source:
            missing[str(y)]={'reason':'source_data_unavailable','evidence_ref':source_ref}
            windows.append({'year':y,**annual,'status':'before_supplied_history'});continue
        absent=[d.isoformat() for d in expected if d not in rows]
        if absent:
            issue('WINDOW_SESSION_GAPS',year=y,dates=absent);continue
        entry,exit_day=expected[0],expected[-1]
        c0,c1=rows[entry]['close'],rows[exit_day]['close']
        subsequent=expected[1:]
        high=max([c0]+[rows[d]['high'] for d in subsequent])
        low=min([c0]+[rows[d]['low'] for d in subsequent])
        try:
            net,mfe,mae=[round_percent(Decimal(100)*(p-c0)/c0) for p in (c1,high,low)]
            if any(finite_number(v) is None for v in (net,mfe,mae)):raise ValueError('nonfinite return')
        except (DecimalException, ValueError, OverflowError):
            issue('UNREPRESENTABLE_WINDOW_RETURN',year=y);continue
        output.append({'year':y,'net':net,'mfe':mfe,'mae':mae})
        windows.append({'year':y,**annual,'entry_session':str(entry),'exit_session':str(exit_day),'session_n':len(expected),'status':'checked'})
    calendar_hash=hashlib.sha256(json.dumps(session_manifest,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    normalized_hash=hashlib.sha256(json.dumps([{ 'date':str(d),**{k:str(v) for k,v in rows[d].items()}} for d in sorted(rows)],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result.update({'status':'held' if result['issues'] else 'passed', 'as_of':now.isoformat(),
        'decision_cutoff':str(cutoff),'instrument_key':instrument_key(instrument),'window':window,
        'requested_years':str(requested_years),'observations':output,'windows':windows,
        'coverage':{'start_year':first,'end_year':last,'source_ref':source_ref,'missing_year_reasons':missing},
        'history':{'instrument_key':instrument_key(instrument),'calendar_id':session_manifest['calendar_id'],
            'dataset_sha256':dataset_sha256,'evidence_ids':[source_ref,session_manifest['source_ref']],
            'last_observation_date':str(last_source),'expected_latest_date':str(latest),'calendar_checked_as_of':now.date().isoformat(),
            'unexplained_gaps':gaps+unexpected,'invalid_values':invalid+duplicate,'nonpositive_values':bool(invalid),
            'window_checks_passed':not result['issues']},
        'provenance':{'source_ref':source_ref,'dataset_sha256':dataset_sha256,'calendar_source_ref':session_manifest['source_ref'],
            'calendar_sha256':calendar_hash,'normalized_ohlc_sha256':normalized_hash,'audited_at':now.isoformat(),
            'scope':'Retrospective calculation from supplied adjusted history; not point-in-time reconstruction.'}})
    return result
