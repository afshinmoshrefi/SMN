"""Retired independent seasonal-price calculation entry points.

September 9 owner requirement: consume TradeWave outputs, never recalculate
its mathematics. The previous local daily-price/median implementation was
incorrectly introduced by the editorial upgrade and has been removed.
"""
from visual_evidence import digest


def derive(*args, **kwargs):
    raise ValueError('Independent price-path calculation is prohibited; obtain TradeWave engine results')


def attach(*args, **kwargs):
    raise ValueError('Independent price-path inputs are prohibited; use engine_seasonal.prepare')


def render(*args, **kwargs):
    raise ValueError('Legacy derived path cannot render; use unchanged TradeWave chart points')


def validate(data, card):
    from engine_seasonal import AUTHORITY, validate_card
    validate_card(card)
    if data.get('authority') != AUTHORITY or data.get('evidence_sha256') != digest(card['price_path']):
        raise ValueError('TradeWave price evidence changed')
    return True


def figure_html(native):
    from engine_seasonal import figure_html as authoritative
    validate(native['price_path'], native['card'])
    return authoritative(native, 'price_projection')
