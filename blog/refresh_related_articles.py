"""
refresh_related_articles.py
===========================

Two modes:

  1) "today"   -> refresh related-articles blocks for ALL articles published today.
                  Intended to be called after article generation.

  2) "nightly" -> refresh older articles whose last related refresh was >= REFRESH_DAYS ago.
                  Intended to be called once per day (cron or manual scheduler).

All scheduling decisions are DATE based (YYYY-MM-DD), not time based.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional

# Your project root
sys.path.insert(0, "/home/flask")
import config  # type: ignore

from related_articles import select_related_articles
from html_injection import inject_related_articles_html, strip_existing_related_articles
from html_injection import strip_existing_related_articles_schema, generate_related_articles_schema

STATE_FILE = "/home/flask/blog/.related_articles_state.json"
REFRESH_DAYS = 7  # cadence for older articles (7, 14, 21, ...)


# --------------------------------------------------------------------------
# State: last refresh date per URL (YYYY-MM-DD)
# --------------------------------------------------------------------------

class RefreshState:
    """
    Store last refresh date per article URL, plus last_run_date.

    {
      "last_run_date": "2025-12-09",
      "updated_urls": {
        "https://tradewave.ai/news/...": "2025-12-09",
        ...
      }
    }
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = Path(state_file)
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with self.state_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Could not load state file {self.state_file}: {e}")
        return {
            "last_run_date": None,
            "updated_urls": {},  # url -> "YYYY-MM-DD"
        }

    def save(self) -> None:
        today_str = date.today().isoformat()
        self.state["last_run_date"] = today_str
        try:
            with self.state_file.open("w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            print(f"[STATE] Saved state to {self.state_file}")
        except Exception as e:
            print(f"[ERROR] Failed to save state: {e}")

    def mark_updated(self, url: str) -> None:
        self.state["updated_urls"][url] = date.today().isoformat()

    def get_last_update_date(self, url: str) -> Optional[date]:
        s = self.state["updated_urls"].get(url)
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _parse_published_date(raw: Optional[str]) -> date:
    """
    Parse published_date into a date.

    Accepts:
      - full ISO strings like "2025-12-09T10:15:00Z"
      - "YYYY-MM-DD"
    Falls back to 1970-01-01 if parsing fails.
    """
    if not raw:
        return date(1970, 1, 1)
    raw = raw.strip()

    # Try full ISO first
    try:
        return datetime.fromisoformat(raw.replace("Z", "")).date()
    except ValueError:
        pass

    # Fallback to first 10 chars as YYYY-MM-DD
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return date(1970, 1, 1)


def _update_article_html(
    article: Dict[str, Any],
    all_articles: List[Dict[str, Any]],
    volume_csv_dir: str,
    dry_run: bool = False,
) -> bool:
    """
    Recompute and inject related-articles block + ItemList schema for ONE article.
    Returns True on success, False on hard error.
    """
    url = article.get("url", "")
    path_str = article.get("path")
    symbol = article.get("symbol", "")

    label = symbol or url or "<unknown>"

    if not path_str:
        print(f"[SKIP] {label}: no 'path' in catalog entry")
        return False

    html_path = Path(path_str)
    if not html_path.exists():
        print(f"[SKIP] {label}: file not found at {html_path}")
        return False

    try:
        html = html_path.read_text(encoding="utf-8")

        # 1) Remove existing related-articles block AND its ItemList schema (idempotent)
        html = strip_existing_related_articles(html)
        html = strip_existing_related_articles_schema(html)

        # 2) Compute new related articles for this article
        related = select_related_articles(
            article,
            all_articles,
            max_related=6,
            volume_csv_dir=volume_csv_dir,
        )

        if not related:
            print(f"[UPDATE] {label}: no related articles found; writing cleaned HTML only")
            if not dry_run:
                html_path.write_text(html, encoding="utf-8")
            return True

        # 3) Inject new related-articles HTML block
        updated_html = inject_related_articles_html(html, related)

        # 4) Build and inject new ItemList schema into <head>
        if url:
            related_schema = generate_related_articles_schema(url, related)
        else:
            related_schema = None

        if related_schema:
            ld_str = json.dumps(related_schema, ensure_ascii=False, indent=2)
            script_tag = f'<script type="application/ld+json">\n{ld_str}\n</script>\n'

            idx_head_close = updated_html.lower().find("</head>")
            if idx_head_close != -1:
                updated_html = (
                    updated_html[:idx_head_close]
                    + script_tag
                    + updated_html[idx_head_close:]
                )
                print(f"[UPDATE] {label}: injected related ItemList schema")
            else:
                print(f"[WARN] {label}: no </head> found; skipping schema injection")

        # 5) Write updated HTML
        if not dry_run:
            html_path.write_text(updated_html, encoding="utf-8")
            print(f"[UPDATE] {label}: injected {len(related)} related articles")
        else:
            print(f"[DRY-RUN] {label}: would inject {len(related)} related articles + schema")

        return True

    except Exception as e:
        print(f"[ERROR] {label}: failed to update related articles: {e}")
        return False


def _load_catalog() -> List[Dict[str, Any]]:
    """
    Load posts.json from news_root_folder.

    Assumes config.news_root_folder points at the root where posts.json lives.
    """
    catalog_path = Path(f"{config.news_root_folder}/posts.json")
    if not catalog_path.exists():
        raise RuntimeError(f"Catalog not found at {catalog_path}")
    with catalog_path.open("r", encoding="utf-8") as f:
        articles = json.load(f)
    print(f"[INFO] Loaded {len(articles)} articles from {catalog_path}")
    return articles


# --------------------------------------------------------------------------
# Mode 1: "today" - refresh ONLY articles published today
# --------------------------------------------------------------------------

def refresh_today_only(dry_run: bool = False) -> None:
    """
    Refresh related-articles block for all articles whose published_date == today.

    Intended to be called after article generation:
      - as more articles are published today, this will keep recomputing related
        blocks for the entire "today" cluster.
    """
    all_articles = _load_catalog()
    today = date.today()
    volume_csv_dir = getattr(config, "volume_csv_dir", "/home/flask/blog/volume_lists")

    processed = 0
    refreshed = 0

    for article in all_articles:
        raw_pub = article.get("published_date")
        pub_date = _parse_published_date(raw_pub)
        if pub_date != today:
            continue

        ok = _update_article_html(article, all_articles, volume_csv_dir, dry_run=dry_run)
        processed += 1
        if ok:
            refreshed += 1

    print(f"[TODAY] Processed {processed} 'today' articles, refreshed {refreshed}")


# --------------------------------------------------------------------------
# Mode 2: "nightly" - refresh older articles every REFRESH_DAYS
# --------------------------------------------------------------------------

def refresh_nightly(dry_run: bool = False) -> None:
    """
    Refresh older articles whose related-articles block was last updated
    at least REFRESH_DAYS days ago.

    Strategy:
      - Ignore articles from today or yesterday (age_days <= 1).
        Those are handled by refresh_today_only.
      - For the rest, refresh when:
          - never refreshed and age_days >= REFRESH_DAYS, or
          - today - last_refresh_date >= REFRESH_DAYS.
    """
    all_articles = _load_catalog()
    state = RefreshState()
    today = date.today()
    volume_csv_dir = getattr(config, "volume_csv_dir", "/home/flask/blog/volume_lists")

    processed = 0
    refreshed = 0

    for article in all_articles:
        url = article.get("url")
        if not url:
            continue

        pub_date = _parse_published_date(article.get("published_date"))
        age_days = (today - pub_date).days

        # Skip very new content; daily today-only pass handles it
        if age_days <= 1:
            continue

        last_date = state.get_last_update_date(url)

        if last_date is None:
            # Never refreshed: only touch once article is at least REFRESH_DAYS old
            should_refresh = age_days >= REFRESH_DAYS
        else:
            should_refresh = (today - last_date).days >= REFRESH_DAYS

        if not should_refresh:
            continue

        ok = _update_article_html(article, all_articles, volume_csv_dir, dry_run=dry_run)
        processed += 1
        if ok:
            refreshed += 1
            state.mark_updated(url)

    state.save()
    print(f"[NIGHTLY] Processed {processed} older articles, refreshed {refreshed}")


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------

if __name__ == "__main__":
    mode = "today" if len(sys.argv) < 2 else sys.argv[1].strip().lower()
    dry_run = ("--dry-run" in sys.argv)

    if mode == "today":
        refresh_today_only(dry_run=dry_run)
    elif mode == "nightly":
        refresh_nightly(dry_run=dry_run)
    else:
        print(f"Usage: {sys.argv[0]} [today|nightly] [--dry-run]")
        sys.exit(1)
