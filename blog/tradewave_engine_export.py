"""Read-only transport, run ON TradeWave, for SMN's retained engine responses.

No financial formulas live here. Projection points are returned by TradeWave's
existing site library on the TradeWave host, never copied/reimplemented in SMN.
Send a JSON request on stdin. Credentials remain inside TradeWave configuration.
"""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys


def export(request):
    owner = Path('/home/flask/site/lib/svg_wave_chart.py')
    if not owner.is_file():
        raise RuntimeError('TradeWave projection owner unavailable; no local fallback')
    sys.path.insert(0, str(owner.parent))
    spec = importlib.util.spec_from_file_location('tradewave_owned_wave_chart', owner)
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)
    token = tw.get_appserver_token()
    if not token:
        raise RuntimeError('TradeWave authentication unavailable')
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    result = {'schema_version': 1, 'captured_at': now,
              'owner': {'path': str(owner), 'sha256': hashlib.sha256(owner.read_bytes()).hexdigest(),
                        'commit': subprocess.check_output(['sudo','-u','flask','git','-C','/home/flask','rev-parse','HEAD'],text=True).strip()},
              'studies': []}
    for study in request['studies']:
        rid, sym = str(study['resource_id']), study['symbol']
        anchor, years = study['pattern_start_date'], study['lookback_years']
        # SMN's inclusive duration is transported to the existing API's offset.
        offset = str(int(study['pattern_days']) - 1)
        queries = list(dict.fromkeys([years] + study['comparison_years']))
        responses = []
        for sy in queries:
            raw = tw.fetch_bar_data(rid, anchor, sym, offset, sy, token)
            if not raw.get('stats') or not isinstance(raw.get('ChartData4'),list):
                raise RuntimeError('Missing TradeWave seasonal response for '+sym+' '+sy)
            responses.append({'request': {'resource_id': rid, 'symbol': sym, 'anchor_date': anchor,
                    'days_out': offset, 'years': sy}, 'response': raw})
        # Use the existing TradeWave library itself, executing on TradeWave.
        # Do not replace this call with a local formula or an SMN chart helper.
        ohlc = tw.fetch_ohlc_data(rid,sym,request['price_start'],request['price_end'],token)
        if not ohlc:
            raise RuntimeError('Missing TradeWave price response for '+sym)
        chart_start = (dt.date.fromisoformat(anchor)-dt.timedelta(days=14)).isoformat()
        seasonal = tw.fetch_seasonal_data(rid,sym,years,chart_start,anchor,token)
        points = tw.compute_projection(float(ohlc[-1][4]),ohlc[-1][0],seasonal,period_days=60)
        if not points:
            raise RuntimeError('TradeWave projection owner returned no points for '+sym)
        result['studies'].append({'identity':study,'responses':responses,
                'price_path':{'owner_function':'site/lib/svg_wave_chart.py::compute_projection',
                    'request':{'resource_id':rid,'symbol':sym,'years':years,'opp_start_date':anchor,
                               'chart_start_date':chart_start,'period_days':60},
                    'ohlc_response':ohlc,'consolidated_response':seasonal,'projection_response':points,
                    'last_price_date':ohlc[-1][0], 'last_price':ohlc[-1][4],
                    'convention':'TradeWave existing 60 weekday-step projection from its average seasonal curve; not a median or a forecast.'}})
    return result


if __name__ == '__main__':
    request = json.load(sys.stdin)
    # Imported legacy owners can log; never mix logs/credentials into evidence.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            result = export(request)
        except Exception as exc:
            result = {'error':type(exc).__name__, 'missing_module':getattr(exc,'name',None), 'message':'TradeWave export held; inspect on-host source availability without logging tokens.'}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
