"""Explicit market-session policies and shared calendar-window semantics.

Pattern durations are calendar days. US-listed equities, ETFs and indices use
US-equity closure rules; crypto trades seven days. Unknown asset families fail
closed unless the caller supplies a :class:`MarketCalendar`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    d = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter(year):
    # Anonymous Gregorian algorithm.
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4; f=(b+8)//25
    g=(b-f+1)//3; h=(19*a+b-d-g+15)%30; i=c//4; k=c%4
    l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31; day=(h+l-7*m+114)%31+1
    return date(year, month, day)


def us_equity_holidays(year):
    """Standard full-day US-equity closures for ``year``."""
    holidays = {
        _observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3), _easter(year) - timedelta(days=2),
        _last_weekday(year, 5, 0), _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    holidays.add(_observed(date(year + 1, 1, 1)))
    return holidays


class UnsupportedMarketCalendar(ValueError):
    """Session semantics are unavailable and must not be guessed."""


@dataclass(frozen=True)
class MarketCalendar:
    """Immutable session policy that callers can construct for supported venues."""
    calendar_id: str
    weekdays: frozenset[int]
    holiday_provider: Callable[[int], set[date] | frozenset[date]] | None = None

    def is_session(self, day: date) -> bool:
        closed = self.holiday_provider(day.year) if self.holiday_provider else ()
        return day.weekday() in self.weekdays and day not in closed


US_EQUITIES = MarketCalendar("us_equities", frozenset(range(5)), us_equity_holidays)
CRYPTO_24_7 = MarketCalendar("crypto_24_7", frozenset(range(7)))

_BUILTIN_ASSET_CALENDARS = {
    "us_equity": US_EQUITIES, "us_equities": US_EQUITIES,
    "us_etf": US_EQUITIES, "us_etfs": US_EQUITIES,
    "us_index": US_EQUITIES, "us_indices": US_EQUITIES,
    "crypto": CRYPTO_24_7, "cryptocurrency": CRYPTO_24_7,
}


def calendar_for_asset_family(asset_family):
    """Resolve a verified built-in policy; unsupported families fail closed."""
    key = str(asset_family or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _BUILTIN_ASSET_CALENDARS[key]
    except KeyError as exc:
        raise UnsupportedMarketCalendar(
            f"asset family {asset_family!r} has no supported market calendar; "
            "pass an explicit MarketCalendar"
        ) from exc


def _resolve_calendar(calendar=None, asset_family=None, holidays=None):
    selected = calendar_for_asset_family(asset_family) if asset_family is not None else calendar
    if selected is not None and holidays is not None:
        raise ValueError("pass calendar/asset_family or holidays, not both")
    if holidays is not None:
        frozen = frozenset(holidays)
        selected = MarketCalendar("custom_weekday_holidays", frozenset(range(5)), lambda _year: frozen)
    if not isinstance(selected, MarketCalendar):
        raise UnsupportedMarketCalendar("an explicit MarketCalendar or supported asset_family is required")
    return selected


def resolve_market_calendar(*, calendar=None, asset_family=None, holidays=None):
    """Resolve caller inputs to one explicit policy (public wiring API)."""
    return _resolve_calendar(calendar, asset_family, holidays)


def is_trading_day(day, holidays=None, *, calendar=None, asset_family=None):
    return _resolve_calendar(calendar, asset_family, holidays).is_session(day)


def advance_to_next_trading_day(day, holidays=None, *, calendar=None, asset_family=None):
    selected = _resolve_calendar(calendar, asset_family, holidays)
    while not selected.is_session(day):
        day += timedelta(days=1)
    return day


@dataclass(frozen=True)
class CalendarWindow:
    """Single translation from configured duration to all endpoint forms."""
    configured_calendar_days: int
    nominal_endpoint: date
    adjusted_session_endpoint: date
    upstream_days_parameter: int
    calendar_id: str


def calendar_window(start, calendar_days, holidays=None, *, calendar=None, asset_family=None):
    """Resolve an inclusive calendar-day duration under an explicit session policy."""
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    days = int(calendar_days)
    if days <= 0:
        raise ValueError("calendar_days must be positive")
    selected = _resolve_calendar(calendar, asset_family, holidays)
    offset = days - 1
    nominal = start + timedelta(days=offset)
    return CalendarWindow(days, nominal,
                          advance_to_next_trading_day(nominal, calendar=selected),
                          offset, selected.calendar_id)


def calendar_window_end(start, calendar_days, holidays=None, *, calendar=None, asset_family=None):
    return calendar_window(start, calendar_days, holidays, calendar=calendar,
                           asset_family=asset_family).adjusted_session_endpoint


def format_calendar_window(start, calendar_days, holidays=None, *, calendar=None, asset_family=None):
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    window = calendar_window(start, calendar_days, holidays, calendar=calendar,
                             asset_family=asset_family)
    adjustment = "; endpoint adjusted" if window.adjusted_session_endpoint != window.nominal_endpoint else ""
    return (f"{start.isoformat()} ➝ {window.adjusted_session_endpoint.isoformat()} "
            f"({window.configured_calendar_days} Calendar Days{adjustment})")


def sessions_in_calendar_horizon(start, calendar_days, holidays=None, *, calendar=None, asset_family=None):
    """Sessions after start through the policy-adjusted endpoint."""
    selected = _resolve_calendar(calendar, asset_family, holidays)
    end = calendar_window(start, calendar_days, calendar=selected).adjusted_session_endpoint
    out = []
    day = start + timedelta(days=1)
    while day <= end:
        if selected.is_session(day):
            out.append(day)
        day += timedelta(days=1)
    return out
