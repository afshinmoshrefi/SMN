"""Private review artifacts and a disabled-by-default audience policy.

This module does not install an authentication gate or publish anything. The
future serving adapter must derive audience flags from server-side identity.
Public previews contain only preview text, never CSS-hidden full articles.
"""
from __future__ import annotations

import argparse
import datetime
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def audience_has_full_access(*, article_type: str, registered: bool = False,
                             paid: bool = False, enabled: bool = False,
                             mode: str = "registration") -> bool:
    if mode not in ("registration", "paid"):
        raise ValueError("Unknown article access mode")
    if not enabled or article_type == "news":
        return True
    return bool(paid or (registered and mode == "registration"))


class _SafeFragment(HTMLParser):
    allowed = {"h1", "h2", "h3", "h4", "p", "div", "span", "section", "aside",
               "ul", "ol", "li", "strong", "em", "b", "i", "small", "sup", "sub",
               "a", "figure", "figcaption", "img", "table", "thead", "tbody", "tr",
               "td", "th", "blockquote", "br", "hr", "details", "summary", "time"}
    suppressed = {"script", "style", "iframe", "object", "svg", "template", "head"}
    void = {"img", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.suppressed:
            self.skipping += 1
            return
        if self.skipping or tag not in self.allowed:
            return
        safe = []
        for name, value in attrs:
            if value is None:
                continue
            if name in ("href", "src"):
                try:
                    parsed = urlsplit(value.strip())
                except ValueError:
                    continue
                if parsed.username or parsed.password:
                    continue
                if not ((parsed.scheme in ("https", "http") and parsed.hostname)
                        or (name == "href" and value.startswith("#"))):
                    continue
            elif name not in ("class", "id", "alt", "title", "width", "height", "colspan", "scope", "datetime"):
                continue
            safe.append(f'{name}="{html.escape(value, quote=True)}"')
        self.out.append("<" + tag + (" " + " ".join(safe) if safe else "") + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.suppressed:
            self.skipping = max(0, self.skipping - 1)
        elif not self.skipping and tag in self.allowed and tag not in self.void:
            self.out.append("</" + tag + ">")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(html.escape(data))


def safe_fragment(document: str) -> str:
    parser = _SafeFragment()
    parser.feed(document or "")
    return "".join(parser.out)


STYLE = """
:root{--ink:#162537;--muted:#607184;--blue:#155fc0;--line:#dce3eb;--paper:#fff}
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:var(--ink);font:17px/1.68 Georgia,serif}
.masthead{background:#0d233b;color:#fff;padding:22px max(24px,calc((100vw - 960px)/2));font:700 19px/1.3 Arial,sans-serif;letter-spacing:.02em}
.masthead small{display:block;font:12px/1.4 Arial,sans-serif;letter-spacing:.13em;color:#b3c8e1;margin-top:5px}
article{background:var(--paper);max-width:960px;margin:0 auto;padding:42px 58px 60px}
h1{font-size:42px;line-height:1.14;letter-spacing:-.025em;margin:0 0 20px}h2{font:700 23px/1.3 Arial,sans-serif;margin:30px 0 13px}h3{font:700 18px/1.35 Arial,sans-serif}
p{margin:0 0 18px}.dek{font:20px/1.5 Arial,sans-serif;color:var(--muted)}a{color:var(--blue)}
.meta,.pattern-meta,.news-meta,.byline{font:13px/1.6 Arial,sans-serif;color:var(--muted);border-bottom:1px solid var(--line);padding:10px 0;margin:10px 0 24px;display:flex;gap:12px;flex-wrap:wrap}
.direct-answer{font-weight:600}figure{margin:28px 0}img{max-width:100%;height:auto}figcaption{font:13px/1.5 Arial,sans-serif;color:var(--muted)}
aside,.key-takeaways-box,.key-takeaways,.key-stats,.takeaways{background:#f1f6fc;border:1px solid var(--line);border-radius:8px;padding:18px 22px;margin:24px 0;font:16px/1.6 Arial,sans-serif}
.key-takeaways h2{margin-top:0}.article-as-of{font:13px/1.5 Arial,sans-serif;color:var(--muted)}
.key-stats .row{display:flex;justify-content:space-between;gap:20px;padding:7px 0;border-bottom:1px solid var(--line)}
.sources,.methodology-note{border-top:1px solid var(--line);margin-top:32px;padding-top:16px;font:14px/1.6 Arial,sans-serif}
.access-callout{background:#0d233b;color:#fff;padding:24px;border-radius:8px;margin-top:30px;font:16px/1.6 Arial,sans-serif}.access-callout strong{display:block;font-size:20px;margin-bottom:8px}.access-callout p{margin:0}
table{border-collapse:collapse;width:100%;font:14px/1.5 Arial,sans-serif}td,th{border-bottom:1px solid var(--line);padding:10px;text-align:left}
.review-note{font:12px/1.5 Arial,sans-serif;background:#fff3d6;padding:9px 20px;text-align:center;color:#66511d}
@media(max-width:650px){article{padding:28px 20px 42px}h1{font-size:31px}.dek{font-size:18px}.masthead{padding:18px 20px}.key-stats .row{gap:10px}table{font-size:12px}}
"""


def _document(fragment: str, title: str, *, status: str = "preview", issue_summary: str = "") -> str:
    label = "Draft ready for your review" if status in ("ready", "draft_ready") else (
        "Preview" if status == "preview" else "Held for review")
    note = ('<div class="review-note"><strong>' + label + '</strong>. '
            'Private development preview. Access rules and new publishing controls are inactive.'
            + ("<br>" + html.escape(issue_summary) if issue_summary else "") + '</div>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>{note}'
            '<header class="masthead">SEASONAL MARKET NEWS<small>MARKET DEVELOPMENTS · HISTORICAL PERSPECTIVE</small></header>'
            f'<article>{fragment}</article></body></html>')


def build_visitor_fragment(full_fragment: str) -> str:
    """A complete opening paragraph; never cut mid-sentence or conceal the rest."""
    h1 = re.search(r"<h1\b[^>]*>.*?</h1>", full_fragment, re.I | re.S)
    dek = re.search(r'<p\b[^>]*class="[^"]*\bdek\b[^"]*"[^>]*>.*?</p>', full_fragment, re.I | re.S)
    hero = re.search(r'<figure\b[^>]*class="[^"]*\bhero\b[^"]*"[^>]*>.*?</figure>', full_fragment, re.I | re.S)
    paragraphs = re.findall(r"<p\b[^>]*>.*?</p>", full_fragment, re.I | re.S)
    lead = next((p for p in paragraphs if not re.search(r'\bclass="[^"]*(?:dek|meta|chart-bridge|byline)', p, re.I)), "")
    # Preview citations must not point to a sources list that was withheld.
    # Include the compact original sources section whenever the lead cites it.
    sources = re.search(r'<section\b[^>]*class="[^"]*\bsources\b[^"]*"[^>]*>.*?</section>', full_fragment, re.I | re.S)
    pieces = [m.group(0) for m in (h1, dek, hero) if m]
    if lead and lead not in pieces:
        pieces.append(lead)
    pieces.append('<aside class="access-callout"><strong>Continue with a free TradeWave account</strong>'
                  '<p>Read the full seasonal analysis, supporting evidence, and what to watch next.</p></aside>')
    if sources and "<sup" in lead:
        pieces.append(sources.group(0))
    return "\n".join(pieces)


def write_article_preview(result: dict, output_dir: str | Path, *,
                          article_type: str = "seasonal", title: str = "Article preview") -> dict:
    """Write an internal comparison, not a live site. Caller selects a private directory."""
    if article_type not in ("seasonal", "news"):
        raise ValueError("Unknown preview article type")
    root = Path(output_dir).expanduser().resolve()
    if any(part.lower() in ("www", "wwwroot", "public_html", "_static") for part in root.parts):
        raise ValueError("Preview output must be outside publication directories")
    raw = result.get("article_html") or result.get("html") or ""
    if not raw:
        raise ValueError("Article result has no HTML to preview")
    fragment = safe_fragment(raw)
    root.mkdir(parents=True, exist_ok=True)
    status = str(result.get("status") or "preview")
    problems = result.get("errors") or result.get("validation", {}).get("editorial", {}).get("issues") or []
    issue_summary = "; ".join(str(p.get("problem") or p.get("detail") or p) if isinstance(p, dict) else str(p)
                              for p in problems[:3])[:600]
    full = _document(fragment, title, status=status, issue_summary=issue_summary)
    visitor = full if article_type == "news" else _document(build_visitor_fragment(fragment), title, status=status, issue_summary=issue_summary)
    (root / "article.html").write_text(full, encoding="utf-8")
    (root / "visitor.html").write_text(visitor, encoding="utf-8")
    (root / "registered.html").write_text(full, encoding="utf-8")
    note = ("Daily news remains fully public in this proposed model." if article_type == "news"
            else "Visitors see an introduction; a free TradeWave account unlocks the full analysis.")
    index = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
             '<title>SMN development review</title><style>body{margin:0;background:#edf2f7;font:15px Arial,sans-serif;color:#18314d}'
             'header{padding:20px 28px;background:#fff;border-bottom:1px solid #cdd8e5}h1{font-size:21px;margin:0 0 9px}'
             'p{margin:7px 0;color:#53677e}nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}'
             'button{border:1px solid #b5c6d8;border-radius:6px;background:#fff;padding:10px 16px;cursor:pointer;color:#18314d}'
             'button[aria-pressed="true"]{background:#183f72;color:#fff}iframe{border:0;width:100%;height:calc(100vh - 183px);min-height:550px}'
             '</style></head><body><header><h1>SMN first-pass review</h1>'
             f'<p>{html.escape(note)}</p><p>Preview only. Paid access is deferred; no access restriction is active.</p>'
             '<nav aria-label="Audience preview"><button data-page="visitor.html" aria-pressed="true">Visitor</button>'
             '<button data-page="registered.html" aria-pressed="false">Registered TradeWave user</button>'
             '<button data-page="article.html" aria-pressed="false">Full article</button></nav></header>'
             '<iframe title="Article audience preview" src="visitor.html" sandbox="allow-popups"></iframe>'
             '<script>document.querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>{'
             'document.querySelector("iframe").src=b.dataset.page;document.querySelectorAll("button").forEach(x=>'
             'x.setAttribute("aria-pressed",String(x===b)));}));</script></body></html>')
    (root / "index.html").write_text(index, encoding="utf-8")
    metadata = {"article_type": article_type, "status": result.get("status"),
                "access_enabled": False, "news_publish_enabled": False,
                "proposed_initial_access": "public" if article_type == "news" else "registration",
                "paid_access": "deferred", "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (root / "preview.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {name: str(root / name) for name in ("index.html", "article.html", "visitor.html", "registered.html", "preview.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create private SMN audience previews; never publishes.")
    parser.add_argument("--article", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--type", choices=("seasonal", "news"), default="seasonal")
    args = parser.parse_args(argv)
    result = {"html": Path(args.article).read_text(encoding="utf-8"), "status": "preview"}
    paths = write_article_preview(result, args.out, article_type=args.type)
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
