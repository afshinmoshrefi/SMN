"""Deterministic, fail-closed pre-publication integrity checks."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html import unescape
from typing import Any, Dict

from market_calendar import calendar_window

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_TRADING_DAY_RE = re.compile(r"\b(?:\d+[\s-]*)?trading[\s-]+days?\b", re.I)
_FORECAST_RE = re.compile(r"\b(?:price\s+forecast|forecasted?\s+(?:price|path|move)|predict(?:s|ed|ing)?\s+(?:the\s+)?(?:price|path|move))\b", re.I)
_PROJECTION_RE = re.compile(r"\b(?:projection|projected|expected path|future path)\b", re.I)
_ALLOWED_PROJECTION_RE = re.compile(r"\b(?:historical\s+average|average\s+historical)\s+(?:trend|path)\b", re.I)
_CAUSAL_RE = re.compile(r"\b(?:caused|drove|triggered|resulted in|because of|due to)\b", re.I)
_HYPOTHESIS_RE = re.compile(r"\b(?:may|might|could|possible|potential|hypothesis|monitor|watch)\b", re.I)
_PERCENT_RE = re.compile(r"(?<![\d.])(100(?:\.0+)?%)(?![\d.])")
_SAMPLE_RE = re.compile(r"\b(\d+)\s+(?:of|out of)\s+\1\b", re.I)
_ANY_SAMPLE_RE = re.compile(r"\b(\d+)\s+(?:of|out of)\s+(\d+)\b", re.I)
_PROFITABLE_RE = re.compile(r"\b(?:profitable|profitability|winning|winners?)\b", re.I)
_ANY_PERCENT_RE = re.compile(r"(?<![\d.])(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)\s*%(?![\d.])")
_CURRENT_PRICE_RE = re.compile(r"\b(?:currently\s+trades?|trades?\s+at|last\s+clos(?:e|ed)|current\s+price|price\s+as\s+of|live\s+price)\b", re.I)
_CUMULATIVE_RE = re.compile(r"\bcumulative return\b", re.I)
_CUMULATIVE_DEFINITION_RE = re.compile(r"\b(?:arithmetic\s+sum|sum\s+of|compounded|sequential\s+capital|not\s+compounded)\b", re.I)
_REQUIRED_STATS = ("Trade Dir", "Num Winners", "Num Losers", "Percent Profitable",
                   "Avg Profit", "Median Profit", "Std Dev", "Sharpe Ratio")
_EXACT_STATS = ("Num Winners", "Num Losers", "Percent Profitable", "Annualized Return",
                "Cumulative Return", "Median Profit", "Avg Profit", "Avg Profit - All",
                "Avg Loss", "Std Dev", "Sharpe Ratio", "TradeWave Ratio")

# Canonical customer-visible contract. Each fact belongs only on its designated
# surface; available metrics are not implicitly mandatory everywhere.
REQUIRED_DISPLAY_SCHEMA = {
    "headline": ("company", "symbol"),
    "dek": ("pattern_start", "adjusted_end", "calendar_duration", "direction"),
    "key_stats": ("lookback", "sample_size", "winners", "losers", "win_rate",
                  "average_return", "median_return", "dispersion", "sharpe"),
    "methodology": ("calendar_duration", "lookback"),
}
# Optional means omission is allowed. If displayed, the ordinary exact-value
# scan below still rejects mutations on any customer-visible surface.
OPTIONAL_DISPLAY_FIELDS = ("Annualized Return", "Cumulative Return", "Avg Profit - All",
                           "Avg Loss", "TradeWave Ratio")


def text_content(html: str) -> str:
    return _SPACE_RE.sub(" ", unescape(_TAG_RE.sub(" ", html or ""))).strip()


def _customer_visible_text(html: str) -> str:
    """Text nodes plus customer-visible title/description social metadata."""
    parts = [text_content(html)]
    for tag in re.findall(r"<meta\b[^>]*>", html or "", re.I):
        attrs = {name.casefold(): unescape(value) for name, _, value in re.findall(
            r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag, re.I | re.S
        )}
        key = (attrs.get("name") or attrs.get("property") or "").casefold()
        if key in {"description", "og:title", "og:description", "twitter:title", "twitter:description"}:
            parts.append(attrs.get("content", ""))
    return _SPACE_RE.sub(" ", " ".join(parts)).strip()


def _stats(cdata: Any) -> Dict[str, Any]:
    if not isinstance(cdata, dict):
        return {}
    for candidate in (cdata.get("stats"),
                      (cdata.get("Data") or {}).get("stats") if isinstance(cdata.get("Data"), dict) else None,
                      (cdata.get("data") or {}).get("stats") if isinstance(cdata.get("data"), dict) else None):
        if isinstance(candidate, dict):
            return candidate
    return {}


@dataclass(frozen=True)
class EvidenceBundle:
    """Immutable exact-data authority captured from structured TradeWave cdata."""
    resource_id: str
    symbol: str
    company: str
    start_date: str
    end_date: str
    calendar_days: int
    calendar_id: str
    asset_family: str
    years: str
    direction: str
    stats: tuple[tuple[str, str], ...]

    def stat(self, name: str) -> str | None:
        return dict(self.stats).get(name)


def build_evidence_bundle(cdata: Any, *, resource_id: str, symbol: str, company: str,
                          start_date: str, days: int, years: str,
                          direction: str, asset_family: str | None = None,
                          market_calendar=None) -> EvidenceBundle:
    stats = _stats(cdata)
    missing = [key for key in _REQUIRED_STATS if stats.get(key) in (None, "")]
    if missing:
        raise ValueError("structured cdata is missing required evidence: " + ", ".join(missing))
    authoritative_direction = str(stats["Trade Dir"]).strip().lower()
    if authoritative_direction != str(direction).strip().lower():
        raise ValueError("requested direction disagrees with structured cdata")
    start = date.fromisoformat(str(start_date))
    window = (calendar_window(start, int(days), calendar=market_calendar)
              if market_calendar is not None else
              calendar_window(start, int(days), asset_family=asset_family))
    frozen_stats = tuple((key, str(stats[key]).strip()) for key in _EXACT_STATS
                         if stats.get(key) not in (None, ""))
    return EvidenceBundle(str(resource_id), str(symbol).strip().upper(), str(company).strip(),
                          start.isoformat(), window.adjusted_session_endpoint.isoformat(),
                          int(days), window.calendar_id, str(asset_family or window.calendar_id),
                          str(years), authoritative_direction, frozen_stats)


def _number(value: Any) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def _displayed_values(text: str, label: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(
        rf"\b{re.escape(label)}\b\s*(?:is|was|:|=)\s*([^;|\n<]+)", text, re.I
    )]


def _displayed_value(text: str, label: str) -> str | None:
    values = _displayed_values(text, label)
    return values[0] if values else None


def _surface(html: str, name: str) -> str:
    if name == "headline":
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.I | re.S)
    else:
        css_names = ("methodology(?:-note)?" if name == "methodology" else re.escape(name.replace("_", "-")))
        match = re.search(
            rf"<(p|div|section|aside)\b[^>]*class=['\"][^'\"]*\b(?:{css_names})\b[^'\"]*['\"][^>]*>(.*?)</\1>",
            html, re.I | re.S,
        )
    return text_content(match.group(2 if name != "headline" else 1)) if match else ""


def _require(surface: str, field: str, pattern: str, errors: list[str]) -> None:
    if not re.search(pattern, surface, re.I):
        errors.append(f"{field} required display is missing from its designated surface")


def _validate_required_display(html: str, evidence: EvidenceBundle, errors: list[str]) -> None:
    headline, dek = _surface(html, "headline"), _surface(html, "dek")
    key_stats, methodology = _surface(html, "key_stats"), _surface(html, "methodology")
    winners = int(_number(evidence.stat("Num Winners")) or 0)
    losers = int(_number(evidence.stat("Num Losers")) or 0)
    fields = (
        (headline, "company", re.escape(evidence.company)),
        (headline, "symbol", rf"\({re.escape(evidence.symbol)}\)"),
        (dek, "pattern_start", rf"\bPattern Start Date\s*:\s*{re.escape(evidence.start_date)}\b"),
        (dek, "adjusted_end", rf"\bPattern End Date\s*:\s*{re.escape(evidence.end_date)}\b"),
        (dek, "calendar_duration", rf"\bDuration\s*:\s*{evidence.calendar_days}\s+calendar[ -]days?\b"),
        (dek, "direction", rf"\bTrade Direction\s*:\s*{re.escape(evidence.direction)}\b"),
        (key_stats, "lookback", rf"\bLookback\s*:\s*{re.escape(evidence.years)}\s+years?\b"),
        (key_stats, "sample_size", rf"\bSample Size\s*:\s*{winners + losers}\b"),
        (key_stats, "winners", rf"\bNum Winners\s*:\s*{winners}\b"),
        (key_stats, "losers", rf"\bNum Losers\s*:\s*{losers}\b"),
        (key_stats, "win_rate", rf"\bPercent Profitable\s*:\s*{re.escape(str(evidence.stat('Percent Profitable')))}"),
        (key_stats, "average_return", rf"\bAvg Profit\s*:\s*{re.escape(str(evidence.stat('Avg Profit')))}"),
        (key_stats, "median_return", rf"\bMedian Profit\s*:\s*{re.escape(str(evidence.stat('Median Profit')))}"),
        (key_stats, "dispersion", rf"\bStd Dev\s*:\s*{re.escape(str(evidence.stat('Std Dev')))}"),
        (key_stats, "sharpe", rf"\bSharpe Ratio\s*:\s*{re.escape(str(evidence.stat('Sharpe Ratio')))}\b"),
        (methodology, "calendar_duration", rf"\b{evidence.calendar_days}\s+calendar[ -]day\b"),
        (methodology, "lookback", rf"\b{re.escape(evidence.years)}\s+years?\s+of\s+observations\b"),
    )
    for surface, field, pattern in fields:
        _require(surface, field, pattern, errors)


def _compare_number(text: str, label: str, expected: str, errors: list[str]) -> None:
    # Match the immediate numeric token so one prose occurrence cannot consume a
    # later duplicate after HTML whitespace has been normalized.
    pattern = rf"\b{re.escape(label)}\b\s*(?:is|was|:|=)\s*([-+]?\d[\d,]*(?:\.\d+)?\s*%?)"
    for match in re.finditer(pattern, text, re.I):
        displayed = match.group(1).strip()
        actual_num, expected_num = _number(displayed), _number(expected)
        if actual_num is None or expected_num is None or abs(actual_num - expected_num) > 1e-9:
            errors.append(f"{label} does not match evidence: displayed {displayed!r}, expected {expected!r}")


def validate_article_integrity(article_html: str, *, evidence: EvidenceBundle | None = None,
                               symbol: str = "", days: int = 0, years: str = "",
                               direction: str = "", cdata: Any = None,
                               require_hero: bool = True) -> Dict[str, Any]:
    """Compare generated HTML with immutable evidence; missing evidence always blocks."""
    html, text = article_html or "", _customer_visible_text(article_html or "")
    errors: list[str] = []
    warnings: list[str] = []
    if evidence is None:
        return {"ok": False, "errors": ["exact-data evidence bundle is missing"], "warnings": [], "facts": {}}
    symbol, days, years, direction = evidence.symbol, evidence.calendar_days, evidence.years, evidence.direction
    _validate_required_display(html, evidence, errors)

    if not re.search(rf"\b{re.escape(symbol)}\b", text, re.I):
        errors.append(f"symbol {symbol} is absent from article text")
    if require_hero and not re.search(r'<figure\b[^>]*class=["\'][^"\']*\bhero\b', html, re.I):
        errors.append("hero figure is missing")
    if "<html" not in html.lower() or "</html>" not in html.lower() or "<article" not in html.lower():
        errors.append("article is not a complete HTML document")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if title and h1 and text_content(title.group(1)).casefold() != text_content(h1.group(1)).casefold():
        errors.append("HTML title and H1 do not match")

    bad_days = sorted(set(m.group(0) for m in _TRADING_DAY_RE.finditer(text)))
    if bad_days:
        errors.append("TradeWave duration is mislabeled as trading days: " + ", ".join(bad_days[:5]))
    if _FORECAST_RE.search(text):
        errors.append("historical average trend is described as a price forecast or prediction")
    if _PROJECTION_RE.search(text) and not _ALLOWED_PROJECTION_RE.search(text):
        errors.append("projection language appears without defining it as the historical average trend/path")
    if _PERCENT_RE.search(text) and not _SAMPLE_RE.search(text):
        warnings.append("100% result appears without an adjacent n-of-n sample-size formulation")
    if _CURRENT_PRICE_RE.search(text) and 'class="price-asof"' not in html:
        errors.append("current-price language appears without one authoritative price-asof marker")
    if len(re.findall(r'class=["\']price-asof["\']', html, re.I)) > 1:
        errors.append("multiple authoritative price-asof markers are present")
    if _CUMULATIVE_RE.search(text) and not _CUMULATIVE_DEFINITION_RE.search(text):
        errors.append("cumulative return is shown without defining sum versus compounding")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _CAUSAL_RE.search(sentence) and not _HYPOTHESIS_RE.search(sentence):
            errors.append("unsupported unhedged causal sentence: " + sentence[:240])

    displayed_symbol = _displayed_value(text, "Symbol")
    if displayed_symbol and displayed_symbol.split()[0].strip("(),.").upper() != symbol:
        errors.append(f"symbol does not match evidence: displayed {displayed_symbol!r}, expected {symbol!r}")
    for label, expected in (("Pattern Start Date", evidence.start_date), ("Start Date", evidence.start_date),
                            ("Pattern End Date", evidence.end_date), ("End Date", evidence.end_date)):
        displayed = _displayed_value(text, label)
        if displayed and expected not in displayed:
            errors.append(f"{label.lower()} does not match evidence: displayed {displayed!r}, expected {expected!r}")
    duration = _displayed_value(text, "Duration")
    if duration is not None and _number(duration) != float(days):
        errors.append(f"duration does not match evidence: displayed {duration!r}, expected {days} calendar days")
    displayed_direction = _displayed_value(text, "Trade Direction")
    if displayed_direction and not re.match(rf"{re.escape(direction)}\b", displayed_direction, re.I):
        errors.append(f"direction does not match evidence: displayed {displayed_direction!r}, expected {direction!r}")
    for label, expected in evidence.stats:
        _compare_number(text, label, expected, errors)
    winners = int(_number(evidence.stat("Num Winners")) or 0)
    losers = int(_number(evidence.stat("Num Losers")) or 0)
    expected_percent = _number(evidence.stat("Percent Profitable"))
    # Restrict free-form matching to clauses that explicitly describe profitability;
    # unrelated market percentages and generic n-of-n prose are intentionally ignored.
    for clause in re.split(r"(?<=[.!?;])\s+", text):
        if not _PROFITABLE_RE.search(clause):
            continue
        for match in _ANY_PERCENT_RE.finditer(clause):
            actual = float(match.group(1))
            if expected_percent is None or abs(actual - expected_percent) > 1e-9:
                errors.append(f"Percent Profitable does not match evidence: displayed {match.group(0)!r}, expected {evidence.stat('Percent Profitable')!r}")
        for sample in _ANY_SAMPLE_RE.finditer(clause):
            if (int(sample.group(1)), int(sample.group(2))) != (winners, winners + losers):
                errors.append(f"winner/sample count does not match evidence: displayed {sample.group(0)!r}, expected {winners} of {winners + losers}")
    # Catch common unlabeled restatements while leaving unrelated market
    # percentages outside this exact-data domain.
    for match in re.finditer(r"\b(?:win rate(?: of)?|won\s+\d+\s+times\s+in\s+\d+\s+years[^.%]*?)\s*(\d+(?:\.\d+)?)\s*%", text, re.I):
        if expected_percent is None or abs(float(match.group(1)) - expected_percent) > 1e-9:
            errors.append(f"alternate win-rate phrasing contradicts evidence: {match.group(0)!r}")
    for match in re.finditer(r"\b(?:runs?|window)\s+from\s+(\d{4}-\d{2}-\d{2})\s+(?:through|to)\s+(\d{4}-\d{2}-\d{2})", text, re.I):
        if match.groups() != (evidence.start_date, evidence.end_date):
            errors.append(f"alternate date-range phrasing contradicts evidence: {match.group(0)!r}")

    sentences = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 12]
    if any(n > 1 for n in Counter(sentences).values()):
        warnings.append("long sentence(s) are repeated verbatim")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "facts": {"calendar_days": days, "lookback": years, "symbol": symbol,
                      "direction": direction, "start_date": evidence.start_date,
                      "end_date": evidence.end_date, "calendar_id": evidence.calendar_id,
                      "asset_family": evidence.asset_family}}
