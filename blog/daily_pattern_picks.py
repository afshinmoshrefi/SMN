#!/usr/bin/env python3
"""
daily_pattern_picks.py
======================
Selects top ML-scored seasonal pattern picks by calling the ML scorer's
/select endpoint on keyprovider.

Usage as module:
    from daily_pattern_picks import get_daily_picks
    picks = get_daily_picks(
        date='2026-03-17',
        resource_ids=['2', '11'],
        num_picks=1,
        direction='l',
        days_out_min=10,
        days_out_max=30,
        min_avg_return=5.0,
        min_win_prob=0.80,
        exclude_symbols=['AAPL'],
    )

Smoke test (just run it):
    python daily_pattern_picks.py
"""

import sys
import json
import logging
import requests
from datetime import datetime, date, timedelta

sys.path.insert(0, '/home/flask')
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)


def get_daily_picks(date, resource_ids, num_picks, direction, days_out_min,
                    days_out_max, min_avg_return, min_win_prob,
                    exclude_symbols=None):
    """
    Get top ML-scored pattern picks for a given date.

    Args:
        date: str 'YYYY-MM-DD'
        resource_ids: list of market IDs to search (e.g. ['2', '11'])
        num_picks: int, number of picks to return (0 = all qualifying)
        direction: 'l' for long, 's' for short, 'both'
        days_out_min: minimum holding period in days
        days_out_max: maximum holding period in days
        min_avg_return: minimum historical avg_profit percentage
        min_win_prob: minimum ML win probability (e.g. 0.80)
        exclude_symbols: list of symbols to skip (optional)

    Returns:
        dict with 'picks' list and metadata from ML scorer
    """
    payload = {
        'date': date,
        'resource_ids': [str(r) for r in resource_ids],
        'num_picks': num_picks,
        'direction': direction,
        'days_out_min': days_out_min,
        'days_out_max': days_out_max,
        'min_avg_return': min_avg_return,
        'min_win_prob': min_win_prob,
        'exclude_symbols': exclude_symbols or [],
    }

    url = f'{config.ml_scorer_url}/select'
    log.info(f'Calling {url} for {date}, {len(resource_ids)} markets, '
             f'{direction} {days_out_min}-{days_out_max}d, min_ret={min_avg_return}%, '
             f'min_wp={min_win_prob:.0%}, num_picks={num_picks}')

    resp = requests.post(url, json=payload, timeout=300)
    resp.raise_for_status()
    result = resp.json()

    log.info(f'Pre-filter: {result.get("candidates_after_prefilter", 0)}, '
             f'scored: {result.get("candidates_scored", 0)}, '
             f'qualifying: {result.get("candidates_passing_win_prob", 0)}, '
             f'picks: {len(result.get("picks", []))}, '
             f'elapsed: {result.get("elapsed_ms", 0):.0f}ms')

    return result


def _print_table(result):
    """Print picks as a formatted table."""
    picks = result.get('picks', [])
    if not picks:
        print('No qualifying picks found.')
        return

    print(f'\n{"#":<4} {"Dir":<4} {"Symbol":<8} {"Days":<5} {"WinPr":<7} '
          f'{"PrRet":<7} {"PrMFE":<7} {"AvgPr":<7} {"AvgPr2":<7} '
          f'{"SR":<6} {"SR2":<6} {"ML":<5}')
    print('-' * 78)
    for i, p in enumerate(picks, 1):
        d = 'L' if p['direction'] == 'l' else 'S'
        print(f'{i:<4} {d:<4} {p["symbol"]:<8} {p["daysOut"]:<5} '
              f'{p["win_prob"]:<7.1%} {p["pred_return"]:<7.1f} '
              f'{p["pred_mfe"]:<7.1f} {p["avg_profit"]:<7.1f} '
              f'{p["avg_profit2"]:<7.1f} {p["sharpe_ratio"]:<6.2f} '
              f'{p["sharpe_ratio2"]:<6.2f} {p["ml_score"]:<5.1f}')

    print(f'\nCandidates: {result.get("candidates_after_prefilter", 0)} pre-filter, '
          f'{result.get("candidates_scored", 0)} scored, '
          f'{result.get("candidates_passing_win_prob", 0)} qualifying')
    print(f'Elapsed: {result.get("elapsed_ms", 0):.0f}ms')


# =============================================================================
# Smoke test: just run the script to see all qualifying picks for today
# =============================================================================

if __name__ == '__main__':
    # Find next weekday (today if weekday, else next Monday)
    today = date.today()
    if today.weekday() >= 5:  # Saturday or Sunday
        today = today + timedelta(days=(7 - today.weekday()))
    target = today.strftime('%Y-%m-%d')

    print(f'Daily Pattern Picks - {target}')
    print(f'ML Scorer: {config.ml_scorer_url}')
    print('=' * 78)

    result = get_daily_picks(
        date=target,
        resource_ids=['2'],         # S&P 500
        num_picks=0,                # all qualifying
        direction='both',           # long and short
        days_out_min=10,
        days_out_max=30,
        min_avg_return=5.0,
        min_win_prob=0.75,
    )

    _print_table(result)
