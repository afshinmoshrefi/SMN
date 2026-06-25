
"""
rebuild_search_index.py
=======================

PURPOSE
-------
Rebuilds search_index.json from posts.json.

posts.json is the single source of truth for all articles. search_index.json
is a derived file used by the front-end search feature. This script regenerates
search_index.json when it becomes corrupted or out of sync.

WHEN TO USE
-----------
- After a failed or partial migration that corrupted search_index.json
- If search results show wrong URLs or missing articles
- If search_index.json was accidentally deleted or modified
- As part of manual recovery/repair procedures

USAGE
-----
    python3 rebuild_search_index.py

NOTES
-----
- Reads from: {news_root_folder}/posts.json
- Writes to:  {news_root_folder}/search_index.json
- Safe to run anytime - won't affect posts.json or actual article files
- Uses news_root_folder from config.py to locate files
"""
import json
import sys
sys.path.insert(0, '/home/flask')
import config


posts_path = f"{config.news_root_folder}/posts.json"
search_index_path = f"{config.news_root_folder}/search_index.json"

# Load posts.json (should be correct)
with open(posts_path) as f:
    posts = json.load(f)

# Build search index from posts
search_index = []
for p in posts:
    search_index.append({
        'title': p.get('title', ''),
        'url': p.get('url', ''),
        'symbol': p.get('symbol', ''),
        'dek': p.get('dek', ''),
        'published_date': p.get('published_date', '')
    })

# Write search_index.json
with open(search_index_path, 'w') as f:
    json.dump(search_index, f)

print(f'Rebuilt search_index.json with {len(search_index)} entries')
