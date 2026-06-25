#!/usr/bin/env python3
"""
send_daily_ai_pick.py
=====================
Sends the daily AI pick email to the DAILY_AI_PICK MailerLite group.

Reads today's pick from featured_history.json (written by generate_home_page.py),
pulls the latest closed result and running stats from the same data,
and sends a dark-themed HTML email via MailerLite.

Usage:
    python send_daily_ai_pick.py           # send today's pick
    python send_daily_ai_pick.py --force   # bypass already-sent check
    python send_daily_ai_pick.py --test    # print HTML to stdout, don't send

Crontab:
    30 7 * * 1-5 cd /home/flask/blog && python send_daily_ai_pick.py >> /home/flask/blog/logs/daily_ai_pick.log 2>&1
"""

import json
import sys
import argparse
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config
from email_tools import get_email_groups, create_campaign, schedule_campaign, future_date_hour_min

# =============================================================================
# Configuration
# =============================================================================

FEATURED_HISTORY_FILE = "/home/flask/blog/featured_history.json"
STATE_FILE = "/home/flask/blog/logs/sent_daily_ai_pick.json"
LOG_FILE = "/home/flask/blog/logs/send_daily_ai_pick.log"

FROM_NAME = "TradeWave AI"
FROM_EMAIL = "info@tradewave.ai"
REPLY_TO = "info@tradewave.ai"
GROUP_NAME = "DAILY_AI_PICK"

DOMAIN_ROOT = config.domain_root.rstrip('/')
SCORECARD_URL = "%s/scorecard" % DOMAIN_ROOT

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)


# =============================================================================
# State tracking
# =============================================================================

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def already_sent_today(state):
    return state.get('last_sent') == date.today().isoformat()


# =============================================================================
# Data loading
# =============================================================================

def load_history():
    try:
        with open(FEATURED_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def get_todays_pick(history):
    """Get today's featured pick."""
    today_str = date.today().isoformat()
    for entry in history:
        if entry.get('featured_date') == today_str:
            return entry
    return None


def get_latest_closed(history):
    """Get the most recently closed pick with an actual return."""
    closed = [
        e for e in history
        if e.get('status') == 'closed' and e.get('actual_return') is not None
    ]
    if not closed:
        return None
    closed.sort(key=lambda x: x.get('featured_date', ''), reverse=True)
    return closed[0]


def compute_stats(history):
    """Compute running track record stats."""
    closed = [e for e in history if e.get('status') == 'closed' and e.get('actual_return') is not None]
    total = len(history)
    wins = [e for e in closed if e.get('win')]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    avg_return = (sum(e['actual_return'] for e in closed) / len(closed)) if closed else 0
    return {
        'total': total,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
    }


# =============================================================================
# Email HTML
# =============================================================================

def build_email_html(pick, latest_closed, stats):
    """Build the dark-themed HTML email body."""
    symbol = pick['symbol']
    direction = 'Long' if pick['direction'] == 'l' else 'Short'
    win_prob = '%.1f' % (pick['win_prob'] * 100)
    pred_return = '%.1f' % pick['pred_return']
    days = pick['daysOut']
    wave_url = pick.get('wave_viewer_url', '')

    # Use production domain for wave viewer URL
    if DOMAIN_ROOT not in wave_url:
        pattern_param = pick.get('pattern_param', '')
        wave_url = '%s/wave-viewer?o=%s' % (DOMAIN_ROOT, pattern_param)

    # Latest result section
    latest_html = ''
    if latest_closed:
        lc_symbol = latest_closed['symbol']
        lc_dir = 'Long' if latest_closed['direction'] == 'l' else 'Short'
        lc_pred = '%.1f' % latest_closed.get('pred_return', 0)
        lc_actual = '%.1f' % latest_closed.get('actual_return', 0)
        lc_won = latest_closed.get('win', False)
        lc_wl = 'Won' if lc_won else 'Lost'
        lc_wl_color = '#10b981' if lc_won else '#ef4444'
        lc_actual_sign = '+' if latest_closed.get('actual_return', 0) >= 0 else ''

        latest_html = '''
        <tr><td class="email-padding" style="padding:24px 32px 0;">
            <table width="100%%" cellpadding="0" cellspacing="0" style="background:#111936;border-radius:8px;border:1px solid #1f2937;">
                <tr><td style="padding:16px 20px;">
                    <p style="margin:0;font-size:15px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Latest Result</p>
                    <p style="margin:6px 0 0;font-size:18px;color:#fff;line-height:1.5;">
                        $%s %s | Predicted +%s%% | Actual: %s%s%% |
                        <span style="color:%s;font-weight:700;">%s</span>
                    </p>
                </td></tr>
            </table>
        </td></tr>''' % (lc_symbol, lc_dir, lc_pred, lc_actual_sign, lc_actual, lc_wl_color, lc_wl)

    # Track record line
    track_sign = '+' if stats['avg_return'] >= 0 else ''
    track_html = '''
        <tr><td class="email-padding" style="padding:20px 32px;">
            <p style="margin:0;text-align:center;font-size:17px;color:#9ca3af;">
                %d picks | %.1f%% win rate | Avg return: %s%.1f%%
            </p>
            <p style="margin:8px 0 0;text-align:center;">
                <a href="%s" style="color:#6366f1;font-size:16px;text-decoration:none;">Full scorecard &rarr;</a>
            </p>
        </td></tr>''' % (stats['total'], stats['win_rate'], track_sign, stats['avg_return'], SCORECARD_URL)

    html = '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
@media only screen and (max-width: 620px) {
    .email-container { width: 100%% !important; }
    .email-padding { padding-left: 20px !important; padding-right: 20px !important; }
    .email-ticker { font-size: 28px !important; }
    .email-stats { font-size: 16px !important; }
    .email-cta { padding: 16px 28px !important; font-size: 16px !important; }
}
</style>
</head>
<body style="margin:0;padding:0;background-color:#0f0a15;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-text-size-adjust:100%%;-ms-text-size-adjust:100%%;">
<table width="100%%" cellpadding="0" cellspacing="0" style="background-color:#0f0a15;">
<tr><td align="center" style="padding:24px 16px;">
    <table class="email-container" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%%;background:#19162b;border-radius:12px;border:1px solid #1f2937;">

        <!-- Header -->
        <tr><td class="email-padding" style="padding:32px 32px 8px;text-align:center;">
            <p style="margin:0;font-size:16px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;">Today's AI Pick</p>
        </td></tr>

        <!-- Ticker -->
        <tr><td class="email-padding" style="padding:8px 32px 4px;text-align:center;">
            <p class="email-ticker" style="margin:0;font-size:42px;font-weight:800;color:#fff;">$%s <span style="color:#6366f1;">%s</span></p>
        </td></tr>

        <!-- Stats line -->
        <tr><td class="email-padding" style="padding:4px 32px 8px;text-align:center;">
            <p class="email-stats" style="margin:0;font-size:20px;color:#9ca3af;line-height:1.5;">
                %s%% win probability
                <span style="color:rgba(255,255,255,0.2);margin:0 6px;">|</span>
                Projected +%s%% in %d days
            </p>
        </td></tr>

        <!-- Tagline -->
        <tr><td class="email-padding" style="padding:0 32px 24px;text-align:center;">
            <p style="margin:0;font-size:16px;color:#6b7280;">Selected from 100 years of seasonal data</p>
        </td></tr>

        <!-- CTA Button -->
        <tr><td class="email-padding" style="padding:0 32px 24px;text-align:center;">
            <a class="email-cta" href="%s" style="display:inline-block;padding:18px 44px;background:#6366f1;color:#fff;font-size:20px;font-weight:600;text-decoration:none;border-radius:10px;">See full seasonal analysis</a>
        </td></tr>

        <!-- Divider -->
        <tr><td style="padding:0 32px;"><hr style="border:none;border-top:1px solid #1f2937;margin:0;"></td></tr>

        %s

        %s

        <!-- Footer -->
        <tr><td class="email-padding" style="padding:20px 32px 24px;text-align:center;">
            <p style="margin:0;font-size:14px;color:#4b5563;line-height:1.7;">
                TradeWave is a research platform, not a brokerage. All data is based on historical analysis for informational purposes only. Past performance does not guarantee future results.
            </p>
        </td></tr>

    </table>
</td></tr>
</table>
</body>
</html>''' % (symbol, direction, win_prob, pred_return, days, wave_url, latest_html, track_html)

    return html


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Bypass already-sent check')
    parser.add_argument('--test', action='store_true', help='Print HTML, do not send')
    args = parser.parse_args()

    today_str = date.today().isoformat()
    print("[DAILY AI PICK] %s" % today_str)
    log.info("Starting daily AI pick email for %s" % today_str)

    # Check if already sent
    state = load_state()
    if not args.force and not args.test and already_sent_today(state):
        print("  Already sent today, skipping. Use --force to resend.")
        log.info("Already sent today, skipping.")
        return

    # Load data
    history = load_history()
    if not history:
        print("  No featured history found. Exiting.")
        log.warning("No featured history.")
        return

    pick = get_todays_pick(history)
    if not pick:
        print("  No pick for today. Run generate_home_page.py first.")
        log.warning("No pick for today.")
        return

    print("  Pick: $%s %s (wp=%.1f%%, pred=+%.1f%%)" % (
        pick['symbol'],
        'Long' if pick['direction'] == 'l' else 'Short',
        pick['win_prob'] * 100,
        pick['pred_return']
    ))

    latest_closed = get_latest_closed(history)
    if latest_closed:
        print("  Latest closed: $%s, actual=%.1f%%, %s" % (
            latest_closed['symbol'],
            latest_closed.get('actual_return', 0),
            'Won' if latest_closed.get('win') else 'Lost'
        ))
    else:
        print("  No closed picks yet.")

    stats = compute_stats(history)
    print("  Track record: %d picks, %.1f%% win rate, %.1f%% avg return" % (
        stats['total'], stats['win_rate'], stats['avg_return']
    ))

    # Build email
    html = build_email_html(pick, latest_closed, stats)

    if args.test:
        print("\n--- EMAIL HTML ---")
        print(html)
        print("--- END ---")
        return

    # Get group ID
    groups = get_email_groups()
    group_id = groups.get(GROUP_NAME)
    if not group_id:
        print("  ERROR: Group '%s' not found in MailerLite." % GROUP_NAME)
        log.error("Group %s not found." % GROUP_NAME)
        return

    # Build subject
    direction = 'Long' if pick['direction'] == 'l' else 'Short'
    subject = "TradeWave AI Pick: $%s %s | %.1f%% win rate | +%.1f%% projected" % (
        pick['symbol'], direction, pick['win_prob'] * 100, pick['pred_return']
    )

    # Create campaign
    campaign_name = "Daily AI Pick %s - %s" % (today_str, pick['symbol'])
    print("  Creating campaign: %s" % campaign_name)
    log.info("Creating campaign: %s" % campaign_name)

    try:
        campaign_id, campaign_time = create_campaign(
            campaign_name, subject, FROM_NAME, FROM_EMAIL, group_id, html
        )
        print("  Campaign created: ID=%s" % campaign_id)
        log.info("Campaign created: ID=%s" % campaign_id)
    except Exception as e:
        print("  ERROR creating campaign: %s" % e)
        log.error("Campaign creation failed: %s" % e)
        return

    # Schedule for 2 minutes from now
    send_date, send_hour, send_minute = future_date_hour_min(2)
    try:
        schedule_campaign(campaign_id, send_date, send_hour, send_minute)
        print("  Scheduled for %s %s:%s" % (send_date, send_hour, send_minute))
        log.info("Scheduled for %s %s:%s" % (send_date, send_hour, send_minute))
    except Exception as e:
        print("  ERROR scheduling: %s" % e)
        log.error("Scheduling failed: %s" % e)
        return

    # Update state
    state['last_sent'] = today_str
    state['last_symbol'] = pick['symbol']
    state['last_campaign_id'] = campaign_id
    save_state(state)

    print("  Done!")
    log.info("Done. Sent %s to %s group." % (pick['symbol'], GROUP_NAME))


if __name__ == "__main__":
    main()
