"""Daily-price evidence for the private seasonal edition. No network/model calls.

The article's explicit sampled years own the calculation. This is a pointwise
median of normalized, observed daily closes, never a path inferred from annual
returns/extrema or a reused production seasonal curve.
"""
from bisect import bisect_right
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
import csv
import hashlib
import html
import io
import json
import math

from article_evidence import inclusive_window, round_percent
from visual_evidence import digest


def derive(card, csv_bytes, audit, *, horizon_days=60, reference_only=False):
    """Return private reproducible paths bound to the article and retained CSV.

    Calendar offsets are relative to each article window's start, so leap years
    use the same inclusive day-count convention as the annual observations.
    A historical date uses the latest recorded close on/before that date.
    Non-session dates retain that close; the supplied independent audit must
    reject missing exchange sessions. GC's explicit dev reference exception
    never upgrades its held calendar audit to a pass.
    """
    cell = card['story_cell']
    sample = sorted(cell['per_year'], key=lambda r: r['year'])
    years = [r['year'] for r in sample]
    if (not isinstance(cell['years'], str) or len(sample) != cell['n'] or
            len(set(years)) != len(years) or len(years) < 2 or
            any(type(y) is not int for y in years)):
        raise ValueError('Exact nonempty article sample required')
    if sample != sorted(card['selection_evidence']['baseline']['per_year'], key=lambda r:r['year']):
        raise ValueError('Article and baseline samples differ')
    if type(horizon_days) is not int or not 2 <= horizon_days <= 366:
        raise ValueError('Bounded inclusive calendar-day horizon required')
    measurement = card['instrument']['semantics']['measurement']
    if reference_only:
        if not (card['symbol'] == 'GC' and str(card['resource_id']) == '7' and
                measurement == 'provider_reference_price_change' and
                audit['status'] == 'reference_calculation_only' and
                audit['independent_calendar_validation'] == 'held' and
                audit['production_release_allowed'] is False):
            raise ValueError('Explicit held GC dev reference evidence required')
    elif (audit['status'] != 'passed' or not audit['history']['window_checks_passed'] or
          measurement not in {'adjusted_price_return', 'price_index_change'}):
        raise ValueError('Independent daily history validation has not passed')
    dataset_hash = hashlib.sha256(csv_bytes).hexdigest()
    if dataset_hash != audit.get('dataset_sha256', audit.get('history', {}).get('dataset_sha256')):
        raise ValueError('Daily CSV differs from audited history')
    source = list(csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig'))))
    records = {}
    for row in source:
        day = date.fromisoformat(row['date'])
        values = {k:Decimal(row[k]) for k in ('open','high','low','close')}
        if day in records or any(not v.is_finite() or v <= 0 for v in values.values()):
            raise ValueError('Duplicate date or invalid price')
        if values['high'] < max(values.values()) or values['low'] > min(values.values()):
            raise ValueError('Invalid OHLC order')
        records[day] = values
    dates = sorted(records)
    if not dates:
        raise ValueError('Daily prices required')
    now = datetime.fromisoformat(audit['as_of'].replace('Z', '+00:00'))
    if now.tzinfo is None or dates[-1] > now.date() or (reference_only and dates[-1] == now.date()):
        raise ValueError('Use a completed, dated price snapshot')
    if not reference_only and str(dates[-1]) != audit['history']['expected_latest_date']:
        raise ValueError('Price anchor differs from the audited completed session')
    if (now.date()-dates[-1]).days > 7:
        raise ValueError('Price snapshot is stale for the edition as-of')
    anchor = dates[-1]
    window_start = date.fromisoformat(cell['anchor_date'])
    window = inclusive_window(cell['anchor_date'], cell['days'])
    offset = (anchor-window_start).days
    last_close = records[anchor]['close']

    def on_or_before(target):
        idx = bisect_right(dates, target)-1
        if idx < 0 or (target-dates[idx]).days > 7:
            raise ValueError('Insufficient historical closes near a path date')
        return dates[idx]

    paths = []
    matches = []
    for annual in sample:
        year = annual['year']
        historical_start = date(year, window_start.month, window_start.day)
        historical_window = inclusive_window(str(historical_start), cell['days'])
        end = date.fromisoformat(historical_window['end_date'])
        inside = [d for d in dates if historical_start <= d <= end]
        if not inside or end >= anchor:
            raise ValueError('Incomplete article observation')
        entry = records[inside[0]]['close']
        numbers = [records[inside[-1]]['close'],
                   max([entry]+[records[d]['high'] for d in inside[1:]]),
                   min([entry]+[records[d]['low'] for d in inside[1:]])]
        computed = dict(zip(('net','mfe','mae'), [round_percent(100*(p-entry)/entry) for p in numbers]))
        if any(computed[k] != annual[k] for k in computed):
            raise ValueError('Daily data does not reproduce the article annual observation: '+str(year))
        mapped_anchor = historical_start+timedelta(days=offset)
        mapped_end = mapped_anchor+timedelta(days=horizon_days-1)
        if mapped_end >= anchor:
            raise ValueError('Path includes uncompleted historical dates')
        anchor_close_day = on_or_before(mapped_anchor)
        base = records[anchor_close_day]['close']
        points = []
        for step in range(horizon_days):
            target = mapped_anchor+timedelta(days=step)
            observed = on_or_before(target)
            points.append({'mapped_date':str(target), 'observed_date':str(observed),
                           'return_pct':float(100*(records[observed]['close']/base-1))})
        paths.append({'year':year, 'anchor_observed_date':str(anchor_close_day), 'points':points})
        matches.append({'year':year, 'entry':str(inside[0]), 'exit':str(inside[-1]), **computed})
    line = []
    for step in range(horizon_days):
        value = median([p['points'][step]['return_pct'] for p in paths])
        line.append({'date':str(anchor+timedelta(days=step)), 'median_return_pct':value,
                     'illustrative_price':float(last_close)*(1+value/100), 'n':len(years)})
    recent = [d for d in dates if d >= anchor-timedelta(days=365)]
    if len(recent) < 100:
        raise ValueError('Insufficient recent price history')
    result = {'schema_version':1, 'variant':'price_projection', 'symbol':card['symbol'],
              'resource_id':str(card['resource_id']), 'card_sha256':digest(card),
              'dataset_sha256':dataset_hash, 'audit_sha256':digest(audit),
              'as_of':audit['as_of'], 'anchor_date':str(anchor), 'anchor_price':float(last_close),
              'analysis_years':years, 'years_parameter':cell['years'], 'n':len(years),
              'window':window, 'horizon_days':horizon_days, 'day_basis':'inclusive_calendar_days',
              'aggregation':'pointwise_median_of_individually_normalized_daily_close_returns',
              'alignment':'calendar_offsets_from_each_article_window_start; last_observed_close_on_or_before',
              'measurement':measurement, 'reference_only':reference_only,
              'independent_calendar_validation':'held' if reference_only else 'passed',
              'production_release_allowed':False,
              'recent_prices':[{'date':str(d), 'price':float(records[d]['close'])} for d in recent],
              'projection':line, 'historical_paths':paths, 'annual_reconciliation':matches}
    result['evidence_sha256'] = digest(result)
    return result


def validate(data, card):
    raw = {k:v for k,v in data.items() if k != 'evidence_sha256'}
    if data.get('evidence_sha256') != digest(raw) or data.get('card_sha256') != digest(card):
        raise ValueError('Price-path evidence or article binding changed')
    if data['analysis_years'] != sorted(r['year'] for r in card['story_cell']['per_year']):
        raise ValueError('Price path does not use the article analysis years')
    if (data['symbol'] != card['symbol'] or data['resource_id'] != str(card['resource_id']) or
            data['n'] != len(data['analysis_years']) or data['years_parameter'] != card['story_cell']['years'] or
            data['window'] != inclusive_window(card['story_cell']['anchor_date'],card['story_cell']['days'])):
        raise ValueError('Price-path study identity differs from article')
    paths=data['historical_paths'];line=data['projection'];days=data['horizon_days']
    if ([p['year'] for p in paths]!=data['analysis_years'] or len(line)!=days or
            any(len(p['points'])!=days for p in paths)):
        raise ValueError('Incomplete daily path sample')
    anchor=date.fromisoformat(data['anchor_date'])
    for i,row in enumerate(line):
        expected=median([p['points'][i]['return_pct'] for p in paths])
        if (row['date']!=str(anchor+timedelta(days=i)) or row['n']!=len(paths) or
                not math.isclose(row['median_return_pct'],expected,abs_tol=1e-10) or
                not math.isclose(row['illustrative_price'],data['anchor_price']*(1+expected/100),abs_tol=1e-9)):
            raise ValueError('Plotted daily median does not match the historical paths')
    if (data['projection'][0]['median_return_pct'] != 0 or
            data['projection'][0]['illustrative_price'] != data['anchor_price']):
        raise ValueError('Historical path must connect at the observed close')


def sample_label(data):
    years = data['analysis_years']
    consecutive = years == list(range(years[0], years[-1]+1))
    return (f"{len(years)} consecutive sample years ({years[0]}-{years[-1]})" if consecutive else
            f"{len(years)} selected sample years ({', '.join(map(str, years))})")


def labels(data):
    start = date.fromisoformat(data['anchor_date'])
    end = date.fromisoformat(data['projection'][-1]['date'])
    span = f"{start:%b} {start.day}-{end:%b} {end.day}, {end.year}"
    basis = {'adjusted_price_return':'Adjusted daily closes', 'price_index_change':'Price-index daily closes',
             'provider_reference_price_change':'Provider-recorded GC reference closes'}[data['measurement']]
    change = data['projection'][-1]['median_return_pct']
    title = f"{data['symbol']} at {data['anchor_price']:,.2f}: Price and Seasonal Path"
    caption = (f"Blue: {basis.lower()} through {start:%B} {start.day}, {start.year}. "
               f"Dashed orange: the median historical path from this article's {sample_label(data)}, "
               f"anchored to that closing value. The {data['horizon_days']}-calendar-day illustration "
               f"({span}, anchor date included) ends {change:+.1f}% from its starting value. "
               "It is a historical illustration, not a forecast or price target; individual years varied.")
    if data['reference_only']:
        caption += ' GC is a provisional reference-series illustration; session coverage and contract-roll validation remain unresolved.'
    return {'title':title, 'span':span, 'basis':basis, 'caption':caption, 'alt':title+'. '+caption}


def render(data, path, *, mobile=False):
    """Approved blue/amber graphic. Mobile expands the same path in panel two."""
    import chartkit as ck
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    pal = ck.PAL
    w,h = (900,1260) if mobile else (1600,900)
    fig = plt.figure(figsize=(w/100,h/100),dpi=100,facecolor=pal['bg'])
    lab = labels(data)
    font = 22 if mobile else 14
    fig.text(.065,.952, data['symbol']+'  ·  TRADEWAVE PRICE AND SEASONAL PATH',
             fontsize=17 if mobile else 14,color=pal['muted'],fontweight=500)
    title = f"{data['symbol']} at {data['anchor_price']:,.2f}" if mobile else lab['title']
    fig.text(.065,.91,title,fontsize=30 if mobile else 26,fontweight=700,color=pal['ink'])
    fig.text(.065,.871, sample_label(data),fontsize=21 if mobile else 16,color=pal['muted'])
    change = data['projection'][-1]['median_return_pct']
    recent_d = [date.fromisoformat(r['date']) for r in data['recent_prices']]
    recent_p = [r['price'] for r in data['recent_prices']]
    pd = [date.fromisoformat(r['date']) for r in data['projection']]
    pp = [r['illustrative_price'] for r in data['projection']]
    axes = ([fig.add_axes((.13,.535,.81,.255)),fig.add_axes((.13,.16,.81,.255))] if mobile else
            [fig.add_axes((.075,.19,.86,.55))])
    for ax in axes:
        ax.set_facecolor(pal['bg'])
        for s in ('top','right','left'):ax.spines[s].set_visible(False)
        ax.spines['bottom'].set_color(pal['axis'])
        ax.grid(axis='y',color=pal['grid'],linewidth=1)
        ax.tick_params(axis='both',length=0,labelsize=font-2,colors=pal['muted'],pad=10)
        ax.yaxis.set_major_locator(MaxNLocator(5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:,.0f}'))
        ax.xaxis.set_major_locator(md.AutoDateLocator(minticks=3,maxticks=4 if mobile else 7))
        ax.xaxis.set_major_formatter(md.DateFormatter('%b %d') if mobile and ax is axes[-1] else md.DateFormatter('%b %Y'))
    actual = axes[0]
    actual.plot(recent_d,recent_p,color=pal['accent'],lw=2.8)
    actual.fill_between(recent_d,recent_p,min(recent_p),color=pal['accent'],alpha=.055)
    actual.plot(recent_d[-1],recent_p[-1],'o',color=pal['accent'],ms=5)
    future = axes[-1]
    future.plot(pd,pp,color=pal['amber'],lw=2.8,linestyle=(0,(3,2)))
    future.plot(pd[-1],pp[-1],'o',color=pal['amber'],ms=5)
    if mobile:
        actual.set_xlim(recent_d[0],recent_d[-1]+timedelta(days=6))
        future.set_xlim(pd[0],pd[-1]+timedelta(days=2))
        future.plot(pd[0],pp[0],'o',color=pal['accent'],ms=5)
        fig.text(.13,.81,'Past 12 months · '+lab['basis'].lower(),fontsize=19,color=pal['accent'])
        fig.text(.13,.458,f"{data['horizon_days']}-day historical illustration  {change:+.1f}%",fontsize=22,fontweight=600,color=pal['amber'])
        fig.text(.13,.433,lab['span']+' · expanded view',fontsize=18,color=pal['muted'])
    else:
        actual.set_xlim(recent_d[0],pd[-1]+timedelta(days=6))
        actual.axvline(pd[0],color=pal['faint'],lw=1,ls=(0,(2,3)))
        fig.text(.075,.78,'Past 12 months · '+lab['basis'],fontsize=14,color=pal['accent'])
        fig.text(.935,.78,f"{data['horizon_days']}-day historical path {change:+.1f}%",fontsize=16,ha='right',fontweight=600,color=pal['amber'])
        ck.place_label(actual,f"{data['anchor_price']:,.2f}",anchor=(md.date2num(pd[0]),pp[0]),
                       avoid=(list(zip(md.date2num(recent_d),recent_p)),list(zip(md.date2num(pd),pp))),
                       fontsize=13,color=pal['accent'])
    for ax in axes:
        ax.margins(y=.15)
        # Preserve fractional ticks on narrow price ranges (e.g. HPQ $31-$34).
        # Rounding every tick to an integer can give two distinct ticks one label.
        ticks=ax.get_yticks()
        precision=next((n for n in range(9) if all(abs(v-round(v,n))<1e-8 for v in ticks)),8)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_,n=precision:format(v,','+'.'+str(n)+'f')))
    if mobile:
        note = 'Historical illustration; not a forecast or target.'
        fig.text(.065,.078,note,fontsize=18,color=pal['muted'])
        fig.text(.065,.052,'Same article sample · Calendar days; anchor is day 1.',fontsize=17,color=pal['muted'])
        fig.text(.065,.025,'TradeWave history',fontsize=16,color=pal['faint'])
        fig.text(.94,.025,'TRADEWAVE.AI',fontsize=16,ha='right',color=pal['faint'])
        if data['reference_only']:
            fig.text(.065,.105,'GC reference series · Session/roll validation pending',fontsize=17,color=pal['muted'])
    else:
        fig.text(.075,.108,lab['span']+f" · {data['horizon_days']} calendar days including the anchor. Historical illustration, not a forecast or target.",fontsize=14,color=pal['muted'])
        fig.text(.075,.06,'Source: TradeWave daily history · Same historical sample as the article',fontsize=13,color=pal['faint'])
        fig.text(.935,.06,'TRADEWAVE.AI',fontsize=13,ha='right',color=pal['faint'])
        if data['reference_only']:
            fig.text(.065,.833,'GC reference-series illustration · Session coverage and contract-roll validation remain unresolved',fontsize=13,color=pal['muted'])
    fig.savefig(path,dpi=100,facecolor=pal['bg'])
    plt.close(fig)


def attach(native, supplied, directory):
    root = Path(directory)
    card = native['card']
    data = derive(card, supplied['csv_bytes'], supplied['audit'],
                  horizon_days=supplied.get('horizon_days',60), reference_only=supplied.get('reference_only',False))
    validate(data,card)
    assets = root/'assets';assets.mkdir(exist_ok=True)
    lab = labels(data)
    paths = [assets/'tradewave-price_projection.png', assets/'tradewave-price_projection-mobile.png']
    for i,p in enumerate(paths):render(data,p,mobile=bool(i))
    export = assets/'tradewave-price-path.csv'
    with export.open('w',encoding='utf-8',newline='') as f:
        writer = csv.writer(f);writer.writerow(['illustration_date','median_historical_change_pct','sample_n'])
        writer.writerows([r['date'],f"{r['median_return_pct']:.8f}",r['n']] for r in data['projection'])
    image = {'variant':'price_projection','path':str(paths[0].resolve()),'url':'assets/'+paths[0].name,
             'mobile_url':'assets/'+paths[1].name,'sha256':hashlib.sha256(paths[0].read_bytes()).hexdigest(),
             'mobile_sha256':hashlib.sha256(paths[1].read_bytes()).hexdigest(),
             'renderer':'seasonal_price_path.render', **lab,
             'semantics':{'variant':'price_projection','observed_years':data['analysis_years'],
                          'n':data['n'],'source_sha256':data['evidence_sha256'],
                          'card_sha256':digest(card),'window_start':data['window']['start_date'],
                          'window_end':data['window']['end_date'],'measurement':data['measurement']}}
    native['images'] = [im for im in native['images'] if im['variant'] != 'price_projection']+[image]
    native['price_path'] = data
    native['price_path_csv_sha256'] = hashlib.sha256(export.read_bytes()).hexdigest()
    (root/'seasonal-manifest.json').write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return native


def figure_html(native):
    data = native['price_path'];validate(data,native['card'])
    im = next(i for i in native['images'] if i['variant']=='price_projection')
    if im['semantics']['source_sha256'] != data['evidence_sha256']:
        raise ValueError('Rendered price path differs from source evidence')
    esc = html.escape
    window = data['window']
    method = (f"The illustration uses the same sample years as the article's {window['start_date']} to "
              f"{window['end_date']} analysis. It shows a separate {data['horizon_days']}-calendar-day view "
              "from the latest recorded close, rather than the full-window return in the annual bars. "
              "Each historical path starts at zero using the last recorded close on or before the corresponding "
              "calendar date. Each subsequent close is expressed as a percentage change from that historical "
              "path's starting close. The orange line "
              "is the median of those changes at each date, scaled to the latest closing value. On dates "
              "without a recorded observation, the preceding close is retained. Dates align by calendar-day "
              "offset from each year's analysis-window start. A median path is not one actual year's journey.")
    return ('<figure class="native-figure" data-native-chart="price_projection"><picture>'
            '<source media="(max-width:600px)" srcset="'+esc(im['mobile_url'],quote=True)+'">'
            '<img src="'+esc(im['url'],quote=True)+'" alt="'+esc(im['alt'],quote=True)+'"></picture>'
            '<figcaption>'+esc(im['caption'])+' Source: TradeWave daily price history.</figcaption>'
            '<details><summary>See the analysis years and how this path is calculated</summary><p><strong>Sample years:</strong> '+
            ', '.join(map(str,data['analysis_years']))+'.</p><p>'+esc(method)+'</p>'
            '<a href="assets/tradewave-price-path.csv" download>Download the historical path in percentages</a>'
            '</details></figure>')
