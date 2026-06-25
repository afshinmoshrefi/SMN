"""
inject_ga.py
============
Inject Google Analytics GA4 snippet into all existing SMN article HTML files
and the home page. Skips files that already have the snippet.

Usage:
    python inject_ga.py              # dry run (shows what would change)
    python inject_ga.py --apply      # actually modify files
"""

import os, sys, glob

sys.path.insert(0, '/home/flask')
import config

MEASUREMENT_ID = getattr(config, 'ga_measurement_id', '')
ARTICLES_DIR = os.path.join(config.news_root_folder, config.articles_subfolder)
HOME_PAGE = os.path.join(config.news_root_folder, 'index.html')

GA_SNIPPET = f"""
    <!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={MEASUREMENT_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{ dataLayer.push(arguments); }}
      gtag('js', new Date());
      gtag('config', '{MEASUREMENT_ID}');
    </script>"""


def inject_file(filepath, dry_run=True):
    """Inject GA snippet before </head>. Returns True if file was modified."""
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()

    if 'googletagmanager.com/gtag' in html:
        return False

    head_close = html.lower().find('</head>')
    if head_close == -1:
        print(f"  SKIP (no </head>): {filepath}")
        return False

    new_html = html[:head_close] + GA_SNIPPET + "\n" + html[head_close:]

    if dry_run:
        print(f"  WOULD inject: {filepath}")
    else:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_html)
        print(f"  INJECTED: {filepath}")
    return True


def main():
    dry_run = '--apply' not in sys.argv

    if not MEASUREMENT_ID:
        print("ERROR: ga_measurement_id is empty in /home/flask/config.py")
        print("Set it to your GA4 measurement ID (e.g. 'G-XXXXXXXXXX') first.")
        sys.exit(1)

    if dry_run:
        print("=== DRY RUN (pass --apply to modify files) ===\n")

    count = 0

    # Home page
    if os.path.isfile(HOME_PAGE):
        if inject_file(HOME_PAGE, dry_run):
            count += 1

    # All article HTML files
    pattern = os.path.join(ARTICLES_DIR, '**', '*.html')
    for filepath in sorted(glob.glob(pattern, recursive=True)):
        if inject_file(filepath, dry_run):
            count += 1

    action = "Would inject" if dry_run else "Injected"
    print(f"\n{action} GA snippet into {count} file(s).")
    if dry_run and count > 0:
        print("Run with --apply to make changes.")


if __name__ == '__main__':
    main()
