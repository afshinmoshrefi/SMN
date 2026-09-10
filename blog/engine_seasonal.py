"""Presentation of retained TradeWave results. No seasonal calculations.

Inputs must carry the production study and traceable engine exports. Missing
results fail closed. Raw prices may be plotted but never converted into returns,
sample summaries or projections here.
"""
from copy import deepcopy
import csv
import hashlib
import html
import json
from pathlib import Path
import re

from visual_evidence import digest, finite

AUTHORITY = 'tradewave-engine-export-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode_rows(response, anchor):
    """Parse the existing ChartData4 transport; preserve each returned value."""
    rows = []
    for raw in response['ChartData4']:
        # The API marks a future, non-observation with both zero price fields.
        # Never drop a real historical flat result.
        if str(raw.get('price')) == '0,0' and raw['year'] >= int(anchor[:4]):
            continue
        pct = [float(v) for v in raw['pct'].split(',')]
        price = [float(v) for v in raw['price'].split(',')]
        if len(pct) != 3 or len(price) != 2:
            raise ValueError('Ambiguous TradeWave row')
        rows.append(dict(year=raw['year'],net=pct[0],mfe=pct[1],mae=pct[2],
                         entry_price=price[0],exit_price=price[1]))
    return rows


def identity(original):
    return {'resource_id':str(original['resource_id']), 'symbol':original['symbol'],
            'anchor_date':original['pattern_start_date'], 'days':int(original['pattern_days']),
            'years':original['lookback_years'], 'direction':original['direction']}


def cohort_label(years, observed):
    if re.fullmatch(r'pe[0-3]-\d+',years):
        phase={'pe0':'election','pe1':'post-election','pe2':'midterm-election','pe3':'pre-election'}[years.split('-')[0]]
        return f'{len(observed)} selected {phase} years ({observed[0]}–{observed[-1]})'
    if years.isdigit():
        return f'{len(observed)} consecutive historical windows ({observed[0]}–{observed[-1]})'
    raise ValueError('Unsupported explicit year selection')


def make_card(original, retained_payload, export, owner, captured_at, origin, angle, full_dataset=None):
    """Bind literal exported fields. The original article's prose is not evidence."""
    study=identity(original); m=retained_payload['meta']
    mapped={'resource_id':str(m['resource_id']),'symbol':m['symbol'],
            'anchor_date':m['pattern_start_date'],'days':int(m['pattern_window_days']),
            'years':m['lookback_years'],'direction':retained_payload['stats']['Trade Dir']}
    if study != mapped or identity(export['identity']) != study:
        raise ValueError('Production study identity differs from retained engine evidence')
    primary=next(r for r in export['responses'] if r['request']['years']==study['years'])
    rows=[{'year':r['year'],**{dst:float(r[src]) for dst,src in
          [('net','net_return_pct'),('mfe','mfe_pct'),('mae','mae_pct'),('entry_price','entry_price'),('exit_price','exit_price')]}}
          for r in retained_payload['per_year']]
    # The live engine must reproduce these primary chart values; compare values,
    # never an independent price/return formula. Historical entries are preserved.
    live=decode_rows(primary['response'],study['anchor_date'])
    keys=('year','net','mfe','mae','entry_price','exit_price')
    matched_live=[r for r in live if r['year'] in {v['year'] for v in rows}]
    if [{k:r[k] for k in keys} for r in rows] != [{k:r[k] for k in keys} for r in matched_live]:
        raise ValueError('TradeWave current primary rows differ from production; hold for source review')
    w=next(i['semantics'] for i in retained_payload['images'] if i.get('variant')=='bars')
    if len(live)!=w['n']:
        raise ValueError('Engine full sample differs from the published chart')
    if len(rows)<w['n']:
        # The legacy writer truncates its prose packet to ten rows. The long
        # study's complete published dataset is a second retained witness.
        if not full_dataset or study['direction']!='long':
            raise ValueError('Full original sample evidence missing')
        witness=[{'year':r['year'],'net':r['net_return_pct'],'mfe':r['mfe_pct'],'mae':r['mae_pct']} for r in full_dataset['years']
                 if r['year']<int(study['anchor_date'][:4])]
        if witness!=[{k:r[k] for k in ('year','net','mfe','mae')} for r in live]:
            raise ValueError('Full engine sample differs from retained published dataset')
    rows=live
    comparisons=[]
    for query in export['responses']:
        if query['request']['years']==study['years']:continue
        crows=decode_rows(query['response'],study['anchor_date'])
        comparisons.append({'request':query['request'],'stats':query['response']['stats'],
            'per_year':crows,'label':cohort_label(query['request']['years'],[r['year'] for r in crows]),
            'raw_response_sha256':digest(query['response'])})
    card={'schema_version':3,'calculation_authority':AUTHORITY, **{k:study[k] for k in ('symbol','resource_id')},
        'angle':angle,'production_identity':study,'production_original':original['url'],
        'story_cell':{**study,'per_year':rows,'n':w['n']},
        'engine_results':{'stats':deepcopy(retained_payload['stats']),
            'window':{'start_date':w['window_start'],'end_date':w['window_end'],'calendar_days':study['days']},
            'cohort':{'label':cohort_label(study['years'],[r['year'] for r in rows]),
                      'n':w['n'],'years':[r['year'] for r in rows]},'comparisons':comparisons},
        'provenance':{'retained':origin,'retained_payload_sha256':digest(retained_payload),
            'engine_owner':owner,'captured_at':captured_at,'primary_response_sha256':digest(primary['response']),
            'primary_chart_values_match_production':True},
        'price_path':deepcopy(export['price_path'])}
    validate_card(card)
    return card


def validate_card(card):
    if card.get('calculation_authority') != AUTHORITY:
        raise ValueError('TradeWave engine export required; independent calculations are prohibited')
    c=card['story_cell']; expected=card['production_identity']
    if {k:c[k] for k in expected} != expected:
        raise ValueError('Selected production study changed')
    rows=c['per_year']; years=[r['year'] for r in rows]
    if not rows or len(set(years))!=len(years) or years!=sorted(years):
        raise ValueError('Invalid authoritative observation identities')
    if len(rows)!=c['n'] or years!=card['engine_results']['cohort']['years']:
        raise ValueError('Authoritative sample binding changed')
    for r in rows:
        for k in ('net','mfe','mae','entry_price','exit_price'):finite(r[k])
        # Do not force extrema to include zero or rebuild engine date rules.
        if r['year']>=int(c['anchor_date'][:4]) or r['entry_price']<=0:
            raise ValueError('Historical observation is incomplete')
    stats=card['engine_results']['stats']
    for key in ('Trade Dir','Num Winners','Num Losers','Percent Profitable','Median Profit','Avg Profit - All','last_trade_date'):
        if key not in stats:raise ValueError('Missing authoritative statistic: '+key)
    if stats['Trade Dir']!=expected['direction']:
        raise ValueError('Trade direction changed')
    p=card['price_path']; q=p['request']
    if (q['symbol'],q['resource_id'],q['years'],q['opp_start_date']) != (c['symbol'],str(c['resource_id']),c['years'],c['anchor_date']):
        raise ValueError('Price outlook uses a different study')
    if p['owner_function']!='site/lib/svg_wave_chart.py::compute_projection' or not p['projection_response']:
        raise ValueError('Existing TradeWave projection result missing')
    if not card['provenance'].get('primary_chart_values_match_production'):
        raise ValueError('Production fidelity check missing')
    return card


def bind_source(source,bundle):
    card=deepcopy(source.get('card') or {})
    validate_card(card)
    contract=bundle['seasonal_contract']
    if contract['card_sha256']!=digest(card) or contract['angle']!=card['angle']['name']:
        raise ValueError('Engine card/commission changed after preparation')
    history=next(s for s in bundle['sources'] if s['id']==contract['history_source_id'])
    if history['payload']!=card['engine_results']:
        raise ValueError('Writer evidence differs from displayed engine results')
    return card,deepcopy(card['engine_results']),contract


def prepare(source,bundle,directory):
    import chartkit
    from seasonal_edition import study_link
    card,e,contract=bind_source(source,bundle)
    root=Path(directory); assets=root/'assets'; assets.mkdir(parents=True,exist_ok=True)
    c=card['story_cell']; rows=c['per_year']; years=[r['year'] for r in rows]; stats=e['stats']; images=[]
    caps=int(digest(card['production_identity'])[:8],16)%4!=0
    for variant in ('bars','bars_mae_mfe'):
        is_range=variant=='bars_mae_mfe'
        caption=('Each year shows the same seasonal period. Bars show the price change at its end. Thin lines show the highest and lowest changes from the starting price during that period. '
                 'They do not describe the order of moves or peak-to-trough drawdown.' if is_range else
                 'Each bar is the underlying share-price change over the complete seasonal window. Green means higher; red means lower.')
        if c['direction']=='long':caption+=' The dashed line marks TradeWave’s median full-window result of '+stats['Median Profit']+'.'
        title=f"{c['symbol']}: "+('yearly seasonal range' if is_range else 'the selected seasonal record')
        meta={'symbol':c['symbol'],'company':contract['company'],'days':c['days'],'direction':c['direction'],
            'window_start':e['window']['start_date'],'window_end':e['window']['end_date'],'verified_completed':True,
            'variant':variant,'range_caps':caps if is_range else False,
            'engine_presentation':{'title':title,'mobile_title':c['symbol']+(': yearly seasonal range' if is_range else ': seasonal record'),
              'mobile_spec':str(c['n'])+(' midterm-year windows' if c['years'].startswith('pe2-') else ' consecutive windows')+' | '+e['window']['start_date'][5:]+' to '+e['window']['end_date'][5:],
              'spec':('Bars: ending price change. Lines: low to high from entry.' if is_range else e['cohort']['label'])+f" · {e['window']['start_date']} to {e['window']['end_date']}",
              'caption':caption,'source':'TradeWave engine · '+e['cohort']['label'],
              'price_median':float(stats['Median Profit'].rstrip('%')) if c['direction']=='long' else None}}
        kwargs={'mfe':[r['mfe'] for r in rows],'mae':[r['mae'] for r in rows]} if is_range else {}
        for mobile in (False,True):
            path=assets/('tradewave-'+variant+('-mobile' if mobile else '')+'.png')
            sem=chartkit.record_bars(years,[r['net'] for r in rows],meta,str(path),mobile=mobile,**kwargs)
        im={'variant':variant,'url':f'assets/tradewave-{variant}.png','mobile_url':f'assets/tradewave-{variant}-mobile.png',
            'caption':caption,'alt':title+'. '+caption+' '+e['cohort']['label'], 'semantics':sem,
            'range_caps':meta['range_caps'],'values_sha256':digest(rows)}
        for k in ('url','mobile_url'):im['sha256' if k=='url' else 'mobile_sha256']=sha(root/im[k])
        images.append(im)
        # Both approved range styles are retained; the stable 75% choice only
        # changes cap geometry, never any financial value.
        if is_range:
            for show_caps in (False,True):
                for mobile in (False,True):
                    suffix=('capped' if show_caps else 'uncapped')+('-mobile' if mobile else '')
                    chartkit.record_bars(years,[r['net'] for r in rows],{**meta,'range_caps':show_caps},str(assets/f'tradewave-range-{suffix}.png'),mobile=mobile,**kwargs)
    with (assets/'tradewave-observations.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    price=render_price(card,assets)
    images.append(price)
    data={'card':card,'evidence':e,'images':images,'study_url':study_link(card,contract['viewer_url']),
        'history_source_id':contract['history_source_id'],'methodology_url':contract['methodology_url'],
        'book_url':contract.get('book_url'),'csv_sha256':sha(assets/'tradewave-observations.csv'),
        'price_path':{'evidence_sha256':digest(card['price_path']),'authority':AUTHORITY},
        'price_path_csv_sha256':sha(assets/'tradewave-price-path.csv')}
    (root/'seasonal-manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data


def render_price(card,assets):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime
    p=card['price_path']; past=p['ohlc_response']; future=p['projection_response']
    # Date parsing and responsive layout only. Plot the owner's points unchanged.
    dates=[datetime.fromisoformat(r[0]) for r in past]; prices=[float(r[4]) for r in past]
    fdates=[dates[-1]]+[datetime.fromisoformat(r[0]) for r in future]
    fprices=[prices[-1]]+[r[1] for r in future]
    for mobile in (False,True):
        with plt.rc_context({'font.family':'DejaVu Sans','font.size':11}):
            fig,ax=plt.subplots(figsize=(5.1,5.3) if mobile else (10,4.8),dpi=160)
            ax.plot(dates,prices,color='#295ac7',linewidth=1.6,label='Recorded closing price')
            ax.plot(fdates,fprices,color='#c67818',linestyle='--',linewidth=1.8,label='TradeWave seasonal illustration')
            ax.axvline(dates[-1],color='#8d9ba5',linewidth=.8,linestyle=':')
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4,maxticks=5 if mobile else 8))
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
            ax.set_ylabel('Share price (USD)');ax.grid(axis='y',color='#e7ecee')
            ax.spines[['right','top']].set_visible(False)
            ax.legend(loc='upper left',fontsize=8 if mobile else 9,frameon=False)
            fig.tight_layout(pad=1.2)
            fig.savefig(assets/('tradewave-price_projection'+('-mobile' if mobile else '')+'.png'),facecolor='white')
            plt.close(fig)
    with (assets/'tradewave-price-path.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['series','date','price'])
        writer.writerows(['TradeWave recorded close',r[0],r[4]] for r in past)
        writer.writerows(['TradeWave projection result',r[0],r[1]] for r in future)
    caption=(f"Blue: recorded prices through {p['last_price_date']}. Amber: TradeWave's seasonal illustration for the next 60 weekdays, "
        f"using {card['engine_results']['cohort']['label']}. Its displayed dates are {future[0][0]} to {future[-1][0]}; "
        'this horizon is separate from the selected seasonal window. It reflects an average historical curve, not a median, price target or forecast.')
    return {'variant':'price_projection','url':'assets/tradewave-price_projection.png','mobile_url':'assets/tradewave-price_projection-mobile.png',
        'sha256':sha(assets/'tradewave-price_projection.png'),'mobile_sha256':sha(assets/'tradewave-price_projection-mobile.png'),
        'caption':caption,'alt':card['symbol']+' recorded prices and TradeWave seasonal illustration. '+caption,
        'values_sha256':digest(p)}


def stats_html(data):
    esc=html.escape; e=data['evidence'];s=e['stats'];w=e['window'];c=e['cohort']
    side='long' if s['Trade Dir']=='long' else 'short'
    values=[('Selected historical windows',str(c['n'])),('Profitable windows (TradeWave)',s['Num Winners']),
        ('Historical success rate',s['Percent Profitable']),('Median full-window '+side+' result',s['Median Profit']),
        ('Average full-window '+side+' result',s['Avg Profit - All'])]
    return (f'<div class="pattern-meta"><span>{esc(data["card"]["symbol"])}</span><span>{w["start_date"]} to {w["end_date"]} · {w["calendar_days"]} calendar days</span><span>{esc(c["label"])}</span></div>'
        '<aside class="key-stats"><h3>TradeWave Key Stats</h3><table><tbody>'+''.join(f'<tr><th scope="row">{esc(k)}</th><td>{esc(v)}</td></tr>' for k,v in values)+
        '</tbody></table><p>Exact TradeWave outputs, including its display precision. '+
        ('Short-side historical profits describe falling prices; the chart bars retain the actual price-change signs. ' if side=='short' else '')+
        'Full-window historical results, before trading costs. These are not forecast probabilities or returns remaining from today.</p></aside>')


def figure_html(data,variant):
    esc=html.escape;im=next(i for i in data['images'] if i['variant']==variant)
    title='Price history and the seasonal outlook' if variant=='price_projection' else 'What happened inside each seasonal window' if variant=='bars_mae_mfe' else 'The year-by-year seasonal record'
    out=f'<figure class="native-figure" data-native-chart="{variant}"><h3>{title}</h3><picture><source media="(max-width:600px)" srcset="{im["mobile_url"]}"><img src="{im["url"]}" alt="{esc(im["alt"],quote=True)}" loading="lazy"></picture><figcaption>{esc(im["caption"])}</figcaption>'
    if variant=='price_projection':
        return out+'<p><a href="assets/tradewave-price-path.csv" download>Download the TradeWave chart points</a></p></figure>'
    rows=data['card']['story_cell']['per_year']
    return out+'<details><summary>View exact TradeWave observations and download</summary><div class="table-scroll"><table><thead><tr><th>Window starting year</th><th>Ending price change</th><th>Highest from entry</th><th>Lowest from entry</th></tr></thead><tbody>'+''.join(f'<tr><th scope="row">{r["year"]}</th><td>{r["net"]:+.2f}%</td><td>{r["mfe"]:+.2f}%</td><td>{r["mae"]:+.2f}%</td></tr>' for r in rows)+'</tbody></table></div><a href="assets/tradewave-observations.csv" download>Download TradeWave observations</a></details></figure>'


def comparison_rows(data):
    e=data['evidence']
    return [{'label':'Production-selected study: '+e['cohort']['label'],'stats':e['stats'],'years':e['cohort']['years']}]+[
        {'label':r['label'],'stats':r['stats'],'years':[x['year'] for x in r['per_year']]} for r in e['comparisons']]


def comparison_html(data):
    esc=html.escape
    rows=comparison_rows(data)
    return '<details class="history-comparison"><summary>See the history behind this comparison</summary><p>The production-selected study stays primary. The same calendar window is also requested from TradeWave with other year selections to test whether the result depends on that choice.</p><div class="table-scroll"><table><thead><tr><th>History</th><th>Observed start years</th><th>Direction</th><th>Success rate</th><th>Median result</th></tr></thead><tbody>'+''.join('<tr><th>'+esc(r['label'])+'</th><td data-label="Years">'+', '.join(map(str,r['years']))+'</td><td data-label="Direction">'+esc(r['stats']['Trade Dir'])+'</td><td data-label="Success">'+esc(r['stats']['Percent Profitable'])+'</td><td data-label="Median">'+esc(r['stats']['Median Profit'])+'</td></tr>' for r in rows)+'</tbody></table></div><p>These groups overlap; agreement is not independent confirmation. The ten-year sample is part of the twenty-year sample, not a separate earlier-decade comparison. Cycle groups select election phases, not consecutive years. Each result uses the direction shown; a short-side gain is not a rising share price. Small samples and different market eras limit what the comparison can establish.</p></details>'


def methodology_html(data):
    esc=html.escape
    return '<section class="methodology-note"><h2>About this seasonal analysis</h2><p>'+esc(data['evidence']['cohort']['label'])+'. The selected dates, years, direction, returns and ranges come from TradeWave. When a window boundary is a weekend or market holiday, TradeWave uses the next available trading session. SMN displays the returned results without recalculating them. A year label identifies the start of the historical window; a window can end in the following calendar year.</p><p>Green and red bars show the underlying adjusted-price movement. The range endpoints measure changes from the entry price, not a peak-to-trough loss. Historical results are before trading costs and do not guarantee future performance.</p><p><a href="'+esc(data['methodology_url'],quote=True)+'">TradeWave data methodology</a> · <a href="https://100yearpattern.com/">The 100-Year Pattern</a></p></section>'


def verify_assets(data,directory):
    validate_card(data['card']);root=Path(directory)
    if data['evidence']!=data['card']['engine_results']:
        raise ValueError('Displayed engine statistics changed')
    for im in data['images']:
        for file,key in [('url','sha256'),('mobile_url','mobile_sha256')]:
            if sha(root/im[file])!=im[key]:raise ValueError('Changed TradeWave chart asset')
    for name,key in [('tradewave-observations.csv','csv_sha256'),('tradewave-price-path.csv','price_path_csv_sha256')]:
        if sha(root/'assets'/name)!=data[key]:raise ValueError('Changed TradeWave data export')
    return True


def inspect_native(data,directory,article,review,bundle):
    issues=[]
    try:
        bind_source({'card':data['card']},bundle);verify_assets(data,directory)
    except ValueError as exc:issues.append(str(exc))
    for im in data['images']:
        if figure_html(data,im['variant']) not in article:issues.append('Missing protected chart '+im['variant'])
    if stats_html(data) not in article or methodology_html(data) not in article:issues.append('Missing protected engine evidence')
    return issues
