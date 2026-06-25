#!/home/flask/venv/bin/python
"""
link_audit.py — offline dead-link scanner for published SMN HTML.

The content audit found ~4.8% of links dead at 6 months and growing. Run this as
an offline audit (e.g. weekly) over the published site to catch link rot; it does
NOT touch generation. Complements citation_gate.py (which blocks bad links at
publish time) by catching links that DIE later.

Usage:
    python link_audit.py /var/www/smn/articles                # scan a tree
    python link_audit.py path/to/article.html                 # scan one file
    python link_audit.py /var/www/smn/articles --sample 50    # random-ish subset
    python link_audit.py <path> --timeout 8 --out /root/link_audit_report.json
"""
import sys, os, re, json, argparse, glob

_HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def collect_links(path):
    """{url: [files...]} for external http(s) links across the given file/dir."""
    files = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "**", "*.html"), recursive=True))
    elif os.path.isfile(path):
        files = [path]
    links = {}
    for f in files:
        try:
            html = open(f, encoding="utf-8").read()
        except Exception:
            continue
        for href in _HREF_RE.findall(html):
            u = href.strip()
            if u.lower().startswith(("http://", "https://")):
                links.setdefault(u, set()).add(f)
    return files, links


def check(url, timeout):
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (SMN link-audit)"}
    try:
        r = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
        if r.status_code >= 400 or r.status_code == 405:
            r = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)
        return r.status_code
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--sample", type=int, default=0, help="check only the first N unique URLs")
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    files, links = collect_links(args.path)
    urls = list(links.keys())
    if args.sample and args.sample < len(urls):
        urls = urls[: args.sample]
    print(f"[link_audit] {len(files)} file(s), {len(links)} unique external URL(s); checking {len(urls)}")

    dead = []
    for i, u in enumerate(urls, 1):
        status = check(u, args.timeout)
        bad = not (isinstance(status, int) and status < 400)
        if bad:
            dead.append({"url": u, "status": status, "files": sorted(links[u])[:5], "count": len(links[u])})
        if i % 50 == 0:
            print(f"  ...{i}/{len(urls)} checked, {len(dead)} dead so far")

    rate = (len(dead) / len(urls) * 100) if urls else 0.0
    print(f"[link_audit] DONE: {len(dead)} dead of {len(urls)} checked ({rate:.1f}%)")
    for d in dead[:40]:
        print(f"  DEAD [{d['status']}] {d['url']}  (in {d['count']} file(s))")
    if args.out:
        json.dump({"checked": len(urls), "dead": dead, "rate_pct": rate}, open(args.out, "w"), indent=2)
        print(f"[link_audit] report -> {args.out}")


if __name__ == "__main__":
    main()
