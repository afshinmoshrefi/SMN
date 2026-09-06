"""Render exact, percent-only historical bars for an isolated article preview.

No market requests, live config, existing image reuse or publisher imports.
Matplotlib is loaded only by an explicit rendering call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from article_evidence import build_cell_evidence, round_percent
from article_chart_evidence import chart_source_sha256


def _render_mobile_comparison(symbol, views, window, limits, directory):
    """Lay out the same three summaries vertically at a readable phone scale.

    768px is a two-density portrait asset. At a 350px article width its main
    labels remain roughly 15 CSS pixels instead of shrinking a desktop layout.
    Data and the shared horizontal scale come from the desktop render call.
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator, PercentFormatter
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 14,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.spines.left': False, 'axes.spines.bottom': False}):
        fig = plt.figure(figsize=(4.8, 7.2), dpi=160)
        fig.text(.07, .965, f'{symbol}: same window', fontsize=20, weight='bold', va='top')
        fig.text(.07, .916, 'Three historical samples', fontsize=15, va='top', color='#51616e')
        fig.text(.07, .87, f"{window['start_date'][5:]} to {window['end_date'][5:]} · "
                 f"{window['calendar_days']} days, inclusive", fontsize=12, va='top')
        for index, ((label, summary), top) in enumerate(zip(views, (.81, .59, .37))):
            value = summary['median_net_display']
            color = '#32658c' if value >= 0 else '#b46f35'
            fig.text(.07, top, label, fontsize=15, weight='bold', va='top', linespacing=1.12)
            fig.text(.07, top-.079, f"{summary['first_year']}–{summary['last_year']} · n={summary['n']}",
                     fontsize=12.5, color='#51616e', va='center')
            fig.text(.93, top-.081, f'{value:+.2f}%', fontsize=19, weight='bold',
                     color=color, ha='right', va='center')
            ax = fig.add_axes((.09, top-.15, .82, .045))
            ax.barh([0], [value], height=.72, color=color, zorder=3)
            ax.set_xlim(*limits)
            ax.set_ylim(-.6, .6)
            ax.axvline(0, color='#35424c', linewidth=1, zorder=2)
            ax.set_yticks([])
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=1))
            ax.grid(axis='x', color='#d8dfe4', linewidth=.7, zorder=0)
            ax.tick_params(axis='x', labelsize=11, length=0, labelbottom=index == 2)
        fig.text(.5, .153, 'Median historical change · same scale', ha='center', fontsize=12)
        fig.text(.07, .096, 'Adjusted-price changes. Samples overlap.', fontsize=11.5, color='#51616e')
        fig.text(.07, .062, 'Historical observations, not forecasts.', fontsize=11.5, color='#51616e')
        path = directory / 'comparison-hero-mobile.png'
        fig.savefig(path, metadata={'Software': 'SMN private comparison illustration'})
        plt.close(fig)
    return {'path': str(path), 'url': path.as_uri(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'width': 768, 'height': 1152}


def render_baseline_bars(card: dict, output_dir: str | Path) -> dict:
    from news_pipeline import _preview_directory
    from cohort_policy import validate_selection_evidence, baseline_cell
    checked = validate_selection_evidence(card.get('selection_evidence') or {})
    if not checked.get('ok'):
        raise ValueError('Cannot render an unverified private history')
    if (card.get('instrument') or {}).get('semantics', {}).get('measurement') != 'adjusted_price_return':
        raise ValueError('This renderer currently supports verified adjusted-price returns only')
    cell = card['story_cell']
    if cell != baseline_cell(card['selection_evidence']):
        raise ValueError('Chart must use the unchanged fixed baseline cell')
    facts = build_cell_evidence(cell)
    if facts != cell.get('evidence'):
        raise ValueError('Chart sample must match the recomputed article facts')
    rows = cell['per_year']
    if not rows:
        raise ValueError('Chart needs completed observations')
    directory = _preview_directory(output_dir)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    years = [r['year'] for r in rows]
    nets = [r['net'] for r in rows]
    with plt.rc_context({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.spines.left':False}):
        fig, ax = plt.subplots(figsize=(12,6.3),dpi=150)
        bars = ax.bar(range(len(rows)), nets,
                      color=['#32658c' if v>=0 else '#b46f35' for v in nets],width=.68,zorder=3)
        ax.axhline(0,color='#35424c',linewidth=.9,zorder=2)
        ax.set_xticks(range(len(years)),[str(y) for y in years],rotation=45,ha='right')
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
        ax.set_ylabel('Return over the historical window')
        ax.grid(axis='y',color='#d8dfe4',linewidth=.6,zorder=0)
        ax.margins(y=.2)
        if len(rows)<=20:
            for bar,v in zip(bars,nets):
                ax.annotate(f'{round_percent(v):+.2f}%',(bar.get_x()+bar.get_width()/2,v),
                            xytext=(0,4 if v>=0 else -5),textcoords='offset points',
                            ha='center',va='bottom' if v>=0 else 'top',fontsize=8)
        window=facts['window']
        fig.suptitle(f"{card['symbol']} historical returns: {window['start_date'][5:]} to {window['end_date'][5:]}",
                     x=.07,ha='left',fontsize=17,fontweight='bold',y=.98)
        ax.set_title(f"{len(rows)} annual observations, {min(years)}–{max(years)} · {window['calendar_days']} calendar days, inclusive",
                     loc='left',fontsize=11,color='#51616e',pad=16)
        fig.text(.07,.015,'Underlying adjusted-price changes. Historical observations are not forecasts.',
                 fontsize=10,color='#51616e')
        fig.tight_layout(rect=(.015,.05,.995,.94))
        path=directory/'baseline-bars.png'
        fig.savefig(path,metadata={'Software':'SMN private historical renderer'})
        plt.close(fig)
    source = {'resource_id':str(card['resource_id']),'symbol':card['symbol'],
              'anchor_date':cell['anchor_date'],'days':cell['days'],'years':cell['years'],
              'per_year':rows}
    semantics = {'symbol':card['symbol'],'resource_id':str(card['resource_id']),
                 'years':cell['years'],'n':len(rows),'observed_years':years,
                 'window_start':window['start_date'],'window_end':window['end_date'],
                 'direction':'long','measurement':'adjusted_price_return',
                 'source_sha256':chart_source_sha256(card)}
    result={'variant':'bars','path':str(path),'url':path.as_uri(),
            'alt':f"{card['symbol']} historical window returns for {len(rows)} annual observations, {min(years)}–{max(years)}",
            'caption':f"{len(rows)} annual observations over the same inclusive {cell['days']}-day calendar window. Bars show underlying price changes.",
            'semantics':semantics,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (directory/'baseline-bars.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def render_comparison_hero(card: dict, output_dir: str | Path) -> dict:
    """An evidence illustration, with period and actual sample beside each bar."""
    from news_pipeline import _preview_directory
    from cohort_policy import validate_selection_evidence, baseline_cell
    evidence = card.get('selection_evidence') or {}
    if (not validate_selection_evidence(evidence)['ok']
            or card.get('story_cell') != baseline_cell(evidence)
            or (card.get('instrument') or {}).get('semantics', {}).get('measurement') != 'adjusted_price_return'):
        raise ValueError('Comparison illustration requires verified adjusted-price evidence')
    views = [('Main annual history', evidence['baseline']['summary']),
             ('Matching cycle years\ninside that period', evidence['cycle']['within_baseline']['summary']),
             ('Earlier matching\ncycle years', evidence['cycle']['earlier']['summary'])]
    caption = ('Data illustration: the same calendar window across different historical samples. '
               'Matching cycle years inside the main period also belong to the annual sample.')
    for length in ('5', '10'):
        recent = evidence['recent'][length]
        if recent['comparison'] == 'contrast':
            views = [('Main annual history', evidence['baseline']['summary']),
                     (f'Most recent {length}\nannual observations', recent['recent']),
                     ('Preceding annual\nobservations', recent['preceding'])]
            caption = ('Data illustration: the same calendar window in recent and preceding annual observations. '
                       'Those two disjoint subsets together form the main annual sample.')
            break
    views = [(label, s) for label, s in views if s['n']]
    if len(views) != 3:
        raise ValueError('Three-view hero unavailable for this sparse comparison; use an annual chart or omit the draft hero')
    directory = _preview_directory(output_dir)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    with plt.rc_context({'font.family':'DejaVu Sans','font.size':14,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.spines.left':False}):
        fig, ax = plt.subplots(figsize=(12,6.3),dpi=150)
        values = [s['median_net_display'] for _,s in views]
        bars = ax.barh(range(len(views)), values, color=['#32658c' if v>=0 else '#b46f35' for v in values],height=.5,zorder=3)
        labels = [f"{label}\n{s['first_year']}–{s['last_year']} · n={s['n']}" for label,s in views]
        ax.set_yticks(range(len(views)), labels)
        ax.invert_yaxis()
        ax.axvline(0,color='#35424c',linewidth=1)
        ax.xaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
        ax.set_xlabel('Median historical change')
        ax.grid(axis='x',color='#d8dfe4',linewidth=.6,zorder=0)
        low, high = min([0]+values), max([0]+values)
        pad=max((high-low)*.22,1)
        ax.set_xlim(low-pad,high+pad)
        for bar,v in zip(bars, values):
            ax.annotate(f'{v:+.2f}%', (v,bar.get_y()+bar.get_height()/2),
                        xytext=(7 if v>=0 else -7,0),textcoords='offset points',
                        ha='left' if v>=0 else 'right',va='center',fontsize=15,fontweight='bold')
        fig.suptitle(f"{card['symbol']}: three views of the same window",x=.04,ha='left',fontsize=22,fontweight='bold',y=.98)
        window=evidence['window']
        fig.text(.04,.88,f"{window['start_date'][5:]} to {window['end_date'][5:]} · {window['calendar_days']} calendar days, inclusive",fontsize=15)
        fig.text(.04,.025,'Adjusted-price changes · Samples overlap · Historical results, not forecasts',fontsize=12,color='#51616e')
        fig.tight_layout(rect=(.02,.06,.98,.87))
        path=directory/'comparison-hero.png'
        fig.savefig(path,metadata={'Software':'SMN private comparison illustration'})
        plt.close(fig)
    source_path=directory/'comparison-hero-evidence.json'
    source_path.write_text(json.dumps({'identity':evidence['identity'],'window':evidence['window'],
                                      'views':views,'overlap':evidence['overlap']},indent=2),encoding='utf-8')
    mobile = _render_mobile_comparison(card['symbol'], views, window, (low-pad, high+pad), directory)
    evidence_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    mobile['evidence_sha256'] = evidence_hash
    result={'path':str(path),'url':path.as_uri(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'width':1800,'height':945,'mobile':mobile,
            'alt':f"{card['symbol']} median historical changes: " + '; '.join(label.replace('\n',' ') for label,_ in views),
            'caption':caption,
            'provenance':{'kind':'illustration','source_url':source_path.as_uri(),'credit':'SMN / supplied TradeWave historical observations'},
            'evidence_sha256':evidence_hash}
    (directory/'comparison-hero.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
