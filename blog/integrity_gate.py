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


# ============================================================
# Cell-article validation (angle-engine pipeline, Phase 2)
# ============================================================
# Articles produced by angle_writer draw on a STORY CELL plus up to two
# AUXILIARY cells (ANGLE_ENGINE_DESIGN.md §4). Matrix cells use long
# accounting (Trade Dir is 'long' for every arbitrary window), so the
# reader-facing direction is the derived bias, and winner/sample pairs may
# legitimately reference any cell — the single-evidence-bundle checks above
# would false-positive. Chrome surfaces are rendered downstream of every
# model call by angle_chrome.assemble_article, so chrome cannot be tampered
# by the writer; the checks here are belt-and-suspenders on the final HTML.

_BRIDGE_RE = re.compile(
    r"<p\b[^>]*id=[\"']transition_to_tradewave[\"'][^>]*>(.*?)</p>", re.I | re.S)
_EXCURSION_RE = re.compile(
    r"\b(?:MAE|MFE|drawdowns?|adverse excursions?|favorable excursions?|"
    r"intraperiod (?:downside|losses))\b", re.I)
_WINDOW_CLAUSE_RE = re.compile(r"\b(?:years?|windows?|cycles?|summers?|winters?|"
                               r"springs?|autumns?|stretch(?:es)?)\b", re.I)
# Unlike _ANY_SAMPLE_RE, tolerates the quotable phrasing "16 of the last 20".
_CELL_SAMPLE_RE = re.compile(
    r"\b(\d+)\s+(?:of|out of)\s+(?:the\s+)?(?:last\s+|past\s+)?(\d+)\b", re.I)
_DIRECTIONAL_RE = re.compile(r"\b(?:closed|risen|fallen|higher|lower|gained|lost|"
                             r"up|down|winning|losing|profitable|winners?|losers?)\b", re.I)
_CHROME_STRIP_RE = re.compile(
    r"<aside\b[^>]*class=[\"'][^\"']*key-stats[^\"']*[\"'].*?</aside>|"
    r"<div\b[^>]*class=[\"'][^\"']*pattern-meta[^\"']*[\"'].*?</div>|"
    r"<section\b[^>]*class=[\"'][^\"']*(?:sources|methodology-note)[^\"']*[\"'].*?</section>|"
    r"<figure\b.*?</figure>|<script\b.*?</script>", re.I | re.S)
_HEAD_STYLE_RE = re.compile(r"<head\b.*?</head>|<style\b.*?</style>", re.I | re.S)


def _prose_text(html: str) -> str:
    """Reader-visible PROSE only: head, style, script, and every server-
    rendered chrome surface removed. Numeric-discipline scans run here —
    chrome is correct by construction (rendered downstream of the model),
    and its stat rows/CSS would otherwise false-positive every scan."""
    return text_content(_CHROME_STRIP_RE.sub(" ", _HEAD_STYLE_RE.sub(" ", html or "")))


def _allowed_pairs(card: Dict[str, Any]) -> set:
    pairs = set()
    for cell in [card.get("story_cell")] + list(card.get("auxiliary_cells") or []):
        if not isinstance(cell, dict):
            continue
        n = int(cell.get("n") or 0)
        for k in (cell.get("up_years"), cell.get("down_years")):
            if k is not None and n:
                pairs.add((int(k), n))
    return pairs


def _allowed_percents(card: Dict[str, Any]) -> list:
    """Every percentage the Angle Card licenses the prose to quote: the story
    cell's stats values plus per-year net/MFE/MAE and derived medians/extremes
    of the story and auxiliary cells. Used to keep the profitability-percent
    scan from flagging legitimate card numbers quoted in win/loss clauses."""
    values: list = []

    def _add(v: Any) -> None:
        num = _number(v)
        if num is not None:
            values.append(abs(num))
            values.append(round(abs(num), 1))   # quotables format at 1 decimal

    for cell in [card.get("story_cell")] + list(card.get("auxiliary_cells") or []):
        if not isinstance(cell, dict):
            continue
        for v in (cell.get("stats_raw") or {}).values():
            _add(v)
        for key in ("median_net", "avg_net", "best_net", "worst_net",
                    "median_mfe", "median_mae"):
            _add(cell.get(key))
        for row in cell.get("per_year") or []:
            for key in ("net", "mfe", "mae"):
                _add(row.get(key))
    return values


def _issue(errors: list, code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def validate_cell_article(article_html: str, card: Dict[str, Any], *,
                          word_budget: int = 0) -> Dict[str, Any]:
    """Deterministic gate for angle-engine articles. Returns
    {ok, errors:[{code,detail}], warnings:[...]}; error codes are the shared
    revision-loop vocabulary (fed verbatim to build_revision_prompt)."""
    html = article_html or ""
    story = card.get("story_cell") or {}
    errors: list = []
    warnings: list = []
    text = _prose_text(html)

    # --- structural: document, headline identity, bridge, chrome singletons
    if "<article" not in html.lower() or "</html>" not in html.lower():
        _issue(errors, "DOC_INCOMPLETE", "not a complete assembled HTML document")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    h1_text = text_content(h1.group(1)) if h1 else ""
    symbol = str(card.get("symbol", "")).upper()
    if symbol and symbol not in h1_text.upper():
        _issue(errors, "HEADLINE_MISSING_IDENTITY",
               f"headline lacks ticker {symbol}: {h1_text[:80]!r}")
    if len(h1_text.split()) > 16:
        warnings.append(f"headline over 16 words: {h1_text[:90]!r}")

    bridges = _BRIDGE_RE.findall(html)
    if len(bridges) != 1:
        _issue(errors, "BRIDGE_MALFORMED",
               f"expected exactly one transition_to_tradewave paragraph, found {len(bridges)}")
    else:
        bridge_text = text_content(bridges[0])
        if "tradewave" not in bridge_text.lower():
            _issue(errors, "BRIDGE_MALFORMED", "bridge does not mention TradeWave.ai")
        if re.search(r"\d+(?:\.\d+)?\s*%|\b\d+\s+(?:of|out of)\s+\d+\b", bridge_text):
            _issue(errors, "BRIDGE_MALFORMED",
                   f"bridge contains statistics: {bridge_text[:100]!r}")
        # No TradeWave in body prose before the bridge (chrome excluded).
        pre = html[:_BRIDGE_RE.search(html).start()]
        pre = _CHROME_STRIP_RE.sub(" ", pre)
        pre = re.sub(r"<head\b.*?</head>|<h1[^>]*>.*?</h1>|"
                     r"<p\b[^>]*class=[\"'][^\"']*dek[^\"']*[\"'][^>]*>.*?</p>|"
                     r"<div\b[^>]*class=[\"']meta[\"'].*?</div>",
                     " ", pre, flags=re.I | re.S)
        if "tradewave" in text_content(pre).lower():
            _issue(errors, "TW_BEFORE_BRIDGE",
                   "TradeWave appears in body prose before the bridge paragraph")

    for cls, label in (("pattern-meta", "META_STRIP"), ("key-stats", "KEY_STATS")):
        count = len(re.findall(rf"class=[\"'][^\"']*\b{cls}\b", html, re.I))
        if count != 1:
            _issue(errors, "CHROME_MISSING" if count == 0 else "CHROME_DUPLICATED",
                   f"{label} chrome appears {count} times")

    # --- language discipline (reuses the deterministic scans above)
    bad_days = sorted(set(m.group(0) for m in _TRADING_DAY_RE.finditer(text)))
    if bad_days:
        _issue(errors, "TRADING_DAYS_LABEL",
               "TradeWave windows mislabeled as trading days: " + ", ".join(bad_days[:5]))
    if _FORECAST_RE.search(text):
        _issue(errors, "PROJECTION_MISLABELED",
               "historical average trend described as a forecast/prediction")
    if _PROJECTION_RE.search(text) and not _ALLOWED_PROJECTION_RE.search(text):
        _issue(errors, "PROJECTION_MISLABELED",
               "projection language without the historical-average definition")
    if _CUMULATIVE_RE.search(text) and not _CUMULATIVE_DEFINITION_RE.search(text):
        _issue(errors, "CUMULATIVE_UNDEFINED",
               "cumulative return shown without defining sum vs compounding")
    if _PERCENT_RE.search(text) and not _SAMPLE_RE.search(text):
        _issue(errors, "MISSING_SAMPLE_SIZE",
               "100% result appears without an adjacent n-of-n formulation")

    # Causal claims: hedge or citation required, per paragraph.
    for para in re.findall(r"<p\b[^>]*>(.*?)</p>", _CHROME_STRIP_RE.sub(" ", html),
                           re.I | re.S):
        ptext = text_content(para)
        for sentence in re.split(r"(?<=[.!?])\s+", ptext):
            if (_CAUSAL_RE.search(sentence) and not _HYPOTHESIS_RE.search(sentence)
                    and "<sup" not in para.lower()):
                _issue(errors, "CAUSAL_UNSUPPORTED",
                       "unhedged, uncited causal sentence: " + sentence[:160])

    # --- numeric discipline against the card
    allowed = _allowed_pairs(card)
    for clause in re.split(r"(?<=[.!?;])\s+", text):
        if not (_WINDOW_CLAUSE_RE.search(clause) and _DIRECTIONAL_RE.search(clause)):
            continue
        for m in _CELL_SAMPLE_RE.finditer(clause):
            pair = (int(m.group(1)), int(m.group(2)))
            if pair[1] <= 40 and pair not in allowed:
                _issue(errors, "PAIR_MISMATCH",
                       f"record {m.group(0)!r} matches no cell in the Angle Card")
        if _PROFITABLE_RE.search(clause):
            licensed = _allowed_percents(card)
            for pm in _ANY_PERCENT_RE.finditer(clause):
                value = abs(float(pm.group(1)))
                if not any(abs(value - a) < 0.006 for a in licensed):
                    _issue(errors, "PCT_MISMATCH",
                           f"percentage {pm.group(0)!r} in a win/loss clause matches "
                           f"no number in the Angle Card")

    # Labeled stats (Avg Profit: X etc.) must match the story cell verbatim —
    # aux cells are barred from these labels by the prompt contract.
    stat_errors: list = []
    for label, expected in (story.get("stats_raw") or {}).items():
        if label in ("1M Return", "52W High", "52W Low", "last_trade_date", "Trade Dir"):
            continue
        if expected in (None, ""):
            continue
        _compare_number(text, label, str(expected), stat_errors)
    for detail in stat_errors:
        _issue(errors, "NUM_MISMATCH", detail)

    # --- chart semantics: excursion talk requires the MAE/MFE chart
    if _EXCURSION_RE.search(text) and "bars_mae_mfe-chart" not in html:
        _issue(errors, "CHART_SEMANTICS_MISMATCH",
               "prose discusses excursions/drawdowns but the bars_mae_mfe chart is absent")

    # --- engine internals must never surface as reader-facing statistics
    leak = re.search(r"\b(?:tail[\s-]?p\b|p[\s-]?values?|binomial tail|"
                     r"story[\s_-]?score|conviction score|angle score)\b", text, re.I)
    if leak:
        _issue(errors, "INTERNAL_METRIC_LEAK",
               f"engine-internal metric surfaced in prose: {leak.group(0)!r}")

    # --- citations: every sup resolves inside the rendered sources list
    n_sources = len(re.findall(r"<li>\s*<a\b", html)) if 'class="sources"' in html else 0
    for m in re.finditer(r"<sup[^>]*>\s*\[(\d+)\]\s*</sup>", html, re.I):
        if int(m.group(1)) > n_sources:
            _issue(errors, "CITATION_BROKEN",
                   f"citation [{m.group(1)}] exceeds the {n_sources}-item sources list")
            break

    if word_budget:
        words = len(text.split())
        if abs(words - word_budget) > max(150, int(word_budget * 0.25)):
            warnings.append(f"length {words} words vs budget {word_budget}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}
