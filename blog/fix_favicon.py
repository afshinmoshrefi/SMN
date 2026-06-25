#!/usr/bin/env python3
"""
fix_favicon.py - Fix/inject favicon in all news articles.
Usage: python3 fix_favicon.py [--dry-run]
"""
import sys, re
from pathlib import Path
sys.path.insert(0, "/home/flask")
import config

CORRECT_FAVICON = f'<link rel="icon" type="image/png" href="{config.smn_favicon}">'

def fix_favicon(dry_run=False):
    articles_dir = Path(config.news_root_folder) / config.articles_subfolder
    html_files = list(articles_dir.rglob("*.html"))
    
    updated = 0
    for f in html_files:
        html = f.read_text(encoding="utf-8")
        
        # Remove any existing favicon links
        new_html = re.sub(r'<link[^>]*rel=["\']icon["\'][^>]*>\n?\s*', '', html)
        
        # Inject correct favicon before </head>
        if '</head>' in new_html:
            new_html = new_html.replace('</head>', f'  {CORRECT_FAVICON}\n</head>')
        
        if new_html != html:
            if dry_run:
                print(f"[DRY-RUN] Would fix: {f.name}")
            else:
                f.write_text(new_html, encoding="utf-8")
                print(f"[FIXED] {f.name}")
            updated += 1
    
    print(f"\nTotal: {updated} files {'would be ' if dry_run else ''}updated")

if __name__ == "__main__":
    fix_favicon(dry_run="--dry-run" in sys.argv)