#!/usr/bin/env python3
"""Replay real article prose + research through the citation pipeline."""
import sys, json, os, glob

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')

import angle_chrome

BASE = '/home/flask/blog/audit/2026/08/21'

for run in sorted(glob.glob(BASE + '/*/')):
    prose_p = os.path.join(run, 'prose_revised.html')
    if not os.path.exists(prose_p):
        prose_p = os.path.join(run, 'prose.html')
    res_p = os.path.join(run, 'research.json')
    if not (os.path.exists(prose_p) and os.path.exists(res_p)):
        continue

    prose = open(prose_p, encoding='utf-8').read()
    try:
        research = json.load(open(res_p))
    except Exception:
        continue
    if not isinstance(research, dict):
        continue

    out, cited = angle_chrome.renumber_citations(prose, research)
    sources_html = angle_chrome.render_sources(cited, research)
    n_items = sources_html.count('<li>')
    sym = os.path.basename(run.rstrip('/')).split('_')[0]
    avail = [s.get('id') for s in research.get('sources', []) if isinstance(s, dict)]
    print('%-6s research_ids=%-22s cited=%-14s sources_rendered=%d'
          % (sym, avail, cited, n_items))
