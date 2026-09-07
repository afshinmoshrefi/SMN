"""Final byte-bound visual inspection for the private visual newsroom stage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from visual_evidence import chart_data, digest, validate_bundle


def inspect_edition(result, bundle, directory, review, *, trusted_reviewers=()):
    """Validate a real inspector's observations; do not manufacture observations.

    A recorded human/vision inspection must cover final desktop/mobile views,
    every chosen chart and the actual hero. Asset mutation invalidates it.
    This checks recorded evidence, not whether an inspector's judgment is right.
    """
    from reader_promise import review_hero
    b = validate_bundle(bundle)
    root = Path(directory)
    article = result.get('article_html', '')
    sha = hashlib.sha256(article.encode()).hexdigest()
    issues = []
    if b.get('edition_type') == 'seasonal':
        from seasonal_edition import inspect_native
        if not result.get('seasonal'):
            issues.append('seasonal_contract_missing')
        else:
            issues.extend(inspect_native(result['seasonal'],root,article,review,b))
    if result.get('text_ready') is not True or not article:
        issues.append('upstream_text_not_ready')
    if (root/'article.html').read_text(encoding='utf-8') != article:
        issues.append('article_file_changed')
    editorial = (result.get('reviews') or [{}])[-1]
    if (editorial.get('article_sha256') != sha or editorial.get('evidence_sha256') != b['evidence_sha256']
            or editorial.get('passed') is not True):
        issues.append('editorial_binding_stale')
    if review.get('evidence_sha256') != b['evidence_sha256'] or review.get('article_sha256') != sha:
        issues.append('visual_binding_stale')
    hero = result.get('hero') or {}
    # Resolve copied preview assets on the machine performing actual inspection.
    local_hero = {**hero, 'path': str(root / hero.get('url', 'missing'))}
    hero_check = review_hero(article, local_hero, review, trusted_reviewers=trusted_reviewers)
    if not hero_check['ready']:
        issues.extend(hero_check.get('issues', []) + hero_check.get('pending', []))
    manifest = json.loads((root/'chart-manifest.json').read_text(encoding='utf-8'))
    chosen = [s['chart_id'] for s in result.get('article', {}).get('sections', []) if s.get('chart_id')]
    checks = review.get('chart_reviews') or []
    if len(checks) != len(chosen) or {c.get('chart_id') for c in checks} != set(chosen):
        issues.append('all_charts_need_inspection')
    catalog = {c['id']:c for c in b['charts']}
    for cid in chosen:
        asset = manifest[cid]
        if (asset.get('evidence_sha256') != b['evidence_sha256'] or asset.get('chart') != catalog[cid]
                or asset.get('data_sha256') != digest(chart_data(catalog[cid], b))):
            issues.append('chart_data_changed:' + cid)
        check = next((c for c in checks if c.get('chart_id') == cid), {})
        if check.get('verdict') != 'pass' or any(len(check.get(k, '').strip()) < 30 for k in ('numeric_observation', 'desktop_observation', 'mobile_observation')):
            issues.append('chart_observations_required:' + cid)
        if check.get('file_sha256') != asset['file_sha256']:
            issues.append('chart_inspection_hashes_stale:' + cid)
        for filename, expected in asset['file_sha256'].items():
            path = root/'assets'/filename
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                issues.append('chart_file_changed:' + filename)
        for variant in ('desktop_png', 'mobile_png'):
            if 'assets/' + asset['paths'][variant] not in article:
                issues.append('reviewed_chart_not_rendered:' + cid + ':' + variant)
    return {'ready': not issues, 'publishable': False, 'status': 'private_preview_ready' if not issues else 'hold',
            'issues': issues, 'article_sha256': sha, 'evidence_sha256': b['evidence_sha256'],
            'hero': hero_check, 'inspected_chart_ids': chosen}
