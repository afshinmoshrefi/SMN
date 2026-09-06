"""Verify selected chart metadata before giving it an article's evidence label.

This module does not load, render, or fetch images. Chartkit's manifest is the
authority for what was drawn; the article card is the authority for its sample.
Legacy manifests without semantics remain usable, but explicit disagreements
must not be hidden by replacing a figure caption with the card's numbers.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from article_evidence import build_cell_evidence, finite_number, inclusive_window


def chart_source_sha256(card: dict) -> str:
    cell = card['story_cell']
    source = {'instrument': {k: (card.get('instrument') or {}).get(k) for k in
                            ('resource_id', 'provider', 'exchange', 'symbol', 'series_id', 'semantics')},
              'anchor_date': cell['anchor_date'], 'days': cell['days'], 'years': cell['years'],
              'per_year': cell['per_year']}
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def chart_accounting_basis(image: dict) -> str:
    """Return 'short', 'long', or 'unknown' from the chart itself.

    A bearish seasonal bias does not establish short-side accounting. The
    explicit renderer direction wins; a legacy caption can establish short
    accounting only when it actually says short-side or shorting.
    """
    semantics = image.get('semantics') or {}
    if isinstance(semantics, dict):
        direction = str(semantics.get('direction') or '').strip().lower()
        if direction in ('long', 'short'):
            return direction
    text = ' '.join(str(image.get(key) or '') for key in ('caption', 'alt'))
    if re.search(r'\b(?:short[ -]side|shorting)\b', text, re.I):
        return 'short'
    return 'unknown'


def _date(value: Any) -> date | None:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _story_identity(card: dict) -> tuple[int | None, dict]:
    """Prefer recomputed observations, with a compatible legacy-card fallback."""
    cell = card.get('story_cell') or {}
    if cell.get('per_year'):
        try:
            evidence = build_cell_evidence(cell)
            return evidence['cohort']['n'], evidence['window']
        except (KeyError, TypeError, ValueError, OverflowError):
            pass  # the separate evidence gate rejects malformed cards
    n = finite_number(cell.get('n'))
    n = int(n) if n is not None and n.is_integer() and n >= 0 else None
    try:
        window = inclusive_window(cell['anchor_date'], cell['days'])
    except (KeyError, TypeError, ValueError, OverflowError):
        window = {}
    return n, window


def validate_chart_evidence(card: dict, images: list[dict] | None,
                            selected_variants: list[str] | None = None) -> dict:
    """Validate explicit n/start/end metadata for selected seasonal figures.

    Pass the plan's selected chart names. None checks all supplied seasonal
    charts, while an empty list checks none. Price and price_proj_* figures
    show broader market context and are deliberately exempt. Missing legacy
    semantics/individual fields are not invented or treated as a mismatch.
    """
    n, window = _story_identity(card)
    strict = card.get('selection_evidence') is not None
    cell = card.get('story_cell') or {}
    selected = set(selected_variants) if selected_variants is not None else None
    errors = []
    supplied = {str(i.get('variant')) for i in images or [] if isinstance(i, dict)}
    if strict and selected is not None:
        for missing in sorted(selected - supplied):
            errors.append({'code': 'CHART_NOT_SUPPLIED', 'detail': f'{missing} has no supplied renderer artifact'})
    for image in images or []:
        if not isinstance(image, dict):
            continue
        variant = str(image.get('variant') or '')
        if not variant:
            continue
        if selected is not None and variant not in selected:
            continue
        contextual = variant == 'price' or variant.startswith('price_proj_')
        if contextual and not strict:
            continue
        semantics = image.get('semantics')
        if semantics is None:
            if strict:
                errors.append({'code': 'CHART_EVIDENCE_MISSING',
                               'detail': f'{variant} needs renderer identity and sample metadata in private previews'})
            continue
        if not isinstance(semantics, dict):
            errors.append({'code': 'CHART_EVIDENCE_INVALID',
                           'detail': f'{variant} chart has malformed renderer metadata'})
            continue
        if strict:
            if contextual:
                errors.append({'code': 'CHART_CONTEXT_NOT_SUPPORTED',
                               'detail': 'Private historical previews currently accept percent-only sample charts, not raw-price or projected-price context'})
            expected_measurement = (card.get('instrument') or {}).get('semantics', {}).get('measurement')
            if not expected_measurement or semantics.get('measurement') != expected_measurement:
                errors.append({'code': 'CHART_MEASUREMENT_MISMATCH',
                               'detail': f'{variant} measurement is not bound to the reviewed instrument'})
            try:
                bound = semantics.get('source_sha256') == chart_source_sha256(card)
                actual = hashlib.sha256(Path(image.get('path', '')).read_bytes()).hexdigest()
                bound = bound and actual == image.get('sha256')
            except (OSError, TypeError, ValueError, KeyError):
                bound = False
            if not bound:
                errors.append({'code': 'CHART_ARTIFACT_UNBOUND',
                               'detail': f'{variant} needs matching local raster bytes and the exact percent-only source hash'})
            for field, expected in (('symbol', card.get('symbol')), ('resource_id', card.get('resource_id'))):
                if str(semantics.get(field, '')) != str(expected):
                    errors.append({'code': 'CHART_INSTRUMENT_MISMATCH',
                                   'detail': f'{variant} {field} does not match the selected instrument'})
            if not contextual:
                required = {'n', 'window_start', 'window_end', 'years', 'observed_years', 'direction'}
                if required - semantics.keys():
                    errors.append({'code': 'CHART_EVIDENCE_MISSING',
                                   'detail': f'{variant} is missing required sample/return metadata'})
                if (not isinstance(semantics.get('years'), str)
                        or semantics.get('years') != cell.get('years')):
                    errors.append({'code': 'CHART_LOOKBACK_MISMATCH',
                                   'detail': f'{variant} lookback differs from the fixed article history'})
                expected_years = sorted(r['year'] for r in cell.get('per_year', []))
                actual_years = semantics.get('observed_years')
                if not isinstance(actual_years, list) or actual_years != expected_years:
                    errors.append({'code': 'CHART_YEARS_MISMATCH',
                                   'detail': f'{variant} does not plot the exact article observation years'})
                if semantics.get('direction') != 'long':
                    errors.append({'code': 'CHART_RETURN_BASIS_MISMATCH',
                                   'detail': f'{variant} must plot underlying price returns, not an inferred short trade'})
        if contextual:
            continue
        if 'n' in semantics:
            observed = finite_number(semantics['n'])
            if observed is None or not observed.is_integer() or observed < 0:
                errors.append({'code': 'CHART_EVIDENCE_INVALID',
                               'detail': f'{variant} chart has an invalid observation count'})
            elif n is not None and int(observed) != n:
                errors.append({'code': 'CHART_SAMPLE_MISMATCH',
                               'detail': f'{variant} chart plots {int(observed)} observations; '
                                         f'the article evidence contains {n}'})
        for field, expected_key in (('window_start', 'start_date'), ('window_end', 'end_date')):
            if field not in semantics:
                continue
            observed = _date(semantics[field])
            expected = _date(window.get(expected_key))
            if observed is None:
                errors.append({'code': 'CHART_EVIDENCE_INVALID',
                               'detail': f'{variant} chart has an invalid {field}'})
            elif expected is not None and observed != expected:
                errors.append({'code': 'CHART_WINDOW_MISMATCH',
                               'detail': f'{variant} chart {field} is {observed.isoformat()}; '
                                         f'the article evidence requires {expected.isoformat()}'})
    return {'ok': not errors, 'errors': errors}
