"""Responsive editorial charts from an immutable numerical ledger, not an LLM."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from pathlib import Path
import textwrap

from visual_evidence import chart_data, digest, format_value, validate_bundle

NAVY, TEAL, RED, GRAY = '#153344', '#237d79', '#a04742', '#a5b4bc'


def _identity(bundle):
    """Kicker naming the article's security, so the image stands on its own."""
    import chartkit
    contract = bundle.get('seasonal_contract') or {}
    if contract.get('company') or bundle.get('edition_type') == 'seasonal':
        return chartkit.identity_kicker(bundle.get('story_id', ''), contract.get('company', ''), 'Context')
    return bundle.get('category') or 'Seasonal Market News'


def _source_line(rows, bundle):
    """Short publisher names (bea.gov, nvidia.com); full titles stay in HTML."""
    from urllib.parse import urlparse
    sources = {s['id']: s for s in bundle['sources']}
    names = []
    for sid in dict.fromkeys(r['source_id'] for r in rows):
        url = sources[sid].get('url', '')
        host = urlparse(url).netloc.lower()
        host = host[4:] if host.startswith('www.') else host
        name = host or ('TradeWave' if sources[sid].get('source_type') == 'engine_export' else sources[sid]['title'])
        if name not in names:
            names.append(name)
    return 'Source: ' + ', '.join(names)


def render_chart(chart, bundle, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    import chartkit
    data = chart_data(chart, bundle)
    rows = data['rows']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths, hashes = {}, {}
    kicker, source = _identity(bundle), _source_line(rows, bundle)
    unit_label = {'percent': 'Percent', 'weight_percent': 'Portfolio weight (%)',
                  'thousand_jobs': 'Thousands of jobs'}.get(chart['unit'], chart['unit'])
    for variant in ('desktop', 'mobile'):
        compact = variant == 'mobile'
        # Same branded frame and ~12+ CSS px text as the TradeWave charts:
        # desktop shows at ~0.58x in the article column, phone at ~0.46x.
        w = 780 if compact else 1280
        if chart['kind'] == 'bars':
            plot_px = len(rows) * (118 if compact else 72) + 20
        else:
            plot_px = 600 if compact else 430
        bottom_px = 205 if compact else 140
        frame = dict(w=w, wrap_lines=(1, 2, 2))
        if compact:
            frame.update(sizes=(13, 20, 13), min_sizes=(11.5, 13, 11.5), source_size=11.5, wrap_lines=(2, 2, 2),
                         track_kicker=False, header_frac=.89)
        aw = 0.89 if compact else 0.905
        label_size, value_size, tick_size = (14, 14, 14) if compact else (12, 12.5, 11)
        with plt.rc_context({'svg.fonttype': 'none', 'text.color': NAVY,
                             'axes.labelcolor': NAVY, 'xtick.color': NAVY, 'ytick.color': NAVY}):
            # Measure the (possibly wrapped) header first, then size the canvas
            # so the plot keeps its full height under it.
            probe, _ = chartkit.new_frame(kicker, chart['title'], chart['subtitle'], source,
                                          h=1400, ax_rect=(0.065, bottom_px / 1400, aw, .3), **frame)
            header_px = (1 - probe.smn_header_bottom) * 1400
            plt.close(probe)
            h = int(header_px + 22 + plot_px + bottom_px)
            fig, ax = chartkit.new_frame(kicker, chart['title'], chart['subtitle'], source, h=h,
                                         ax_rect=(0.065, bottom_px / h, aw, plot_px / h), **frame)
            ax.tick_params(labelsize=tick_size)
            vals = [r['value'] for r in rows]
            if chart['kind'] == 'bars':
                colors = [GRAY if r['status'] == 'previous_estimate' else RED if r['value'] < 0 else TEAL for r in rows]
                ax.barh(range(len(rows)), vals, color=colors, height=.52, zorder=3)
                labels = [textwrap.fill(r['label'], 36 if compact else 30) for r in rows]
                ax.set_yticks([] if compact else range(len(rows)), [] if compact else labels)
                ax.invert_yaxis()
                lo, hi = min(0, min(vals)), max(0, max(vals))
                span = max(hi - lo, 1)
                ax.set_xlim(lo - span * (.40 if lo < 0 else 0),
                            hi + span * (.30 if compact else .22))
                if compact:
                    ax.set_ylim(len(rows)-.45, -.75)
                    for i, label in enumerate(labels):
                        ax.text(ax.get_xlim()[0], i-.36, label, ha='left', va='bottom', fontsize=label_size,
                                bbox={'facecolor':'white', 'edgecolor':'none', 'pad':1.5}, zorder=5)
                else:
                    # Make room on the left for the measured category labels.
                    fig.canvas.draw()
                    renderer = fig.canvas.get_renderer()
                    widest = max(t.get_window_extent(renderer=renderer).width for t in ax.get_yticklabels())
                    box = ax.get_position()
                    shift = (widest + 24) / w
                    ax.set_position((box.x0 + shift, box.y0, box.width - shift, box.height))
                    ax.tick_params(axis='y', labelsize=label_size)
                for i, r in enumerate(rows):
                    value = r['value']
                    ax.annotate(format_value(value, chart['unit']), (value, i),
                                xytext=(6 if value >= 0 else -6, 0), textcoords='offset points',
                                va='center', ha='left' if value >= 0 else 'right', weight='bold',
                                fontsize=value_size, color=NAVY)
                ax.xaxis.set_major_locator(MaxNLocator(4, integer=chart['unit'] == 'thousand_jobs'))
                ax.set_xlabel(unit_label, fontsize=tick_size, color=chartkit.PAL['muted'])
                ax.grid(False, axis='y')
                ax.grid(axis='x', color='#edf0f2', zorder=0)
                ax.axvline(0, color='#526b79', lw=.9)
                ax.spines[['top', 'right', 'left']].set_visible(False)
                ax.tick_params(axis='y', length=0, pad=10)
            else:
                plotted = [v / 1_000_000 for v in vals]
                ax.bar(range(len(rows)), plotted, color=[GRAY] * (len(rows)-1) + [TEAL], width=.7, zorder=3)
                ax.set_ylim(0, max(plotted) * 1.35 or 1)
                ax.set_xlim(-.7, len(rows)-.2)
                ax.set_ylabel('Million shares', fontsize=tick_size, color=chartkit.PAL['muted'])
                ticks = [0, 10, 20] if compact else [0, 9, 19, 20]
                ax.set_xticks(ticks, [rows[i]['label'] for i in ticks], fontsize=tick_size - 1)
                baseline = data['prior_median'] / 1_000_000
                ax.axhline(baseline, color=NAVY, ls=(0, (4, 3)), lw=1)
                ax.text(.02, .86, 'Prior 20-session median\n' + format_value(data['prior_median'], 'shares') + ' shares',
                        transform=ax.transAxes, fontsize=label_size - 1, va='top', color=NAVY)
                multiple = data['relative_volume']
                label = f'{multiple:.1f}× typical volume' if multiple is not None else 'Latest session'
                ax.annotate(format_value(vals[-1], 'shares'), (len(rows)-1, plotted[-1]), xytext=(-3, 9),
                            textcoords='offset points', ha='right', weight='bold', fontsize=value_size)
                ax.text(.02, .98, label, transform=ax.transAxes, fontsize=value_size, weight='bold', va='top', color=TEAL)
                ax.yaxis.set_major_locator(MaxNLocator(5))
                ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
                ax.grid(False, axis='x')
                ax.grid(axis='y', color='#edf0f2', zorder=0)
                ax.spines[['top', 'right', 'left']].set_visible(False)
            for ext in ('png', 'svg'):
                filename = f'{chart["id"]}-{variant}.{ext}'
                path = directory / filename
                fig.savefig(path, dpi=chartkit.DPI, facecolor='white', metadata={'Creator': 'SMN deterministic visual renderer'} if ext == 'svg' else None)
                paths[variant + '_' + ext] = filename
                hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
            plt.close(fig)
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=['id', 'label', 'value', 'unit', 'period', 'status', 'source_id', 'locator'], extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    csv_path = directory / (chart['id'] + '.csv')
    csv_path.write_text(output.getvalue(), encoding='utf-8')
    paths['csv'] = csv_path.name
    hashes[csv_path.name] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    return {'chart': chart, 'data': data, 'paths': paths, 'file_sha256': hashes,
            'evidence_sha256': bundle['evidence_sha256'], 'data_sha256': digest(data)}


def render_catalog(bundle, directory):
    b = validate_bundle(bundle)
    return {c['id']: render_chart(c, b, directory) for c in b['charts']}


def figure_html(asset, bundle):
    esc = html.escape
    c, data, paths = asset['chart'], asset['data'], asset['paths']
    source_ids = list(dict.fromkeys(r['source_id'] for r in data['rows']))
    sources = {s['id']: s for s in bundle['sources']}
    labels = '; '.join(f'{r["label"]}: {format_value(r["value"], c["unit"])}' for r in data['rows'])
    alt = c['title'] + '. Units: ' + c['unit'].replace('_', ' ') + '. ' + (labels if len(labels) < 350 else c['subtitle'] + '. Complete values in the data table below.')
    links = ', '.join(f'<a href="{esc(sources[s]["url"], quote=True)}">{esc(sources[s]["title"])}</a>' for s in source_ids)
    table = ''.join(f'<tr><th scope="row">{esc(r["label"])}</th><td>{esc(format_value(r["value"], c["unit"]))}</td>'
                    f'<td>{esc(r["period"])}</td><td>{esc(r["status"].replace("_", " "))}</td></tr>' for r in data['rows'])
    return (f'<figure class="data-figure" id="chart-{esc(c["id"])}" data-evidence-sha256="{asset["evidence_sha256"]}">'
            f'<div class="chart-eyebrow">The evidence</div><h3>{esc(c["title"])}</h3><p class="chart-subtitle">{esc(c["subtitle"])}</p>'
            f'<picture><source media="(max-width: 600px)" srcset="assets/{paths["mobile_png"]}" type="image/png">'
            f'<img src="assets/{paths["desktop_png"]}" alt="{esc(alt, quote=True)}" loading="lazy"></picture>'
            f'<figcaption>{esc(c["note"])}<span class="chart-source">Source: {links}.</span></figcaption>'
            f'<details class="chart-data"><summary>View chart data &amp; download</summary><div class="table-scroll"><table>'
            f'<caption>{esc(c["title"])} · {esc(c["unit"].replace("_", " "))}</caption>'
            '<thead><tr><th scope="col">Measure</th><th scope="col">Value</th><th scope="col">Period</th><th scope="col">Status</th></tr></thead>'
            f'<tbody>{table}</tbody></table></div><p><a href="assets/{paths["csv"]}" download>CSV data</a> · '
            f'<a href="assets/{paths["desktop_svg"]}">Full-size vector chart</a></p></details></figure>')
