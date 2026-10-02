"""Private dashboard settings for future operational runs.

The scheduler reads this file at each tick. Saving it cannot alter a running job.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATE_DIR = Path(os.environ.get('SMN_DASHBOARD_STATE', '/var/lib/smn-dashboard'))
FILE = STATE_DIR / 'operational-settings.json'
DEFAULTS = {
    'alerts_enabled': False,
    'alert_recipients': [],
    'daily_generation': {'start_time': '05:30', 'target_time': '07:00',
                         'timezone': 'America/New_York',
                         'no_start_grace_minutes': None, 'stall_minutes': None},
    'sunday_summary': {'enabled': True, 'time': '09:00', 'timezone': 'UTC'},
    'weekday_newsletter': {'time': '07:00', 'timezone': 'America/New_York'},
}
EMAIL = re.compile(r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$')
TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def load():
    if not FILE.exists():
        return json.loads(json.dumps(DEFAULTS))
    value = json.loads(FILE.read_text(encoding='utf-8'))
    value.setdefault('weekday_newsletter', dict(DEFAULTS['weekday_newsletter']))
    return validate(value)


def validate(value):
    if not isinstance(value, dict) or set(value) - (set(DEFAULTS) | {'effective_from', 'updated_at', 'updated_by', 'previous_schedule'}):
        raise ValueError('unknown settings field')
    if set(DEFAULTS) - set(value):
        raise ValueError('all settings fields are required')
    if type(value['alerts_enabled']) is not bool:
        raise ValueError('alerts_enabled must be true or false')
    recipients = value['alert_recipients']
    if not isinstance(recipients, list) or len(recipients) > 10 or (value['alerts_enabled'] and not recipients) or any(
            not isinstance(x, str) or len(x) > 254 or '..' in x or not EMAIL.fullmatch(x) for x in recipients):
        raise ValueError('alert_recipients must contain valid email addresses and cannot be empty when alerts are enabled')
    if len({x.lower() for x in recipients}) != len(recipients):
        raise ValueError('alert_recipients contains a duplicate')
    for name, keys in [('daily_generation', {'start_time', 'target_time', 'timezone',
                                            'no_start_grace_minutes', 'stall_minutes'}),
                       ('sunday_summary', {'enabled', 'time', 'timezone'}),
                       ('weekday_newsletter', {'time', 'timezone'})]:
        item = value[name]
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError(name + ' fields are invalid')
        for field in ({'start_time', 'target_time'} if name == 'daily_generation' else {'time'}):
            if not isinstance(item[field], str) or not TIME.fullmatch(item[field]):
                raise ValueError(name + '.' + field + ' must be HH:MM')
        if not isinstance(item['timezone'], str):
            raise ValueError(name + '.timezone is required')
        try:
            ZoneInfo(item['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(name + '.timezone must be an IANA timezone') from None
    if type(value['sunday_summary']['enabled']) is not bool:
        raise ValueError('sunday_summary.enabled must be true or false')
    daily = value['daily_generation']
    start = int(daily['start_time'][:2])*60 + int(daily['start_time'][3:])
    target = int(daily['target_time'][:2])*60 + int(daily['target_time'][3:])
    if start < 180:
        raise ValueError('daily_generation.start_time must be 03:00 or later to keep selection on the same local day')
    if target <= start:
        raise ValueError('daily_generation.target_time must follow start_time on the same day')
    zone = ZoneInfo(daily['timezone'])
    start_clock = datetime.strptime(daily['start_time'], '%H:%M').time()
    first = datetime.now(timezone.utc).date()
    for offset in range(370):
        day = first + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        local_start = datetime.combine(day, start_clock, zone)
        utc_start = local_start.astimezone(timezone.utc)
        if (utc_start.date() != day or
                (utc_start - timedelta(hours=1)).date() != day or
                utc_start.astimezone(zone).strftime('%H:%M') != daily['start_time']):
            raise ValueError('daily_generation.start_time must keep selection and generation on the same local and UTC date')
    newsletter = value['weekday_newsletter']
    newsletter_zone = ZoneInfo(newsletter['timezone'])
    newsletter_clock = datetime.strptime(newsletter['time'], '%H:%M').time()
    ny = ZoneInfo('America/New_York')
    for offset in range(370):
        day = first + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        mail = datetime.combine(day, newsletter_clock, newsletter_zone).astimezone(timezone.utc)
        seven_ny = datetime.combine(day, datetime.strptime('07:00', '%H:%M').time(), ny)
        if mail < seven_ny.astimezone(timezone.utc) or mail.astimezone(zone).date() != day:
            raise ValueError('weekday_newsletter.time must be at or after 07:00 America/New_York on the edition date')
    for field in ('no_start_grace_minutes', 'stall_minutes'):
        minutes = daily[field]
        if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 1440):
            raise ValueError('daily_generation.' + field + ' must be blank or 1–1440 minutes')
    return value


def save(value, who, now=None):
    cleaned = validate(value)
    now = now or datetime.now(timezone.utc)
    current = load()
    schedule_changed = any(cleaned[key] != current[key] for key in ('daily_generation', 'sunday_summary',
                                                                     'weekday_newsletter'))
    # Future dates only: a time edit cannot schedule a second run today.
    result = {key: cleaned[key] for key in DEFAULTS}
    if schedule_changed:
        previous = {}
        effective = {}
        for key in ('daily_generation', 'sunday_summary', 'weekday_newsletter'):
            local_date = now.astimezone(ZoneInfo(cleaned[key]['timezone'])).date().isoformat()
            old_from = current.get('effective_from', {}).get(key)
            if old_from and local_date < old_from:
                previous[key] = current.get('previous_schedule', {}).get(key, current[key])
            else:
                previous[key] = current[key]
            effective[key] = (now.astimezone(ZoneInfo(cleaned[key]['timezone'])).date() +
                              timedelta(days=1)).isoformat()
        result['previous_schedule'] = previous
        result['effective_from'] = effective
    elif current.get('effective_from'):
        result['effective_from'] = current['effective_from']
        if current.get('previous_schedule'):
            result['previous_schedule'] = current['previous_schedule']
    result['updated_at'] = now.isoformat()
    result['updated_by'] = who
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.operational-settings-', dir=FILE.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(result, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o600)
        os.replace(name, FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return result


def for_date(date):
    """Return the daily schedule effective on an edition's local date."""
    value = load()
    if date < value.get('effective_from', {}).get('daily_generation', ''):
        value['daily_generation'] = value['previous_schedule']['daily_generation']
    return value


def for_instant(now):
    """Return each schedule effective at an instant, honoring its own timezone."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware datetime required')
    value = load()
    for key in ('daily_generation', 'sunday_summary', 'weekday_newsletter'):
        effective = value.get('effective_from', {}).get(key)
        if not effective:
            continue
        local_date = now.astimezone(ZoneInfo(value[key]['timezone'])).date().isoformat()
        if local_date < effective:
            value[key] = value['previous_schedule'][key]
    return value
