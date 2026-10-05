#!/usr/bin/env python3
"""
send_smn_emails.py
==================
Sends daily (Mon–Fri) and weekly (Sunday) email campaigns to SMN subscribers
via MailerLite using AI-generated narratives.

  Daily  → SMN-DAILY group  — articles published today not yet sent
  Weekly → SMN-WEEKLY group — articles from the past 7 days not yet sent

Runs the correct send based on today's day automatically.
Run with --force to re-send even if already sent today (e.g. for testing).
Run with --test EMAIL to send a single test email without touching state.

Usage:
    python send_smn_emails.py                          # auto: daily Mon–Fri, weekly Sunday
    python send_smn_emails.py --force                  # bypass already-sent check
    python send_smn_emails.py --test you@example.com   # test send to one address

Crontab (webserver):
    0 7 * * *  cd /home/flask/blog && python send_smn_emails.py >> /home/flask/blog/logs/smn_email_cron.log 2>&1
"""

import json
import re
import sys
import argparse
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

sys.path.insert(0, '/home/flask')
import config
from email_tools import (get_email_groups, create_campaign,
                         create_mailerlite_group,
                         create_subscriber, assign_subscriber_to_a_group,
                         get_subscriber_by_email)
from AI_tools import send_openai_prompt

# =============================================================================
# Configuration
# =============================================================================
NEWS_ROOT  = Path(config.news_root_folder)
POSTS_JSON = NEWS_ROOT / 'posts.json'
STATE_FILE = Path('/home/flask/blog/logs/sent_smn_emails.json')
LOG_FILE   = '/home/flask/blog/logs/send_smn_emails.log'

FROM_NAME  = config.smn_from_name
FROM_EMAIL = config.smn_from_email

# Cap articles per email to keep length reasonable
MAX_DAILY_ARTICLES  = 8
MAX_WEEKLY_ARTICLES = 16
MAILERLITE_ACCOUNT_TIMEZONE = ZoneInfo('America/New_York')
MAILERLITE_CAMPAIGN_URL = 'https://connect.mailerlite.com/api/campaigns/'
MAILERLITE_TIMEZONES_URL = 'https://connect.mailerlite.com/api/timezones'

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)


# =============================================================================
# State tracking  (logs/sent_smn_emails.json)
# =============================================================================

def _load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            raise RuntimeError('Newsletter state unreadable; refusing to create a campaign') from exc
    return {
        'daily_sent': [], 'weekly_sent': [],
        'last_weekly_date': None,
        'last_daily_narrative': None,
        'last_weekly_narrative': None, 'campaigns': {},
    }


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=STATE_FILE.parent,
                                         prefix=STATE_FILE.name + '.', suffix='.tmp',
                                         delete=False) as f:
            temporary = Path(f.name)
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, STATE_FILE)
    except BaseException:
        if 'temporary' in locals():
            temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _newsletter_lock():
    """Serialize entrypoints across processes; the OS releases locks on exit."""
    lock_path = STATE_FILE.with_name(STATE_FILE.name + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as lock_file:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b'0')
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Newsletter sender already running') from exc
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Newsletter sender already running') from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _serialized_newsletter(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _newsletter_lock():
            return function(*args, **kwargs)
    return wrapped


def _reserved_slugs(state, kind):
    # The old *_sent arrays remain historical deduplication inputs. New records
    # say what the provider has actually acknowledged, without claiming delivery.
    slugs = set(state.get(f'{kind}_sent', []))
    for key, record in state.get('campaigns', {}).items():
        if key.startswith(kind + ':'):
            slugs.update(record.get('slugs', []))
    return slugs


def _today():
    return datetime.now(timezone.utc).astimezone(MAILERLITE_ACCOUNT_TIMEZONE).date()


# =============================================================================
# Posts loading & filtering
# =============================================================================

def _load_posts():
    if not POSTS_JSON.exists():
        return []
    with open(POSTS_JSON) as f:
        return json.load(f)


def _normalize_url(url):
    """
    Re-anchor a stored article URL to the current config.news_website_url.

    Articles are published with whatever news_website_url was in config at
    the time (could be a dev IP or staging domain).  This strips that host
    and rebuilds with the live configured base so emails always link to the
    correct public domain.
    """
    if not url:
        return url
    from urllib.parse import urlparse
    path = urlparse(url).path          # e.g. /articles/US/2026/02/27/slug.html
    base = config.news_website_url.rstrip('/')
    return base + path


def _public_url(article):
    return _normalize_url(article.get('url', ''))


def _hero_url(article):
    # Use stored hero_image URL if available (set for articles published after hero collision fix)
    if article.get('hero_image'):
        return _normalize_url(article['hero_image'])
    # Fallback: construct from symbol (legacy articles published before the fix)
    url    = article.get('url', '')
    symbol = article.get('symbol', '')
    if not url or not symbol:
        return None
    last_slash = url.rfind('/')
    if last_slash == -1:
        return None
    filename_symbol = symbol.replace('/', '_').replace('=', '_').upper()
    dir_url = _normalize_url(url[:last_slash + 1])
    return f"{dir_url}hero_{filename_symbol}.jpg"


def _filter_daily(posts, state):
    """Articles published today not yet included in a daily email."""
    today_str = _today().isoformat()
    sent      = _reserved_slugs(state, 'daily')
    results = [
        p for p in posts
        if p.get('published_date', '')[:10] == today_str
        and p.get('slug') not in sent
    ]
    results.sort(key=lambda x: x.get('published_date', ''), reverse=True)
    return results[:MAX_DAILY_ARTICLES]


def _filter_weekly(posts, state):
    """Articles from the past 7 days not yet included in a weekly email."""
    today      = _today()
    week_start = today - timedelta(days=7)
    sent       = _reserved_slugs(state, 'weekly')
    results    = []
    for p in posts:
        try:
            pub = date.fromisoformat(p.get('published_date', '')[:10])
        except Exception:
            continue
        if week_start <= pub <= today and p.get('slug') not in sent:
            results.append(p)
    results.sort(key=lambda x: x.get('published_date', ''), reverse=True)
    return results[:MAX_WEEKLY_ARTICLES]


# =============================================================================
# AI narrative generation
# =============================================================================

def _article_brief(a):
    direction = 'Bullish' if a.get('direction', '').lower() == 'long' else 'Bearish'
    dek       = (a.get('dek') or a.get('meta_description') or '')[:160].strip()
    return (
        f"• {a.get('symbol', '?')} ({direction}) — {a.get('title', '')}\n"
        f"  {a.get('pattern_days', '?')}-day pattern starting {a.get('pattern_start_date', '?')} "
        f"| {a.get('lookback_years', '?')} years of data\n"
        f"  {dek}"
    )


def _parse_gpt_json(raw, fallback_narrative):
    """Extract {subject, narrative} from GPT response, with fallback."""
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(r'^```[a-z]*\n?|\n?```$', '', raw.strip(), flags=re.MULTILINE)
        data = json.loads(cleaned)
        return data.get('subject', ''), data.get('narrative', fallback_narrative)
    except Exception:
        log.warning('GPT JSON parse failed, using raw as narrative. raw=%r', raw[:120])
        return '', fallback_narrative


def _generate_daily_narrative(articles, prev_narrative=None):
    """Returns (subject, narrative)."""
    briefs = '\n\n'.join(_article_brief(a) for a in articles)
    system = (
        "You are a sharp market analyst writing ultra-concise daily briefings for traders. "
        "Respond ONLY with valid JSON, no markdown, no code fences. "
        'Format: {"subject": "...", "narrative": "..."} '
        "Subject: compelling, news-like, specific — sounds like breaking intelligence, not a template. "
        "Max 9 words. "
        "Narrative: exactly 2-3 sentences, 40-60 words total. Lead with the sharpest angle. "
        "Name specific tickers. No filler, no 'Today we...', no newsletter-speak. "
        "Punchy — like a Bloomberg terminal alert, not a press release."
    )
    prompt = (
        f"Date: {date.today().strftime('%A, %B %d, %Y')}\n"
        f"Today's patterns:\n\n{briefs}\n\n"
        f"Write the subject and a 2-3 sentence briefing."
    )
    raw = send_openai_prompt(prompt, system=system, stream=False)
    return _parse_gpt_json(raw, raw)


def _generate_weekly_narrative(articles, prev_narrative=None):
    """Returns (subject, narrative)."""
    today      = date.today()
    week_start = (today - timedelta(days=6)).strftime('%B %d')
    week_end   = today.strftime('%B %d, %Y')
    briefs     = '\n\n'.join(_article_brief(a) for a in articles[:12])
    system = (
        "You are a sharp market analyst writing concise weekly recaps for traders. "
        "Respond ONLY with valid JSON, no markdown, no code fences. "
        'Format: {"subject": "...", "narrative": "..."} '
        "Subject: compelling, specific — sounds like a must-read market letter. Max 9 words. "
        "Narrative: 3-4 sentences, 70-90 words total. Identify the dominant theme of the week, "
        "name the strongest setups, end with one thing to watch next week. "
        "Sharp and credible — no filler, no newsletter-speak."
    )
    prompt = (
        f"Week of {week_start}–{week_end}\n"
        f"Patterns this week:\n\n{briefs}\n\n"
        f"Write the subject and a 3-4 sentence weekly recap."
    )
    raw = send_openai_prompt(prompt, system=system, stream=False)
    return _parse_gpt_json(raw, raw)


# =============================================================================
# Email HTML builder
# =============================================================================

def _direction_style(article):
    if article.get('direction', '').lower() == 'long':
        return '#0d7a3e', '#e6f4ec', 'BULLISH &#x2191;'
    return '#c41e3a', '#fce8eb', 'BEARISH &#x2193;'


def _article_card_html(article):
    pub_url = _public_url(article)
    hero    = _hero_url(article)
    ticker  = article.get('symbol', '')
    title   = article.get('title', '')
    dek     = (article.get('dek') or article.get('meta_description') or '')[:180].strip()
    days    = article.get('pattern_days', '')
    badge_color, badge_bg, badge_text = _direction_style(article)

    if hero:
        img_block = (
            '<a href="' + pub_url + '" style="display:block;">'
            '<img src="' + hero + '" alt="' + ticker + ' seasonal pattern" width="260" '
            'style="width:100%;height:140px;object-fit:cover;display:block;" /></a>'
        )
    else:
        img_block = (
            '<div style="height:80px;background:#0a0e1a;'
            'text-align:center;line-height:80px;">'
            '<span style="color:#ffffff;font-size:24px;font-weight:700;'
            'font-family:Arial,sans-serif;">' + ticker + '</span></div>'
        )

    days_html = (
        '<span style="font-size:10px;color:#9ca3af;margin-left:8px;'
        'font-family:Arial,sans-serif;">' + str(days) + 'd</span>'
        if days else ''
    )

    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #e5e7eb;">'
        '<tr><td style="padding:0;">' + img_block + '</td></tr>'
        '<tr><td style="padding:14px 14px 16px;">'
        '<table width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding-bottom:6px;">'
        '<span style="font-size:9px;font-weight:700;letter-spacing:1.2px;'
        'color:' + badge_color + ';text-transform:uppercase;'
        'font-family:Arial,sans-serif;">' + badge_text + '</span>'
        '<span style="font-size:11px;font-weight:700;color:#1a1a1a;'
        'margin-left:8px;font-family:Arial,sans-serif;">' + ticker + '</span>'
        + days_html +
        '</td></tr>'
        '<tr><td style="padding-bottom:8px;border-bottom:1px solid #f3f4f6;">'
        '<a href="' + pub_url + '" style="font-size:13px;font-weight:600;color:#0a0e1a;'
        'text-decoration:none;line-height:1.4;font-family:Arial,sans-serif;">' + title + '</a>'
        '</td></tr>'
        '<tr><td style="padding-top:8px;padding-bottom:12px;">'
        '<p style="margin:0;font-size:11px;color:#6b7280;line-height:1.6;'
        'font-family:Arial,sans-serif;">' + dek + '</p>'
        '</td></tr>'
        '<tr><td>'
        '<a href="' + pub_url + '" style="font-size:11px;font-weight:600;color:#0066cc;'
        'text-decoration:none;font-family:Arial,sans-serif;">Read full analysis &#x2192;</a>'
        '</td></tr>'
        '</table></td></tr></table>'
    )


def _article_grid_html(articles):
    rows = ''
    for article in articles:
        card = _article_card_html(article)
        rows += (
            '<tr><td width="100%" valign="top">' + card + '</td></tr>'
            '<tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>'
        )
    return '<table width="100%" cellpadding="0" cellspacing="0" border="0">' + rows + '</table>'


def _build_email_html(email_type, narrative, articles, send_date):
    date_str      = send_date.strftime('%B %d, %Y')
    type_label    = 'Daily Briefing' if email_type == 'daily' else 'Weekly Recap'
    section_title = "TODAY'S FEATURED PATTERNS" if email_type == 'daily' else "THIS WEEK'S FEATURED PATTERNS"
    grid          = _article_grid_html(articles)
    year          = send_date.year
    news_url      = config.news_website_url.rstrip('/')
    smn_icon_url  = config.smn_favicon
    narrative_html = narrative.replace('\n', '<br>')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Seasonal Market News &mdash; {type_label}</title>
<style>
@media only screen and (max-width:620px) {{
  .email-wrap {{ width:100%!important; }}
  .col-half   {{ display:block!important;width:100%!important;
                 padding-left:0!important;padding-right:0!important;
                 margin-bottom:12px!important; }}
  .pad-sides  {{ padding-left:18px!important;padding-right:18px!important; }}
  .hdr-pad    {{ padding:18px!important; }}
  .cta-row    {{ display:block!important;width:100%!important;padding-bottom:12px!important; }}
  .cta-btn    {{ display:block!important;margin-top:10px!important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:#ffffff;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
<tr><td align="center" style="padding:0;">

<table class="email-wrap" width="600" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;width:100%;">

  <!-- ── Header ── -->
  <tr>
    <td class="hdr-pad" style="background:#0a0e1a;padding:20px 28px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td valign="middle">
            <p style="margin:0 0 2px;font-size:9px;font-weight:700;letter-spacing:2px;
                      color:#f0b429;text-transform:uppercase;font-family:Arial,sans-serif;">
              Seasonal Market News &nbsp;&middot;&nbsp; {type_label}</p>
            <p style="margin:0;font-size:11px;color:#7a8fa6;font-family:Arial,sans-serif;">
              {date_str}</p>
          </td>
          <td align="right" valign="middle">
            <img src="{smn_icon_url}" alt="SMN" width="32" height="32"
                 style="display:block;" />
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Blue rule -->
  <tr>
    <td style="background:#0066cc;height:2px;font-size:0;line-height:0;">&nbsp;</td>
  </tr>

  <!-- ── AI Narrative ── -->
  <tr>
    <td class="pad-sides" style="padding:24px 28px 20px;border-bottom:1px solid #e5e7eb;">
      <p style="margin:0;font-size:14px;line-height:1.75;color:#1a1a1a;
                font-family:Georgia,'Times New Roman',serif;">{narrative_html}
        <br><span style="font-size:12px;color:#6b7280;">More patterns and analysis at
        <a href="{news_url}" style="color:#0066cc;text-decoration:none;font-weight:600;">SeasonalMarketNews.com</a></span></p>
    </td>
  </tr>

  <!-- ── Section label ── -->
  <tr>
    <td class="pad-sides" style="padding:18px 28px 12px;">
      <p style="margin:0;font-size:9px;font-weight:700;letter-spacing:2px;color:#6b7280;
                text-transform:uppercase;font-family:Arial,sans-serif;
                padding-bottom:10px;border-bottom:2px solid #0a0e1a;">{section_title}</p>
    </td>
  </tr>

  <!-- ── Article grid ── -->
  <tr>
    <td class="pad-sides" style="padding:0 20px 24px;">
      {grid}
    </td>
  </tr>

  <!-- ── CTA ── -->
  <tr>
    <td class="pad-sides" style="padding:0 28px 28px;border-top:1px solid #e5e7eb;
        padding-top:20px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td class="cta-row" valign="middle">
            <p style="margin:0;font-size:12px;color:#6b7280;font-family:Arial,sans-serif;">
              Full pattern library at
              <a href="{news_url}" style="color:#0066cc;text-decoration:none;font-weight:600;">
                SeasonalMarketNews.com</a></p>
          </td>
          <td class="cta-btn" align="right" valign="middle" style="white-space:nowrap;">
            <a href="{news_url}"
               style="display:inline-block;background:#0066cc;color:#ffffff;
                      font-size:11px;font-weight:700;padding:8px 16px;
                      text-decoration:none;font-family:Arial,sans-serif;letter-spacing:0.3px;">
              Browse All &#x2192;</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ── Footer ── -->
  <tr>
    <td class="pad-sides" style="padding:16px 28px;border-top:1px solid #e5e7eb;
        background:#f9fafb;">
      <p style="margin:0 0 4px;font-size:10px;color:#9ca3af;font-family:Arial,sans-serif;">
        &copy; {year} Tara Data Research LLC &nbsp;&middot;&nbsp;
        You&#39;re receiving this because you subscribed at SeasonalMarketNews.com</p>
      <p style="margin:0;font-size:10px;font-family:Arial,sans-serif;">
        <a href="{{$unsubscribe}}" style="color:#6b7280;text-decoration:underline;">Unsubscribe</a>
        &nbsp;&middot;&nbsp;
        <a href="{{$unsubscribe}}" style="color:#6b7280;text-decoration:underline;">Manage Preferences</a>
      </p>
    </td>
  </tr>

</table>

</td></tr>
</table>

</body>
</html>'''


# =============================================================================
# Campaign creation & scheduling
# =============================================================================

def _schedule_fields(minutes, now=None):
    """Express an absolute target instant in the named campaign timezone."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('Scheduling instant must include a timezone')
    local = (instant.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(MAILERLITE_ACCOUNT_TIMEZONE)
    return local.strftime('%Y-%m-%d'), local.strftime('%H'), local.strftime('%M')


def _mailerlite_headers():
    return {'Authorization': 'Bearer ' + config.mailerlite_token}


def _schedule_campaign_explicit(campaign_id, date_string, hour, minute):
    zones = requests.get(MAILERLITE_TIMEZONES_URL, headers=_mailerlite_headers(), timeout=15)
    zones.raise_for_status()
    matches = [zone for zone in zones.json().get('data', [])
               if zone.get('name') == MAILERLITE_ACCOUNT_TIMEZONE.key]
    if len(matches) != 1 or not str(matches[0].get('id', '')).isdigit():
        raise ValueError('MailerLite timezone ID unavailable; campaign remains unscheduled')
    response = requests.post(
        MAILERLITE_CAMPAIGN_URL + str(campaign_id) + '/schedule',
        headers=_mailerlite_headers(),
        json={'delivery': 'scheduled', 'schedule': {
            'date': date_string, 'hours': hour, 'minutes': minute,
            'timezone_id': int(matches[0]['id'])}}, timeout=15)
    response.raise_for_status()
    return response.json()


def _campaign_key(kind, day):
    return f'{kind}:{day.isoformat()}'


def _campaign_name(kind, day):
    if kind not in ('daily', 'weekly'):
        raise ValueError('Campaign kind must be daily or weekly')
    return f'SMN-{kind.title()}-{day.isoformat()}'


def _create_and_schedule(group_id, subject, html, campaign_name, state, kind, day, articles):
    """Journal intent before any POST; an uncertain result must be reconciled."""
    key = _campaign_key(kind, day)
    if key in state.setdefault('campaigns', {}):
        raise ValueError(f'Campaign {key} already attempted; reconcile provider status before retry')
    record = {'name': campaign_name, 'slugs': [a['slug'] for a in articles],
              'phase': 'create_unknown', 'campaign_id': None, 'provider_status': None}
    state['campaigns'][key] = record
    _save_state(state)
    campaign_id, _ = create_campaign(campaign_name, subject, FROM_NAME, FROM_EMAIL, group_id, html)
    record['campaign_id'] = str(campaign_id)
    record['phase'] = 'schedule_unknown'
    _save_state(state)
    d, h, m = _schedule_fields(5)
    response = _schedule_campaign_explicit(campaign_id, d, h, m)
    data = response.get('data') if isinstance(response, dict) else None
    if (not isinstance(data, dict) or str(data.get('id')) != str(campaign_id)
            or data.get('status') not in ('ready', 'sent')):
        raise ValueError(f'MailerLite schedule acknowledgement missing for {key}; reconcile before retry')
    record['phase'] = 'scheduled'
    record['provider_status'] = data.get('status')
    record['scheduled_for_account_time'] = f'{d} {h}:{m} {MAILERLITE_ACCOUNT_TIMEZONE.key}'
    record['provider_finished_at'] = data.get('finished_at')
    _save_state(state)
    log.info('Campaign scheduled: id=%s name="%s" send_at=%s %s:%s account_zone=%s',
             campaign_id, campaign_name, d, h, m, MAILERLITE_ACCOUNT_TIMEZONE.key)
    return record


def _get_provider_campaign(campaign_id):
    response = requests.get(
        MAILERLITE_CAMPAIGN_URL + str(campaign_id),
        headers=_mailerlite_headers(), timeout=15)
    response.raise_for_status()
    return response.json().get('data', {})


def _apply_provider_status(record, data):
    if str(data.get('id')) != str(record['campaign_id']) or data.get('name') != record['name']:
        raise ValueError('MailerLite campaign identity mismatch')
    record['provider_status'] = data.get('status')
    record['provider_finished_at'] = data.get('finished_at')
    stats = data.get('stats') or {}
    record['provider_counts'] = {
        'sent': stats.get('sent'),
        'hard_bounces': stats.get('hard_bounces_count'),
        'soft_bounces': stats.get('soft_bounces_count'),
        'delivered': stats.get('delivered_count'),
    }
    record['phase'] = 'provider_sent' if data.get('status') == 'sent' and data.get('finished_at') else 'provider_' + str(data.get('status') or 'unknown')


@_serialized_newsletter
def reconcile_campaign(kind, day):
    """Read the provider status of a journaled campaign; never creates or sends."""
    state = _load_state()
    key = _campaign_key(kind, day)
    record = state.get('campaigns', {}).get(key)
    if not record or not record.get('campaign_id'):
        raise ValueError(f'No known campaign id for {key}; inspect MailerLite manually')
    _apply_provider_status(record, _get_provider_campaign(record['campaign_id']))
    _save_state(state)
    return record


@_serialized_newsletter
def poll_pending_campaigns(current=None, max_polls=48):
    """Bounded GET-only status reconciliation; never reserves or sends a campaign."""
    current = current or datetime.now(timezone.utc)
    state = _load_state()
    results = {}
    for key, record in sorted(state.get('campaigns', {}).items()):
        if (not record.get('campaign_id') or record.get('provider_status') in {'failed', 'canceled', 'cancelled'} or
                (record.get('provider_status') == 'sent' and record.get('provider_finished_at'))):
            continue
        if record.get('poll_count', 0) >= max_polls:
            results[key] = {'status': 'needs_attention', 'reason': 'Provider status polling limit reached'}
            continue
        retry = record.get('next_poll_utc')
        if retry and current < datetime.fromisoformat(retry):
            continue
        record['poll_count'] = record.get('poll_count', 0) + 1
        record['next_poll_utc'] = (current + timedelta(minutes=30)).isoformat()
        _save_state(state)
        try:
            _apply_provider_status(record, _get_provider_campaign(record['campaign_id']))
            record.pop('poll_error', None)
        except Exception as exc:
            record['poll_error'] = str(exc)[:300]
        record['last_poll_utc'] = current.isoformat()
        results[key] = {'provider_status': record.get('provider_status'),
                        'provider_counts': record.get('provider_counts'),
                        'poll_error': record.get('poll_error')}
        _save_state(state)
    return results


def attach_existing_campaign(kind, day, campaign_id, slugs):
    """Reconcile a legacy campaign from a supplied ID and legacy catalog evidence.

    MailerLite's campaign GET does not return the sent HTML. The slug binding here
    is explicitly inherited from the old local *_sent marker, not provider proof.
    """
    if not isinstance(day, date) or not str(campaign_id).isdigit():
        raise ValueError('A date and numeric campaign ID are required')
    name = _campaign_name(kind, day)
    if not slugs or len(slugs) != len(set(slugs)):
        raise ValueError('A nonempty unique slug lineup is required')
    with _newsletter_lock():
        state = _load_state()
        key = _campaign_key(kind, day)
        if key in state.get('campaigns', {}):
            raise ValueError(f'Campaign {key} is already journaled')
        legacy = set(state.get(f'{kind}_sent', []))
        if not set(slugs) <= legacy:
            raise ValueError('Lineup is not present in the legacy sent marker')
        matches = {slug: [post for post in _load_posts() if post.get('slug') == slug]
                   for slug in slugs}
        if any(len(posts) != 1 or not posts[0].get('url')
               for posts in matches.values()):
            raise ValueError('Lineup is missing or ambiguous in the article catalog')
        try:
            published_days = [date.fromisoformat(matches[slug][0]['published_date'][:10])
                              for slug in slugs]
        except (KeyError, ValueError) as exc:
            raise ValueError('Lineup publication dates are unavailable') from exc
        if any(published != day if kind == 'daily' else
               not day - timedelta(days=7) <= published <= day
               for published in published_days):
            raise ValueError('Lineup publication dates do not match campaign date')
        data = _get_provider_campaign(campaign_id)
        if str(data.get('id')) != str(campaign_id) or data.get('name') != name:
            raise ValueError('MailerLite campaign identity mismatch')
        record = {'name': name, 'slugs': list(slugs), 'campaign_id': str(campaign_id),
                  'lineup_evidence': 'legacy_sent_marker_and_current_catalog',
                  'provider_status': None}
        _apply_provider_status(record, data)
        state.setdefault('campaigns', {})[key] = record
        _save_state(state)
        return record


# =============================================================================
# Daily send  (Mon–Fri)
# =============================================================================

@_serialized_newsletter
def daily_send(force=False, verified_urls=None, edition_date=None):
    today = edition_date or _today()

    state = _load_state()
    posts = _load_posts()

    if verified_urls is not None:
        selected = [p for p in posts if p.get('url') in verified_urls]
        if len(selected) != len(verified_urls) or {p.get('url') for p in selected} != verified_urls:
            raise ValueError('Verified reader lineup is missing from the email catalog')
        sent = _reserved_slugs(state, 'daily')
        remaining = [p for p in selected if p.get('slug') not in sent]
        if not remaining:
            print('Verified reader edition already reserved by a daily campaign. No new campaign created.')
            return
        if len(remaining) != len(selected):
            raise ValueError('Verified reader edition was partly reserved; inspect before retry')
        articles = sorted(remaining, key=lambda x: x.get('published_date', ''), reverse=True)
    elif force:
        # Dev/test: grab most recent unsent articles regardless of date
        sent     = _reserved_slugs(state, 'daily')
        articles = [p for p in posts if p.get('slug') not in sent]
        articles.sort(key=lambda x: x.get('published_date', ''), reverse=True)
        articles = articles[:MAX_DAILY_ARTICLES]
    else:
        articles = _filter_daily(posts, state)

    if not articles:
        log.info('daily_send: no new articles for %s', today)
        print(f'No new articles for {today}. Nothing sent.')
        return

    log.info('daily_send: %d article(s) for %s', len(articles), today)

    groups   = get_email_groups()
    group_id = groups.get('SMN-DAILY')
    if not group_id:
        log.error('daily_send: SMN-DAILY group not found in MailerLite')
        print('Error: SMN-DAILY group not found.')
        return

    print(f'Generating daily narrative for {len(articles)} article(s)...')
    subject, narrative = _generate_daily_narrative(articles, prev_narrative=state.get('last_daily_narrative'))

    if not subject:
        subject = f"Today's Seasonal Market Briefing — {today.strftime('%B %d, %Y')}"

    html = _build_email_html('daily', narrative, articles, today)
    name = f"SMN-Daily-{today.isoformat()}"

    record = _create_and_schedule(group_id, subject, html, name, state, 'daily', today, articles)
    state['last_daily_narrative'] = narrative
    _save_state(state)

    print(f'Daily campaign queued: {len(articles)} article(s) | "{subject}" | id={record["campaign_id"]}')
    log.info('daily_send: provider schedule acknowledged; delivery pending.')


# =============================================================================
# Weekly send  (Sunday)
# =============================================================================

@_serialized_newsletter
def weekly_send(force=False):
    today = _today()
    state = _load_state()

    if not force and (state.get('last_weekly_date') == today.isoformat() or
                      _campaign_key('weekly', today) in state.get('campaigns', {})):
        log.info('weekly_send: already sent this week (%s)', today)
        print('Weekly recap already reserved this week. No new campaign created.')
        return

    posts = _load_posts()

    if force:
        sent     = _reserved_slugs(state, 'weekly')
        articles = [p for p in posts if p.get('slug') not in sent]
        articles.sort(key=lambda x: x.get('published_date', ''), reverse=True)
        articles = articles[:MAX_WEEKLY_ARTICLES]
    else:
        articles = _filter_weekly(posts, state)

    if not articles:
        log.info('weekly_send: no new articles this week')
        print('No new articles this week. Nothing sent.')
        return

    log.info('weekly_send: %d article(s) for week ending %s', len(articles), today)

    groups   = get_email_groups()
    group_id = groups.get('SMN-WEEKLY')
    if not group_id:
        log.error('weekly_send: SMN-WEEKLY group not found in MailerLite')
        print('Error: SMN-WEEKLY group not found.')
        return

    print(f'Generating weekly narrative for {len(articles)} article(s)...')
    subject, narrative = _generate_weekly_narrative(articles, prev_narrative=state.get('last_weekly_narrative'))

    if not subject:
        week_start = (today - timedelta(days=6)).strftime('%b %d')
        subject = f"Your Weekly Seasonal Digest — {week_start}–{today.strftime('%b %d, %Y')}"

    html = _build_email_html('weekly', narrative, articles, today)
    name = f"SMN-Weekly-{today.isoformat()}"

    record = _create_and_schedule(group_id, subject, html, name, state, 'weekly', today, articles)
    state['last_weekly_narrative'] = narrative
    _save_state(state)

    print(f'Weekly campaign queued: {len(articles)} article(s) | "{subject}" | id={record["campaign_id"]}')
    log.info('weekly_send: provider schedule acknowledged; delivery pending.')


# =============================================================================
# Test send  (--test email@address.com)
# =============================================================================

TEST_GROUP_NAME = 'SMN-TEST'


def _get_or_create_test_group():
    """Return the SMN-TEST group id, creating it in MailerLite if it doesn't exist."""
    groups = get_email_groups()
    if TEST_GROUP_NAME in groups:
        return groups[TEST_GROUP_NAME]
    print(f'Creating MailerLite group "{TEST_GROUP_NAME}"...')
    response = create_mailerlite_group(TEST_GROUP_NAME)
    return response['data']['id']


def _ensure_subscriber_in_test_group(email, group_id):
    """Make sure the email exists as a subscriber and is in the test group."""
    try:
        resp = get_subscriber_by_email(email)
        subscriber_id = resp['data']['id']
    except Exception:
        print(f'Creating subscriber {email}...')
        resp = create_subscriber(email, '', '', '', '')
        subscriber_id = resp['data']['id']
    assign_subscriber_to_a_group(subscriber_id, group_id)


def test_send(email):
    today = _today()
    posts = _load_posts()

    # Use most recent articles regardless of date (same as --force)
    articles = sorted(posts, key=lambda x: x.get('published_date', ''), reverse=True)
    articles = articles[:MAX_DAILY_ARTICLES]

    if not articles:
        print('No articles found. Nothing sent.')
        return

    group_id = _get_or_create_test_group()
    _ensure_subscriber_in_test_group(email, group_id)

    print(f'Generating test narrative for {len(articles)} article(s)...')
    subject, narrative = _generate_daily_narrative(articles)

    if not subject:
        subject = f"[TEST] Today's Seasonal Market Briefing — {today.strftime('%B %d, %Y')}"
    else:
        subject = f'[TEST] {subject}'

    html  = _build_email_html('daily', narrative, articles, today)
    name  = f'TEST-SMN-Daily-{today.isoformat()}'

    campaign_id, _ = create_campaign(name, subject, FROM_NAME, FROM_EMAIL, group_id, html)
    d, h, m = _schedule_fields(1)
    _schedule_campaign_explicit(campaign_id, d, h, m)

    print(f'Test campaign created: "{name}" | subject: "{subject}"')
    print(f'Sending to: {email}  |  Scheduled in ~1 min')
    print('Remember to delete this campaign from MailerLite after reviewing.')
    log.info('test_send: campaign="%s" to=%s', name, email)


# =============================================================================
# Entry point
# =============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send SMN subscriber emails via MailerLite')
    parser.add_argument('--force', action='store_true',
                        help='Re-send even if already sent today (for testing)')
    parser.add_argument('--test', metavar='EMAIL',
                        help='Send a single test email to the given address (no state changes)')
    parser.add_argument('--verified-reader-date', metavar='YYYY-MM-DD',
                        help='Require the complete verified production reader edition')
    args = parser.parse_args()

    today = _today()
    print(f'SEND SMN EMAILS  —  Started {datetime.now():%Y-%m-%d %H:%M:%S}  —  {today.strftime("%A %B %d, %Y")}')

    if args.verified_reader_date:
        import operational_settings
        setting = operational_settings.for_instant(datetime.now(timezone.utc))['weekday_newsletter']
        local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(setting['timezone']))
        local_today = local_now.date()
        if (args.test or args.force or args.verified_reader_date != local_today.isoformat() or
                local_today.weekday() >= 5 or local_now.strftime('%H:%M') < setting['time']):
            parser.error('verified reader send requires the current weekday without --test or --force')
        from operational_schedule import CONTROLLER_ROOT, verified_reader_urls
        urls = verified_reader_urls(CONTROLLER_ROOT, args.verified_reader_date)
        if not urls:
            raise ValueError('Complete verified reader edition is not ready')
        daily_send(verified_urls=urls, edition_date=local_today)
    elif args.test:
        test_send(args.test)
    elif args.force:                # --force always sends daily regardless of day
        daily_send(force=True)
    elif today.weekday() == 6:      # Sunday
        weekly_send(force=False)
    elif today.weekday() <= 4:      # Mon–Fri
        daily_send(force=False)
    else:                           # Saturday
        log.info('No email scheduled for Saturday.')
        print('No email scheduled for Saturday.')
