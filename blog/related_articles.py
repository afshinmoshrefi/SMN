"""
related_articles.py
===================

Purpose
-------
Given a catalog of Seasonal Market News articles (like the JSON you pasted),
compute "related articles" for each one based on:

    • Same ticker continuity
    • Sector / market_family proximity
    • Thematic overlap (seasonal / earnings / AI / etc.)
    • Recency
    • Volume leader status (top 50 stocks get recent SPX/DJI articles)

The scoring is deterministic and cheap enough to run inside your
article_post_process step for every article.

Typical Usage
-------------
1) As a library from article_post_process.py:

    from related_articles import select_related_articles

    all_articles = load_articles_json_somehow()
    for article in all_articles:
        related = select_related_articles(
            article, 
            all_articles, 
            max_related=6,
            volume_csv_dir="/home/flask/blog/volume_lists"
        )
        article["related_articles"] = related
        # then inject HTML or write JSON, etc.

2) As a batch refresh script (see refresh_related_articles.py)

Input format
------------
The script expects a JSON file with a top-level list of dicts, each shaped like:

    {
        "title": "...",
        "url": "http://....html",
        "symbol": "GOOG",
        "market_family": "US",
        "pattern_start_date": "2025-12-03",
        "pattern_days": 62,
        "lookback_years": "10",
        "published_date": "2025-12-04T16:59:38Z",
        ...
    }

Only a small subset (url, symbol, market_family, title, dek, published_date)
is actually used by the scoring logic.
"""

from __future__ import annotations

import json
import csv
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Tuple, Optional

# ---------------------------------------------------------------------
# Theme detection configuration
# ---------------------------------------------------------------------

_THEME_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "seasonal": (
        "seasonal window", "seasonal tailwind", "seasonal stretch",
        "seasonal pattern", "seasonality", "seasonal"
    ),
    "earnings": (
        "earnings", "results", "quarter ", "q1 ", "q2 ", "q3 ", "q4 ",
        "guidance", "outlook"
    ),
    "ai": (
        " ai ", "artificial intelligence", "cloud", "data center",
        "data-centre", "gpu", "chips"
    ),
    "dividend": (
        "dividend", "payout", "yield", "distribution"
    ),
    "volatility": (
        "volatility", "vix"
    ),
    "election_cycle": (
        "election", "post-election", "post election",
        "midterm", "presidential cycle", "year after the presidential election"
    ),
}

# ---------------------------------------------------------------------
# Volume Leader Detection
# ---------------------------------------------------------------------

def _load_volume_leaders(volume_csv_dir: str, top_n: int = 50) -> Set[str]:
    """
    Load top N volume leaders from CSV files.
    
    Reads both:
    - highest_volume_list.csv (ticker, avg_volume_30d, today_volume, rvol)
    - highest_volume_spikes.csv (ticker, avg_volume_30d, today_volume, rvol)
    
    Returns set of ticker symbols (uppercased).
    """
    volume_leaders: Set[str] = set()
    csv_dir = Path(volume_csv_dir)
    
    # Load from highest_volume_list.csv
    volume_list_path = csv_dir / "highest_volume_list.csv"
    if volume_list_path.exists():
        try:
            with volume_list_path.open('r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    if count >= top_n:
                        break
                    # Column name is 'ticker' not 'Symbol'
                    symbol = row.get('ticker', '').strip().upper()
                    if symbol:
                        volume_leaders.add(symbol)
                        count += 1
        except Exception as e:
            print(f"[WARNING] Could not load {volume_list_path}: {e}")
    
    # Load from highest_volume_spikes.csv
    volume_spikes_path = csv_dir / "highest_volume_spikes.csv"
    if volume_spikes_path.exists():
        try:
            with volume_spikes_path.open('r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    if count >= top_n:
                        break
                    # Column name is 'ticker' not 'Symbol'
                    symbol = row.get('ticker', '').strip().upper()
                    if symbol:
                        volume_leaders.add(symbol)
                        count += 1
        except Exception as e:
            print(f"[WARNING] Could not load {volume_spikes_path}: {e}")
    
    return volume_leaders


def _find_recent_index_article(
    all_articles: List[Dict[str, Any]], 
    max_age_days: int = 14
) -> Optional[Dict[str, Any]]:
    """
    Find the most recent SPX or DJI article published within max_age_days.
    
    Returns the most recent one between SPX and DJI.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=max_age_days)
    
    index_symbols = {'SPX', 'DJI', 'DJIA'}
    candidates = []
    
    for article in all_articles:
        if article.get('market_family') != 'INDX':
            continue
        
        symbol = (article.get('symbol') or '').upper()
        if symbol not in index_symbols:
            continue
        
        pub_date_str = article.get('published_date')
        if not pub_date_str:
            continue
        
        try:
            pub_date = datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            try:
                pub_date = datetime.strptime(pub_date_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
        
        if pub_date >= cutoff:
            candidates.append((pub_date, article))
    
    if not candidates:
        return None
    
    # Return most recent
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------


def _parse_published_dt(article: Dict[str, Any]) -> datetime:
    """
    Parse published_date to datetime.

    Expected primary format:
        "2025-11-13T02:37:49Z"

    Fallback:
        "2025-11-13"
    """
    raw = article.get("published_date")
    if not raw:
        return datetime(1970, 1, 1)

    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return datetime(1970, 1, 1)


def _extract_themes(article: Dict[str, Any]) -> Set[str]:
    """
    Very cheap theme tagging based on title + dek (description paragraph).

    Themes:
        seasonal, earnings, ai, dividend, volatility, election_cycle
    """
    text = ((article.get("title") or "") + " " + (article.get("dek") or "")).lower()
    themes: Set[str] = set()
    for theme, keywords in _THEME_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                themes.add(theme)
                break
    return themes


def _recency_score(published_dt: datetime, newest_dt: datetime) -> float:
    """
    Map relative age (in days from newest article) to [0,1].

    Newest article in the catalog gets the biggest boost.
    """
    delta_days = max(0, (newest_dt - published_dt).days)
    if delta_days <= 7:
        return 1.0
    if delta_days <= 14:
        return 0.7
    if delta_days <= 30:
        return 0.4
    return 0.1


def _sector_bucket(symbol: str, market_family: str) -> str:
    """
    Ultra-light sector / bucket mapping.

    This is intentionally simple and easily extendable. Unknowns fall
    back to "MF_<market_family>" so that at least US / ETF / COMM /
    INDX grouping still gives some structure.
    """
    symbol = (symbol or "").upper()
    tech = {"AAPL", "MSFT", "GOOG", "GOOGL", "NVDA", "AVGO", "AMAT", "ORCL", "MPWR", "ADI", "CTSH", "NDAQ"}
    financials = {"FICO", "PGR", "JPM", "BAC"}
    consumer = {"NKE", "MCD", "WYNN", "MGM", "SBUX", "BKNG", "SJM", "CHD", "KMB", "T"}
    health = {"MRK", "BDX", "HCA"}
    industrials = {"MMM", "BA", "GE", "L", "CSX", "HCA", "LMT"}
    comm = {"GC", "GLD", "CL", "VIX"}

    if symbol in tech:
        return "TECH"
    if symbol in financials:
        return "FINANCIALS"
    if symbol in consumer:
        return "CONSUMER"
    if symbol in health:
        return "HEALTHCARE"
    if symbol in industrials:
        return "INDUSTRIALS"
    if symbol in comm:
        return "COMMODITIES"
    return f"MF_{market_family}"


def _relation_type(base: Dict[str, Any], cand: Dict[str, Any]) -> str:
    """
    Simple label for why two articles are related.
    """
    if (cand.get("symbol") or "").upper() == (base.get("symbol") or "").upper():
        return "same_ticker"
    if cand.get("market_family") == "INDX":
        return "index_macro"
    if cand.get("market_family") == base.get("market_family"):
        return "same_family"
    return "thematic"


# ---------------------------------------------------------------------
# Core selection API
# ---------------------------------------------------------------------


def select_related_articles(
    base_article: Dict[str, Any],
    all_articles: List[Dict[str, Any]],
    max_related: int = 6,
    volume_csv_dir: Optional[str] = None,
    index_article_max_age_days: int = 14,
) -> List[Dict[str, Any]]:
    """
    Compute a list of up to `max_related` related articles for `base_article`.
    
    NEW: If base_article symbol is in top 50 volume leaders AND a recent 
    SPX/DJI article exists, it will be included in results (either competing 
    for position or as a bonus 7th article).

    Scoring:
        Score = (Ticker_Match × 40)
              + (Sector_Match × 20)
              + (Theme_Match × 25)
              + (Recency_Bonus × 15)

    - Ticker_Match:
        same primary ticker → 1.0
        otherwise → 0

    - Sector_Match:
        same sector bucket → 1.0
        same market_family (US vs ETF vs COMM vs INDX) → 0.5

    - Theme_Match:
        fraction of overlapping themes between base and candidate,
        capped at 1.0.

    - Recency_Bonus:
        newest article in catalog has bonus 1.0, decays with age.

    Output shape (for each related item):
        {
            "url": ...,
            "title": ...,
            "symbol": ...,
            "relation_type": "same_ticker" | "index_macro" | "same_family" | "thematic",
            "published_date": "2025-12-04T16:18:00Z"
        }
    """
    if not all_articles:
        return []

    base_url = base_article.get("url")
    base_symbol = (base_article.get("symbol") or "").upper()
    base_mf = base_article.get("market_family") or ""
    base_themes = _extract_themes(base_article)
    base_sector = _sector_bucket(base_symbol, base_mf)

    # Check if this symbol is a volume leader
    is_volume_leader = False
    index_article = None
    if volume_csv_dir and base_mf == "US":
        volume_leaders = _load_volume_leaders(volume_csv_dir, top_n=50)
        is_volume_leader = base_symbol in volume_leaders
        
        if is_volume_leader:
            index_article = _find_recent_index_article(all_articles, index_article_max_age_days)
            if index_article:
                print(f"[INFO] Volume leader {base_symbol} gets index article: {index_article.get('symbol')}")

    # Enrich all articles with cached fields used in scoring
    enriched: List[Dict[str, Any]] = []
    for art in all_articles:
        art_copy = dict(art)
        art_copy["_published_dt"] = _parse_published_dt(art_copy)
        art_copy["_themes"] = _extract_themes(art_copy)
        art_copy["_sector"] = _sector_bucket(
            art_copy.get("symbol", ""), art_copy.get("market_family", "")
        )
        enriched.append(art_copy)

    newest_dt = max(a["_published_dt"] for a in enriched)

    scored_candidates: List[Tuple[float, datetime, Dict[str, Any]]] = []

    for cand in enriched:
        # Never link to itself
        if cand.get("url") == base_url:
            continue

        sym = (cand.get("symbol") or "").upper()
        mf = cand.get("market_family") or ""
        themes = cand["_themes"]
        pub_dt = cand["_published_dt"]
        sector = cand["_sector"]

        # Ticker match
        ticker_match = 1.0 if sym == base_symbol and sym != "" else 0.0

        # Sector / family match
        sector_match = 0.0
        if sector == base_sector and sector != "":
            sector_match = 1.0
        elif mf == base_mf and mf != "":
            sector_match = 0.5

        # Theme overlap
        if base_themes:
            overlap = len(base_themes & themes)
            theme_match = min(1.0, overlap / max(1.0, float(len(base_themes))))
        else:
            theme_match = 0.0

        # Recency
        recency = _recency_score(pub_dt, newest_dt)

        score = (
            ticker_match * 40.0
            + sector_match * 20.0
            + theme_match * 25.0
            + recency * 15.0
        )

        # Skip total zeros so we do not spam garbage
        if score <= 0:
            continue

        scored_candidates.append((score, pub_dt, cand))

    # Sort by score desc, then by recency desc
    scored_candidates.sort(key=lambda x: (-x[0], -x[1].timestamp()))

    used_symbols: Set[str] = set()
    same_ticker_count = 0
    # Cap how many same-ticker articles can appear so the related list does not
    # fill up with near-duplicate stories about the base article's own ticker.
    SAME_TICKER_MAX = 1
    related: List[Dict[str, Any]] = []

    for score, pub_dt, cand in scored_candidates:
        if len(related) >= max_related:
            break

        sym = (cand.get("symbol") or "").upper()

        # Limit same-ticker articles to SAME_TICKER_MAX; for other tickers, one each.
        if sym == base_symbol and sym != "":
            if same_ticker_count >= SAME_TICKER_MAX:
                continue
        elif sym in used_symbols:
            continue

        rel_type = _relation_type(base_article, cand)

        related.append(
            {
                "url": cand.get("url"),
                "title": cand.get("title"),
                "symbol": sym,
                "relation_type": rel_type,
                "published_date": cand.get("published_date"),
            }
        )
        used_symbols.add(sym)
        if sym == base_symbol and sym != "":
            same_ticker_count += 1

    # If volume leader and we have an index article, add it
    # Strategy: if index article scored well enough, it's already in the list
    # Otherwise, add it as a bonus 7th article
    if index_article and is_volume_leader:
        index_url = index_article.get("url")
        index_symbol = (index_article.get("symbol") or "").upper()
        
        # Check if already included
        already_included = any(r.get("url") == index_url for r in related)
        
        if not already_included:
            # Add as bonus article (position 7)
            related.append({
                "url": index_article.get("url"),
                "title": index_article.get("title"),
                "symbol": index_symbol,
                "relation_type": "index_macro",
                "published_date": index_article.get("published_date"),
            })
            print(f"[INFO] Added index article as bonus for {base_symbol}")

    return related


# ---------------------------------------------------------------------
# Optional helper: build mapping for ALL articles in one shot
# ---------------------------------------------------------------------


def build_related_index(
    all_articles: List[Dict[str, Any]],
    max_related: int = 6,
    volume_csv_dir: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Compute related-article lists for every article in the catalog.

    Returns dict keyed by article URL (stable identifier), e.g.:

        {
            "http://.../alphabet-inc-class-c-goog.html": [
                {...}, {...}, ...
            ],
            ...
        }

    This is convenient for a batch post-process job where you want to
    decorate each HTML file with a related-articles block.
    """
    related_by_url: Dict[str, List[Dict[str, Any]]] = {}
    for article in all_articles:
        url = article.get("url")
        if not url:
            continue
        related_by_url[url] = select_related_articles(
            article, 
            all_articles, 
            max_related,
            volume_csv_dir=volume_csv_dir
        )
    return related_by_url


# ---------------------------------------------------------------------
# Main for testing
# ---------------------------------------------------------------------

if __name__ == "__main__":
    """
    Smoketest with hardcoded paths - no arguments
    """
    import sys
    sys.path.insert(0, '/home/flask')
    import config
    
    # HARD-CODED CONFIGURATION
    catalog_path = f"{config.news_root_folder}/posts.json"
    volume_csv_dir = getattr(config, 'volume_csv_dir', '/home/flask/blog/volume_lists')
    
    print("=" * 70)
    print("RELATED ARTICLES SMOKETEST")
    print("=" * 70)
    
    if not Path(catalog_path).exists():
        print(f"[ERROR] Catalog not found: {catalog_path}")
        print("[ERROR] Check config.news_root_folder setting")
        exit(1)
    
    with open(catalog_path, 'r', encoding='utf-8') as f:
        articles = json.load(f)
    
    print(f"[INFO] Loaded {len(articles)} articles from catalog")
    
    # Test with first article
    if articles:
        test_article = articles[0]
        print(f"\n[TEST] Finding related articles for: {test_article.get('title')}")
        print(f"[TEST] Symbol: {test_article.get('symbol')}")
        
        related = select_related_articles(
            test_article, 
            articles, 
            max_related=6,
            volume_csv_dir=volume_csv_dir
        )
        
        print(f"\n[RESULTS] Found {len(related)} related articles:")
        for i, r in enumerate(related, 1):
            print(f"  {i}. [{r['relation_type']}] {r['symbol']} - {r['title']}")