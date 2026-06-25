"""
citation_gate.py — pre-publish validation of an article's citations.

The 2026-06-20 content audit found the LLM occasionally emits broken/fabricated
citations (placeholder example.com URLs, empty/`#` hrefs, off-topic links,
Sources items with no link, citation markers with no matching source). This gate
catches those BEFORE an article is published, so a finance brand never ships a
fake reference.

Usage (in the publish pipeline, after the article+title are built):
    from citation_gate import validate_citations
    result = validate_citations(article_html, research=research, symbol=symbol)
    if not result["ok"]:
        # block publish — result["violations"] explains why

Design notes:
  * HARD violations (block publish): example.com, empty/`#`/javascript:/host-less
    hrefs, duplicate source URLs, a Sources <li> with no anchor, and an inline
    citation number [n] with no matching Sources item.
  * SOFT warnings (logged, do NOT block by default): a source host that is neither
    whitelisted nor traceable to the research set, and a missing Sources section.
    Pass strict=True to promote the "fabricated host" warning to a hard violation.
  * Pure stdlib + regex. Optional network liveness check is OFF by default
    (check_liveness=True enables a lightweight HEAD/GET per unique host).
"""
import re
from urllib.parse import urlparse

_SOURCES_SECTION_RE = re.compile(
    r'<section[^>]*class="[^"]*\bsources\b[^"]*"[^>]*>(.*?)</section>',
    re.IGNORECASE | re.DOTALL,
)
_LI_RE = re.compile(r'<li\b[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
# inline citation markers like <sup>[3]</sup> or [3] inside a <sup>
_SUP_CITE_RE = re.compile(r'<sup\b[^>]*>\s*\[?(\d{1,3})\]?\s*</sup>', re.IGNORECASE)

_BAD_HOST_SUBSTRINGS = ("example.com", "example.org", "example.net", "yourdomain", "domain.com")


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower().lstrip(".")
    except Exception:
        return ""


def _base_domain(host):
    """reuters.com from www.reuters.com; best-effort, no PSL."""
    parts = (host or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _load_whitelist(whitelist):
    if whitelist is not None:
        return set(whitelist)
    try:
        from article_prompt import WHITELISTED_SOURCE_DOMAINS
        return set(WHITELISTED_SOURCE_DOMAINS)
    except Exception:
        return set()


def _research_hosts(research):
    """Hosts that legitimately appear because Tavily/research surfaced them."""
    hosts = set()
    if not research:
        return hosts
    srcs = []
    if isinstance(research, dict):
        srcs = research.get("sources") or []
        # some shapes nest under research['research']
        if not srcs and isinstance(research.get("research"), dict):
            srcs = research["research"].get("sources") or []
    for s in srcs:
        url = s.get("url") if isinstance(s, dict) else (s if isinstance(s, str) else "")
        h = _host(url)
        if h:
            hosts.add(_base_domain(h))
    return hosts


def validate_citations(article_html, research=None, symbol=None, company=None,
                       whitelist=None, strict=False, check_liveness=False, timeout=6):
    """Return {ok, violations[], warnings[], stats{}}. ok is False iff a HARD
    violation is present (unless strict=True, which also fails on fabricated hosts)."""
    violations, warnings = [], []
    html = article_html or ""

    m = _SOURCES_SECTION_RE.search(html)
    if not m:
        warnings.append("no <section class=\"sources\"> found")
        return {"ok": True, "violations": violations, "warnings": warnings,
                "stats": {"sources": 0, "cited_markers": 0}}

    sources_html = m.group(1)
    items = _LI_RE.findall(sources_html)
    # If the section isn't <li>-based, fall back to treating the whole block as one bucket
    buckets = items if items else [sources_html]

    seen_urls = {}
    n_with_link = 0
    allowed = _load_whitelist(whitelist) | _research_hosts(research)
    # always allow the site's own domain + TradeWave + the book link host
    allowed |= {"tradewave.ai", "seasonalmarketnews.com", "smn-dev.trxstat.com", "amazon.com"}

    for i, li in enumerate(buckets, 1):
        hrefs = _HREF_RE.findall(li)
        if not hrefs:
            violations.append(f"source item #{i} has no link")
            continue
        n_with_link += 1
        for href in hrefs:
            h = (href or "").strip()
            low = h.lower()
            if any(b in low for b in _BAD_HOST_SUBSTRINGS):
                violations.append(f"placeholder URL in source #{i}: {h}")
                continue
            if low in ("", "#") or low.startswith("javascript:") or low.startswith("#"):
                violations.append(f"empty/anchor href in source #{i}: {h!r}")
                continue
            if not (low.startswith("http://") or low.startswith("https://")) or not _host(h):
                violations.append(f"malformed href in source #{i}: {h!r}")
                continue
            # duplicate detection
            key = h.rstrip("/")
            if key in seen_urls:
                violations.append(f"duplicate source URL: {h}")
            seen_urls[key] = seen_urls.get(key, 0) + 1
            # host trustworthiness (soft unless strict)
            bd = _base_domain(_host(h))
            if bd not in allowed:
                msg = f"source #{i} host not whitelisted/traceable: {bd}"
                (violations if strict else warnings).append(msg)

    # inline citation markers must map to a source item
    cited = sorted({int(n) for n in _SUP_CITE_RE.findall(html)})
    n_sources = len(buckets)
    for n in cited:
        if n < 1 or n > n_sources:
            violations.append(f"inline citation [{n}] has no matching source (only {n_sources} sources)")

    # optional network liveness on unique hosts
    if check_liveness and seen_urls:
        dead = _check_liveness(list(seen_urls.keys()), timeout)
        for url in dead:
            violations.append(f"dead/unreachable source URL: {url}")

    ok = len(violations) == 0
    return {
        "ok": ok,
        "violations": violations,
        "warnings": warnings,
        "stats": {"sources": n_sources, "sources_with_link": n_with_link,
                  "unique_urls": len(seen_urls), "cited_markers": len(cited)},
    }


def _check_liveness(urls, timeout=6):
    """Best-effort reachability check; returns the list of dead/unreachable URLs."""
    dead = []
    try:
        import requests
    except Exception:
        return dead  # cannot check; do not fabricate failures
    headers = {"User-Agent": "Mozilla/5.0 (SMN citation-gate)"}
    # 401/403/405/406/429/451 mean reachable-but-restricted (paywall, bot-block,
    # rate-limit) -- NOT dead. The audit confirmed Bloomberg/WSJ/CNBC/Reuters/
    # Seeking Alpha routinely return 403 to bots; treating those as dead would
    # wrongly block legitimate citations.
    PAYWALL_OR_BLOCKED = {401, 403, 405, 406, 429, 451}
    for url in urls:
        try:
            r = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
            if r.status_code >= 400:
                r = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)
            if r.status_code >= 400 and r.status_code not in PAYWALL_OR_BLOCKED:
                dead.append(url)
        except Exception:
            dead.append(url)
    return dead


if __name__ == "__main__":
    # smoketest
    good = '<p>x<sup>[1]</sup></p><section class="sources"><ol>' \
           '<li><a href="https://www.reuters.com/markets/x">Reuters</a></li></ol></section>'
    bad = '<p>x<sup>[1]</sup><sup>[2]</sup></p><section class="sources"><ol>' \
          '<li><a href="https://example.com/foo">Fake</a></li>' \
          '<li>no link here</li></ol></section>'
    print("GOOD:", validate_citations(good))
    print("BAD :", validate_citations(bad))
