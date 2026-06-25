import re
import json
from slugify import slugify
from pathlib import Path
import os, sys, socket
sys.path.insert(0, "/home/flask")
import config


RECENT_TITLES_FILENAME = "recent_titles.json"
DEFAULT_MAX_KEEP = 400


_hostname = socket.gethostname()

def _base_social_root():
    # Highest priority: explicit folder in config
    folder = getattr(config, "socialmedia_thumbnail_folder", None)
    if folder:
        return folder
    # Default: WP-style paths; dev has extra /wordpress/
    return "/var/www/html/wordpress/wp-content/uploads/p/" if _hostname == "afshin-VirtualBox" \
           else "/var/www/html/wp-content/uploads/p/"

# Final base out dir for articles (exactly like article_images.py)
BASE_OUT_DIR = config.article_images_folder

def get_out_dir(date_str: str) -> str:
    out_dir = os.path.join(BASE_OUT_DIR, str(date_str)[:4], str(date_str))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def abs_to_rel(abs_path: str) -> str:
    # Use news_root_folder as the base for relative paths
    news_root = str(Path(config.news_root_folder).resolve()).rstrip("/") + "/"
    if abs_path.startswith(news_root):
        rel = abs_path[len(news_root):]
        return "/" + rel.lstrip("/")
    # Fallback: try web_root_dir for backward compatibility
    web_root = config.web_root_dir
    if not abs_path.startswith(web_root):
        raise RuntimeError(f"abs_to_rel error: abs_path '{abs_path}' does not start with news_root_folder '{news_root}' or web_root_dir '{web_root}'")
    rel = abs_path[len(web_root):]
    return rel if rel.startswith("/") else "/" + rel

def rel_to_url(rel_path: str) -> str:
    # Use news_website_url for article URLs
    base = getattr(config, "news_website_url", "").strip()
    if not base:
        # Fallback to domain_root if news_website_url not set
        base = getattr(config, "domain_root", "").strip()
    base = base.rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "http://" + base
    return f"{base}{rel_path}"

# def get_article_image_paths(date_str: str, symbol: str, filename: str):
#     """
#     Returns a tuple (out_dir, abs_path, rel_path, url)
#     using TradeWave's folder and URL logic.
#     """
#     out_dir = os.path.join(BASE_OUT_DIR, str(date_str)[:4], str(date_str))
#     os.makedirs(out_dir, exist_ok=True)
#     abs_path = os.path.join(out_dir, filename)
#     rel_path = abs_to_rel(abs_path)
#     url = rel_to_url(rel_path)

    

#     return out_dir, abs_path, rel_path, url - updated 11/25/2025
def get_article_image_paths(resource_id, date_str: str, symbol: str, filename: str):
    """
    Returns (out_dir, abs_path, rel_path, url) for article images.

    Directory scheme (with articles_subfolder='articles'):
      news_root_folder / articles / <market_family> / YYYY / MM / DD / filename

    Example (with articles_subfolder='articles'):
      /var/www/smn/articles/US/2025/11/26/hero_NKE.jpg
    
    Example (with articles_subfolder=''):
      /var/www/smn/US/2025/11/26/hero_NKE.jpg
    """

    # 1) resolve market family for this resource
    market_family = config.exchange_mapping[resource_id]  # e.g. "US", "ETF", "FUT", etc.

    # 2) split date
    yyyy, mm, dd = date_str.split("-")

    # 3) get optional articles subfolder
    articles_subfolder = getattr(config, "articles_subfolder", "").strip().strip("/")

    # 4) build output directory path
    news_root = Path(config.news_root_folder).resolve()
    if articles_subfolder:
        out_dir = os.path.join(str(news_root), articles_subfolder, market_family, yyyy, mm, dd)
    else:
        out_dir = os.path.join(str(news_root), market_family, yyyy, mm, dd)
    os.makedirs(out_dir, exist_ok=True)

    # 5) absolute path on disk
    abs_path = os.path.join(out_dir, filename)

    # 6) relative path for URL (from news root, includes subfolder if set)
    if articles_subfolder:
        rel_path = f"/{articles_subfolder}/{market_family}/{yyyy}/{mm}/{dd}/{filename}"
    else:
        rel_path = f"/{market_family}/{yyyy}/{mm}/{dd}/{filename}"

    # 7) full URL using news_website_url
    url = rel_to_url(rel_path)

    return out_dir, abs_path, rel_path, url
#-----------------------------------------------------------------------------------------------
def compute_article_paths_and_url(resource_id, symbol, pattern_start_date, days, years, article_html):
    """
    Pure-ish helper: given article HTML and identity (resource_id, symbol, date, days, years),
    compute:
      - cleaned HTML (PE title normalization)
      - title and slug
      - article filesystem path and URL
      - dataset filesystem path and URL

    This is safe to call from article_post_process.py WITHOUT doing any IO.
    """

    # Normalize days / years
    days_int  = int(days)
    years_str = str(years)

    # 1) Strip stray code fences (same as old publish_article_to_folder)
    article_html = re.sub(r'^\s*(```|\'\'\'|""")\s*$', '', article_html, flags=re.MULTILINE)

    # 2) Sanitize title/h1 for PE shorthand
    article_html = sanitize_article_html_for_titles(article_html)

    # 3) Title + slug
    title = extract_title_from_html(article_html)
    if title is None:
        title = "Article doesn't have a title - This is remove or a test title for dev or debug"
    slug = slugify(title)

    # 4) Market family and date pieces
    market_family = detect_market_family(resource_id)
    yyyy, mm, dd  = pattern_start_date.split("-")  # "YYYY-MM-DD"

    # 5) Get optional articles subfolder
    articles_subfolder = getattr(config, "articles_subfolder", "").strip().strip("/")

    # 6) Article root on disk
    news_root = Path(config.news_root_folder).resolve()

    # 7) Article directory: <news_root_folder>/[articles_subfolder/]<market_family>/YYYY/MM/DD/
    if articles_subfolder:
        rel_dir  = Path(articles_subfolder) / market_family / yyyy / mm / dd
    else:
        rel_dir  = Path(market_family) / yyyy / mm / dd
    out_dir  = news_root / rel_dir
    out_path = out_dir / f"{slug}.html"

    # 8) Public URL using news_website_url
    if articles_subfolder:
        url_path = f"/{articles_subfolder}/{market_family}/{yyyy}/{mm}/{dd}/{slug}.html"
    else:
        url_path = f"/{market_family}/{yyyy}/{mm}/{dd}/{slug}.html"

    # Get base URL from news_website_url (with fallback to domain_root)
    base_url = getattr(config, "news_website_url", "").strip()
    if not base_url:
        base_url = getattr(config, "domain_root", "").strip()
    base_url = base_url.rstrip("/")
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = "http://" + base_url

    full_url = base_url + url_path

    # 9) Dataset file / URL
    # Naming convention: SYMBOL_YYYY-MM-DD_DAYS_YEARS_dataset.json
    dataset_filename = f"{symbol.upper()}_{pattern_start_date}_{days_int}_{years_str}_dataset.json"

    # Dataset directory on disk: <news_root_folder>/datasets/
    # NOTE: datasets stay at news_root level, NOT inside articles_subfolder
    dataset_dir  = news_root / "datasets"
    dataset_path = dataset_dir / dataset_filename

    # Dataset URL: base_url/datasets/<file>
    dataset_url_path = "/datasets/" + dataset_filename
    dataset_full_url = base_url + dataset_url_path

    return {
        "article_html": article_html,          # cleaned, PE-normalized HTML
        "title": title,
        "slug": slug,
        "market_family": market_family,
        "news_root": str(news_root),
        "rel_dir": str(rel_dir),
        "out_dir": str(out_dir),
        "out_path": str(out_path),
        "url_path": url_path,
        "full_url": full_url,
        "dataset_filename": dataset_filename,
        "dataset_dir": str(dataset_dir),
        "dataset_path": str(dataset_path),
        "dataset_url_path": dataset_url_path,
        "dataset_full_url": dataset_full_url,
        "yyyy": yyyy,
        "mm": mm,
        "dd": dd,
        "days_int": days_int,
        "years_str": years_str,
    }
#-----------------------------------------------------------------------------------------------
def _replace_many(text: str, mapping: dict) -> str:
    for pat, rep in mapping.items():
        text = re.sub(pat, rep, text)
    return text
#-----------------------------------------------------------------------------------------------
def sanitize_article_html_for_titles(html: str) -> str:
    """
    Rewrites PE shorthand in <title> and <h1> only.
    Leaves body prose alone (WP flow strips the first <h1> anyway).
    """
    def _fix_tag(tag, s):
        return re.sub(
            fr"(<{tag}[^>]*>)(.*?)(</{tag}>)",
            lambda m: m.group(1) + _replace_many(m.group(2), PE_TITLE_MAP) + m.group(3),
            s,
            flags=re.IGNORECASE | re.DOTALL
        )
    out = _fix_tag("title", html)
    out = _fix_tag("h1", out)
    return out
#-----------------------------------------------------------------------------------------------
def extract_title_from_html(html_article):
    """
    Extracts the <title> text from an HTML string using regex.
    Returns None if no <title> tag is found.
    """
    match = re.search(r"<title>(.*?)</title>", html_article, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
#-----------------------------------------------------------------------------------------------
def detect_market_family(resource_id):
    return config.exchange_mapping[resource_id]


# ---------- PE-cycle normalization for titles/headlines ----------
PE_TITLE_MAP = {
    r"\bPE\+1\b": "the year after the election",
    r"\bPE\+2\b": "the midterm election year",
    r"\bPE\+3\b": "the year before the presidential election",
    r"\bPE0\b":   "the presidential election year",
}
#-----------------------------------------------------------------------------------------------
def rebuild_recent_titles(posts_json_path: str, max_keep: int = DEFAULT_MAX_KEEP) -> str:
    posts_path = Path(posts_json_path).resolve()
    if not posts_path.exists():
        raise FileNotFoundError(str(posts_path))

    base_dir = posts_path.parent
    out_path = base_dir / RECENT_TITLES_FILENAME

    # Load posts.json (hard-crash on invalid JSON)
    posts = json.loads(posts_path.read_text(encoding="utf-8"))

    if not isinstance(posts, list):
        raise TypeError(f"posts.json must be a JSON list, got {type(posts)}")

    titles: list[str] = []
    for i, post in enumerate(posts):
        if not isinstance(post, dict):
            raise TypeError(f"posts.json[{i}] must be a dict, got {type(post)}")

        t = post.get("title")
        if t is None:
            # posts.json can be messy; just skip entries without titles
            continue
        if not isinstance(t, str):
            raise TypeError(f"posts.json[{i}].title must be str, got {type(t)}")

        t = t.strip()
        if not t:
            continue

        titles.append(t)

    # Deduplicate while preserving order (keep last occurrence)
    # We want the "most recent" title versions, so do it from the end.
    seen = set()
    dedup_rev: list[str] = []
    for t in reversed(titles):
        if t in seen:
            continue
        seen.add(t)
        dedup_rev.append(t)
    titles = list(reversed(dedup_rev))

    if max_keep and len(titles) > max_keep:
        titles = titles[-max_keep:]

    base_dir.mkdir(parents=True, exist_ok=True)

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(titles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)

    return str(out_path)
###################################################################################################################

if __name__ == '__main__':

    rebuild_recent_titles('/var/www/html/wordpress/news/posts.json')


    exit()


    resource_id = 5        # indices common per config.available_resources
    date = "2025-11-06"
    symbol = "SPX"
    days = "47"
    years = "10"


    out_dir = get_out_dir(date)