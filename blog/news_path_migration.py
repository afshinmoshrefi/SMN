#!/usr/bin/env python3
"""
news_path_migration.py
======================

PURPOSE
-------
This script migrates Seasonal Market News articles from an old path/URL structure
to a new path/URL structure. It handles:

    1. Moving all HTML article files from old location to new location
    2. Moving all image files (charts, hero images) from old location to new location
    3. Updating posts.json with new file paths and URLs
    4. Updating search_index.json with new URLs
    5. Updating recent_titles.json (move to new location if needed)
    6. Migrating datasets folder
    7. Fixing image URLs inside HTML article files
    8. Syncing Redis cache from updated posts.json
    9. Rebuilding index.html to point to new article locations
    10. Refreshing related_articles sections in all HTML files with correct URLs
    11. Cleanup of empty directories from old location

CONFIGURATION
-------------
OLD CONFIG: Auto-detected from posts.json (no manual editing needed!)
NEW CONFIG: Read from config.py

The script analyzes posts.json to determine the current path/URL structure,
then migrates to whatever is set in config.py.

File structure pattern:
    {news_root_folder}/[articles_subfolder/]{market_family}/YYYY/MM/DD/{files}

Example migration:
    OLD (auto-detected): /var/www/html/wordpress/news/US/2025/12/07/slug.html
    NEW (from config):   /var/www/smn/articles/US/2025/12/07/slug.html

USAGE
-----
1. Update config.py with the NEW/TARGET path settings:
   - news_root_folder
   - articles_subfolder  
   - news_website_url

2. Run with --dry-run first to preview changes:
       python3 news_path_migration.py --dry-run

3. Run with --full-run to execute the migration:
       python3 news_path_migration.py --full-run

MANUAL OVERRIDE (if auto-detection fails)
-----------------------------------------
    python3 news_path_migration.py --full-run \\
        --old-root '/var/www/html/wordpress/news/' \\
        --old-subfolder '' \\
        --old-url '192.168.1.151/news'

IMPORTANT NOTES
---------------
- Files are MOVED (not copied) - originals are deleted after successful transfer
- The script stops immediately on any error (fix and re-run)
- Always run --dry-run first to verify the migration plan
- Back up your data before running the actual migration
- Supporting files (posts.json, search_index.json, etc.) stay at news_root_folder level
- Script is idempotent - safe to re-run if interrupted
- Old config is AUTO-DETECTED from posts.json - no manual editing needed!

DEPENDENCIES
------------
- config.py must have the new path variables set
- rebuild_news_home.py must be available for index.html regeneration
- refresh_related_articles.py must be available for related articles refresh
- publish_article.py must be available for Redis sync
"""

import os
import re
import sys
import json
import shutil
import argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from urllib.parse import urlparse

sys.path.insert(0, "/home/flask")
import config


# =============================================================================
# AUTO-DETECTION OF CURRENT (OLD) CONFIG
# =============================================================================

def detect_current_config() -> Dict[str, str]:
    """
    Auto-detect the current configuration by analyzing posts.json.
    
    Returns dict with:
        - news_root_folder: detected disk path root
        - articles_subfolder: detected subfolder (or '')
        - news_website_url: detected URL base
    """
    # Try to find posts.json in likely CURRENT locations (not target)
    # Order matters: check common current locations before config location
    possible_locations = [
        Path('/var/www/html/wordpress/news/posts.json'),
        Path('/var/www/html/news/posts.json'),
        Path('/var/www/smn/posts.json'),
    ]
    
    # Also check config location as last resort
    config_location = Path(getattr(config, 'news_root_folder', '')) / 'posts.json'
    if config_location not in possible_locations:
        possible_locations.append(config_location)
    
    posts_path = None
    for loc in possible_locations:
        if loc.exists():
            posts_path = loc
            break
    
    if not posts_path:
        raise FileNotFoundError("Cannot find posts.json to auto-detect current config")
    
    with open(posts_path, 'r', encoding='utf-8') as f:
        posts = json.load(f)
    
    if not posts:
        raise ValueError("posts.json is empty, cannot detect config")
    
    # Sample multiple entries to ensure consistency
    sample_size = min(5, len(posts))
    samples = posts[:sample_size]
    
    detected = {
        'news_root_folder': None,
        'articles_subfolder': '',
        'news_website_url': None,
        'posts_json_location': str(posts_path),
    }
    
    # Known market families and known subfolders
    market_families = ['US', 'INDX', 'ETF', 'COMM', 'FOREX', 'GBOND', 'CC', 'LSE', 'TO', 'KO', 'KQ']
    known_subfolders = ['articles', 'content', 'posts']
    
    for post in samples:
        url = post.get('url', '')
        path = post.get('path', '')
        
        if url and not detected['news_website_url']:
            # Parse URL to extract base
            parsed = urlparse(url)
            url_path_parts = parsed.path.strip('/').split('/')
            
            # Find where market_family starts
            mf_index = None
            for i, part in enumerate(url_path_parts):
                if part in market_families:
                    mf_index = i
                    break
            
            if mf_index is not None:
                url_base_parts = url_path_parts[:mf_index]
                scheme = parsed.scheme or 'http'
                netloc = parsed.netloc
                
                # Check if there's a known subfolder
                if url_base_parts and url_base_parts[-1] in known_subfolders:
                    detected['articles_subfolder'] = url_base_parts[-1]
                    url_base_parts = url_base_parts[:-1]
                
                if url_base_parts:
                    base_path = '/'.join(url_base_parts)
                    detected['news_website_url'] = f"{netloc}/{base_path}"
                else:
                    detected['news_website_url'] = netloc
        
        if path and not detected['news_root_folder']:
            path_parts = path.strip('/').split('/')
            
            # Find where market_family starts
            mf_index = None
            for i, part in enumerate(path_parts):
                if part in market_families:
                    mf_index = i
                    break
            
            if mf_index is not None:
                root_parts = path_parts[:mf_index]
                
                # Check if last part is a known subfolder
                if root_parts and root_parts[-1] in known_subfolders:
                    if not detected['articles_subfolder']:
                        detected['articles_subfolder'] = root_parts[-1]
                    root_parts = root_parts[:-1]
                
                detected['news_root_folder'] = '/' + '/'.join(root_parts) + '/'
    
    # Validate we got everything
    if not detected['news_root_folder']:
        raise ValueError("Could not detect news_root_folder from posts.json")
    if not detected['news_website_url']:
        raise ValueError("Could not detect news_website_url from posts.json")
    
    return detected


def print_detected_config(detected: Dict[str, str], new_config: Dict[str, str]):
    """Print detected vs new config for user verification."""
    print("\n" + "=" * 70)
    print("CONFIGURATION DETECTION")
    print("=" * 70)
    
    print("\nDETECTED CURRENT STATE (from posts.json):")
    print(f"  posts.json location:  {detected['posts_json_location']}")
    print(f"  news_root_folder:     {detected['news_root_folder']}")
    print(f"  articles_subfolder:   '{detected['articles_subfolder']}' (empty = no subfolder)")
    print(f"  news_website_url:     {detected['news_website_url']}")
    
    print("\nTARGET STATE (from config.py):")
    print(f"  news_root_folder:     {new_config['news_root_folder']}")
    print(f"  articles_subfolder:   '{new_config['articles_subfolder']}' (empty = no subfolder)")
    print(f"  news_website_url:     {new_config['news_website_url']}")
    
    # Show what will change
    print("\nCHANGES TO BE MADE:")
    changes = []
    if detected['news_root_folder'] != new_config['news_root_folder']:
        changes.append(f"  • Root folder: {detected['news_root_folder']} → {new_config['news_root_folder']}")
    if detected['articles_subfolder'] != new_config['articles_subfolder']:
        changes.append(f"  • Subfolder: '{detected['articles_subfolder']}' → '{new_config['articles_subfolder']}'")
    if detected['news_website_url'] != new_config['news_website_url']:
        changes.append(f"  • URL base: {detected['news_website_url']} → {new_config['news_website_url']}")
    
    if changes:
        for change in changes:
            print(change)
    else:
        print("  (No changes detected - current state matches target)")
    
    print("=" * 70)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_base_url(news_website_url: str) -> str:
    """Ensure URL has http(s) prefix and no trailing slash."""
    base = news_website_url.strip().rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "http://" + base
    return base


def build_content_dir(news_root: Path, articles_subfolder: str, market_family: str, 
                      yyyy: str, mm: str, dd: str) -> Path:
    """Build the directory path for articles/images."""
    if articles_subfolder:
        return news_root / articles_subfolder / market_family / yyyy / mm / dd
    else:
        return news_root / market_family / yyyy / mm / dd


def build_url(news_website_url: str, articles_subfolder: str, market_family: str,
              yyyy: str, mm: str, dd: str, filename: str) -> str:
    """Build the full URL for an article or image."""
    base_url = get_base_url(news_website_url)
    if articles_subfolder:
        return f"{base_url}/{articles_subfolder}/{market_family}/{yyyy}/{mm}/{dd}/{filename}"
    else:
        return f"{base_url}/{market_family}/{yyyy}/{mm}/{dd}/{filename}"


def parse_path_components(file_path: str, news_root: str, articles_subfolder: str) -> Tuple[str, str, str, str, str]:
    """
    Parse a file path to extract: market_family, yyyy, mm, dd, filename
    
    Returns tuple: (market_family, yyyy, mm, dd, filename)
    """
    # Normalize paths
    file_path = str(Path(file_path).resolve())
    news_root = str(Path(news_root).resolve()).rstrip("/") + "/"
    
    if not file_path.startswith(news_root):
        raise ValueError(f"File path '{file_path}' is not under news_root '{news_root}'")
    
    # Get relative path from news_root
    rel_path = file_path[len(news_root):]
    
    # Strip articles_subfolder if present
    if articles_subfolder:
        prefix = articles_subfolder.strip("/") + "/"
        if rel_path.startswith(prefix):
            rel_path = rel_path[len(prefix):]
    
    # Parse: market_family/YYYY/MM/DD/filename
    parts = rel_path.split("/")
    if len(parts) < 5:
        raise ValueError(f"Cannot parse path components from '{rel_path}'")
    
    market_family = parts[0]
    yyyy = parts[1]
    mm = parts[2]
    dd = parts[3]
    filename = "/".join(parts[4:])  # In case there are subdirs
    
    return market_family, yyyy, mm, dd, filename


# =============================================================================
# DISCOVERY FUNCTIONS
# =============================================================================

def discover_content_files(news_root: Path, articles_subfolder: str) -> List[Path]:
    """
    Discover all HTML and image files in the content directory structure.
    
    Looks for files matching pattern:
        {news_root}/[articles_subfolder/]{market_family}/YYYY/MM/DD/*
    """
    files = []
    
    # Determine the starting directory
    if articles_subfolder:
        start_dir = news_root / articles_subfolder
    else:
        start_dir = news_root
    
    if not start_dir.exists():
        return files
    
    # Walk the directory structure
    for root, dirs, filenames in os.walk(start_dir):
        root_path = Path(root)
        
        # Skip the supporting files at the news_root level
        if root_path == news_root:
            # Exclude known non-content directories
            dirs[:] = [d for d in dirs if d not in ('datasets',)]
            continue
        
        for filename in filenames:
            # Include HTML and image files
            if filename.endswith(('.html', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.txt', '.json')):
                # Skip posts.json, search_index.json, recent_titles.json at root
                if root_path == news_root and filename in ('posts.json', 'search_index.json', 'recent_titles.json'):
                    continue
                files.append(root_path / filename)
    
    return files


# =============================================================================
# MIGRATION FUNCTIONS
# =============================================================================

def migrate_files(old_news_root: str, old_articles_subfolder: str,
                  new_news_root: str, new_articles_subfolder: str,
                  dry_run: bool = False) -> List[Tuple[Path, Path]]:
    """
    Move all content files from old location to new location.
    
    Returns list of (old_path, new_path) tuples for files that were moved.
    """
    old_root = Path(old_news_root).resolve()
    new_root = Path(new_news_root).resolve()
    
    # Discover files in old location
    files = discover_content_files(old_root, old_articles_subfolder)
    
    if not files:
        print("[INFO] No content files found to migrate")
        return []
    
    print(f"[INFO] Found {len(files)} files to migrate")
    
    moved_files = []
    deleted_legacy = []
    skipped_already_correct = []
    
    for old_path in files:
        try:
            # Parse path components
            market_family, yyyy, mm, dd, filename = parse_path_components(
                str(old_path), old_news_root, old_articles_subfolder
            )
            
            # Build new path
            new_dir = build_content_dir(new_root, new_articles_subfolder, market_family, yyyy, mm, dd)
            new_path = new_dir / filename
            
            # Skip if file is already in correct location
            if old_path.resolve() == new_path.resolve():
                skipped_already_correct.append(old_path)
                continue
            
            if dry_run:
                print(f"  [DRY-RUN] Would move:")
                print(f"    FROM: {old_path}")
                print(f"    TO:   {new_path}")
            else:
                # Create destination directory
                new_dir.mkdir(parents=True, exist_ok=True)
                
                # Move file
                shutil.move(str(old_path), str(new_path))
                print(f"  [MOVED] {old_path.name}")
                print(f"    -> {new_path}")
            
            moved_files.append((old_path, new_path))
            
        except ValueError as e:
            # File doesn't match expected pattern (legacy file) - delete it
            if dry_run:
                print(f"  [DRY-RUN] Would DELETE legacy file: {old_path}")
            else:
                old_path.unlink()
                print(f"  [DELETED] Legacy file: {old_path}")
            deleted_legacy.append(old_path)
            
        except Exception as e:
            print(f"[ERROR] Failed to migrate {old_path}: {e}")
            raise
    
    if deleted_legacy:
        print(f"[INFO] Legacy files deleted: {len(deleted_legacy)}")
    
    if skipped_already_correct:
        print(f"[INFO] Files already in correct location (skipped): {len(skipped_already_correct)}")
    
    return moved_files


def update_posts_json(old_news_root: str, old_articles_subfolder: str, old_news_website_url: str,
                      new_news_root: str, new_articles_subfolder: str, new_news_website_url: str,
                      dry_run: bool = False) -> int:
    """
    Update posts.json with new paths and URLs.
    
    Returns count of updated entries.
    """
    old_root = Path(old_news_root).resolve()
    new_root = Path(new_news_root).resolve()
    
    # posts.json location - check new location first, then old
    new_posts_path = new_root / "posts.json"
    old_posts_path = old_root / "posts.json"
    
    if new_posts_path.exists():
        posts_path = new_posts_path
    elif old_posts_path.exists():
        posts_path = old_posts_path
    else:
        print(f"[WARN] posts.json not found at {old_posts_path} or {new_posts_path}")
        return 0
    
    with open(posts_path, 'r', encoding='utf-8') as f:
        posts = json.load(f)
    
    print(f"[INFO] Updating {len(posts)} entries in posts.json")
    
    updated_count = 0
    old_base_url = get_base_url(old_news_website_url)
    
    for post in posts:
        old_path = post.get("path", "")
        old_url = post.get("url", "")
        
        if not old_path and not old_url:
            continue
        
        try:
            # Try to parse from path first, then URL
            if old_path:
                # Check if path is under OLD root or NEW root
                old_root_resolved = str(Path(old_news_root).resolve())
                new_root_resolved = str(Path(new_news_root).resolve())
                
                if old_path.startswith(new_root_resolved):
                    # Already migrated to new location
                    market_family, yyyy, mm, dd, filename = parse_path_components(
                        old_path, new_news_root, new_articles_subfolder
                    )
                elif old_path.startswith(old_root_resolved):
                    # Still at old location
                    market_family, yyyy, mm, dd, filename = parse_path_components(
                        old_path, old_news_root, old_articles_subfolder
                    )
                else:
                    # Try old root first (most common case during migration)
                    try:
                        market_family, yyyy, mm, dd, filename = parse_path_components(
                            old_path, old_news_root, old_articles_subfolder
                        )
                    except ValueError:
                        market_family, yyyy, mm, dd, filename = parse_path_components(
                            old_path, new_news_root, new_articles_subfolder
                        )
            else:
                # Parse from URL
                url_path = old_url.replace(old_base_url, "").lstrip("/")
                if old_articles_subfolder:
                    prefix = old_articles_subfolder.strip("/") + "/"
                    if url_path.startswith(prefix):
                        url_path = url_path[len(prefix):]
                parts = url_path.split("/")
                if len(parts) >= 5:
                    market_family, yyyy, mm, dd = parts[0], parts[1], parts[2], parts[3]
                    filename = "/".join(parts[4:])
                else:
                    continue
            
            # Build new path and URL
            new_dir = build_content_dir(new_root, new_articles_subfolder, market_family, yyyy, mm, dd)
            new_path = str(new_dir / filename)
            new_url = build_url(new_news_website_url, new_articles_subfolder, market_family, yyyy, mm, dd, filename)
            
            if dry_run:
                if old_path != new_path or old_url != new_url:
                    print(f"  [DRY-RUN] {post.get('symbol', 'UNKNOWN')}:")
                    if old_path != new_path:
                        print(f"    path: {old_path} -> {new_path}")
                    if old_url != new_url:
                        print(f"    url:  {old_url} -> {new_url}")
            
            post["path"] = new_path
            post["url"] = new_url
            updated_count += 1
            
        except Exception as e:
            print(f"[WARN] Could not update post '{post.get('title', 'UNKNOWN')}': {e}")
    
    if not dry_run:
        # Ensure new root exists
        new_root.mkdir(parents=True, exist_ok=True)
        
        # Write updated posts.json to new location
        with open(new_posts_path, 'w', encoding='utf-8') as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Wrote updated posts.json to {new_posts_path}")
        
        # Remove old posts.json if different location and exists
        if old_posts_path != new_posts_path and old_posts_path.exists():
            old_posts_path.unlink()
            print(f"[INFO] Removed old posts.json from {old_posts_path}")
    
    return updated_count


def update_search_index_json(old_news_root: str, old_articles_subfolder: str, old_news_website_url: str,
                             new_news_root: str, new_articles_subfolder: str, new_news_website_url: str,
                             dry_run: bool = False) -> int:
    """
    Update search_index.json with new URLs.
    
    Returns count of updated entries.
    """
    old_root = Path(old_news_root).resolve()
    new_root = Path(new_news_root).resolve()
    
    # Check new location first, then old
    new_index_path = new_root / "search_index.json"
    old_index_path = old_root / "search_index.json"
    
    if new_index_path.exists():
        index_path = new_index_path
    elif old_index_path.exists():
        index_path = old_index_path
    else:
        print(f"[WARN] search_index.json not found")
        return 0
    
    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    
    print(f"[INFO] Updating {len(index)} entries in search_index.json")
    
    updated_count = 0
    old_base_url = get_base_url(old_news_website_url)
    new_base_url = get_base_url(new_news_website_url)
    
    for entry in index:
        old_url = entry.get("url", "")
        
        if not old_url:
            continue
        
        try:
            # Parse URL to extract components
            # URL format: http://base/[subfolder/]market_family/YYYY/MM/DD/slug.html
            
            # Handle both old and new URL formats
            if old_url.startswith(new_base_url):
                url_path = old_url.replace(new_base_url, "").lstrip("/")
                current_subfolder = new_articles_subfolder
            else:
                url_path = old_url.replace(old_base_url, "").lstrip("/")
                current_subfolder = old_articles_subfolder
            
            # Strip subfolder if present
            if current_subfolder:
                prefix = current_subfolder.strip("/") + "/"
                if url_path.startswith(prefix):
                    url_path = url_path[len(prefix):]
            
            parts = url_path.split("/")
            if len(parts) >= 5:
                market_family = parts[0]
                yyyy = parts[1]
                mm = parts[2]
                dd = parts[3]
                filename = "/".join(parts[4:])
                
                new_url = build_url(new_news_website_url, new_articles_subfolder, 
                                   market_family, yyyy, mm, dd, filename)
                
                if dry_run and old_url != new_url:
                    print(f"  [DRY-RUN] URL: {old_url} -> {new_url}")
                
                entry["url"] = new_url
                updated_count += 1
                
        except Exception as e:
            print(f"[WARN] Could not update search index entry: {e}")
    
    if not dry_run:
        new_root.mkdir(parents=True, exist_ok=True)
        
        with open(new_index_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False)
        print(f"[INFO] Wrote updated search_index.json to {new_index_path}")
        
        if old_index_path != new_index_path and old_index_path.exists():
            old_index_path.unlink()
            print(f"[INFO] Removed old search_index.json from {old_index_path}")
    
    return updated_count


def migrate_recent_titles_json(old_news_root: str, new_news_root: str, dry_run: bool = False) -> bool:
    """
    Move recent_titles.json from old location to new location.
    No content changes needed - just file location.
    
    Returns True if file was migrated.
    """
    old_root = Path(old_news_root).resolve()
    new_root = Path(new_news_root).resolve()
    
    old_path = old_root / "recent_titles.json"
    new_path = new_root / "recent_titles.json"
    
    # Already in new location
    if new_path.exists():
        print("[INFO] recent_titles.json already in correct location")
        return True
    
    if not old_path.exists():
        print(f"[WARN] recent_titles.json not found at {old_path}")
        return False
    
    if old_path == new_path:
        print("[INFO] recent_titles.json already in correct location")
        return True
    
    if dry_run:
        print(f"  [DRY-RUN] Would move recent_titles.json:")
        print(f"    FROM: {old_path}")
        print(f"    TO:   {new_path}")
    else:
        new_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_path), str(new_path))
        print(f"[INFO] Moved recent_titles.json to {new_path}")
    
    return True


def migrate_datasets_folder(old_news_root: str, new_news_root: str, dry_run: bool = False) -> int:
    """
    Move datasets folder from old location to new location.
    
    Returns count of files moved.
    """
    old_root = Path(old_news_root).resolve()
    new_root = Path(new_news_root).resolve()
    
    old_datasets = old_root / "datasets"
    new_datasets = new_root / "datasets"
    
    # Same location - nothing to do
    if old_datasets == new_datasets:
        print("[INFO] datasets folder already in correct location")
        return 0
    
    if not old_datasets.exists():
        print(f"[INFO] No datasets folder found at {old_datasets}")
        return 0
    
    files = list(old_datasets.glob("*"))
    if not files:
        print(f"[INFO] datasets folder is empty at {old_datasets}")
        return 0
    
    print(f"[INFO] Found {len(files)} files in datasets folder")
    
    if dry_run:
        print(f"  [DRY-RUN] Would move datasets files:")
        print(f"    FROM: {old_datasets}")
        print(f"    TO:   {new_datasets}")
    else:
        new_datasets.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in files:
            dest = new_datasets / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                moved += 1
            else:
                # File already exists in target - remove source
                f.unlink()
        
        # Remove old datasets folder if empty
        if old_datasets.exists() and not any(old_datasets.iterdir()):
            old_datasets.rmdir()
        
        print(f"[INFO] Moved {moved} files to {new_datasets}")
    
    return len(files)


def fix_image_urls_in_articles(old_news_website_url: str, old_articles_subfolder: str,
                               new_news_root: str, new_articles_subfolder: str, 
                               new_news_website_url: str, dry_run: bool = False) -> int:
    """
    Fix image URLs inside HTML article files.
    
    When articles are moved, the embedded image URLs still point to old location.
    This function updates them to the new location.
    
    Also fixes inconsistent URLs where /articles/ subfolder might be missing.
    Handles both http and https variants.
    
    Returns count of files updated.
    """
    new_root = Path(new_news_root).resolve()
    
    # Normalize URLs - strip scheme for comparison, we'll handle both http/https
    def strip_scheme(url):
        return url.replace('https://', '').replace('http://', '').strip('/')
    
    old_base_stripped = strip_scheme(old_news_website_url)
    new_base_url = get_base_url(new_news_website_url)
    
    # Build the correct new URL prefix
    if new_articles_subfolder:
        new_url_prefix = f"{new_base_url}/{new_articles_subfolder.strip('/')}"
    else:
        new_url_prefix = new_base_url
    
    # Build list of OLD patterns to search for (multiple variations with http and https)
    old_patterns = []
    
    # Pattern with old subfolder
    if old_articles_subfolder:
        old_patterns.append(f"http://{old_base_stripped}/{old_articles_subfolder.strip('/')}")
        old_patterns.append(f"https://{old_base_stripped}/{old_articles_subfolder.strip('/')}")
    
    # Pattern without subfolder
    old_patterns.append(f"http://{old_base_stripped}")
    old_patterns.append(f"https://{old_base_stripped}")
    
    # Known market families
    market_families = ['US', 'INDX', 'ETF', 'COMM', 'FOREX', 'GBOND', 'CC', 'LSE', 'TO', 'KO', 'KQ']
    
    # Load posts.json to get list of articles
    posts_path = new_root / "posts.json"
    if not posts_path.exists():
        # Try common locations
        for try_path in [Path('/var/www/html/wordpress/news/posts.json'),
                         Path('/var/www/html/news/posts.json'),
                         Path('/var/www/smn/posts.json')]:
            if try_path.exists():
                posts_path = try_path
                break
    
    if not posts_path.exists():
        print(f"[WARN] posts.json not found")
        return 0
    
    with open(posts_path, 'r', encoding='utf-8') as f:
        posts = json.load(f)
    
    print(f"[INFO] Checking {len(posts)} articles for image URL updates")
    print(f"  Target prefix: {new_url_prefix}")
    print(f"  Looking for patterns: {old_patterns[:2]}...")  # Show first two
    
    updated_count = 0
    total_replacements = 0
    
    for post in posts:
        html_path = post.get("path")
        symbol = post.get("symbol", "UNKNOWN")
        
        if not html_path:
            continue
        
        html_path = Path(html_path)
        
        if not html_path.exists():
            continue
        
        try:
            html = html_path.read_text(encoding="utf-8")
            original_html = html
            file_replacements = 0
            
            # For each market family, fix URLs that don't have correct prefix
            for mf in market_families:
                # Correct pattern should be: {new_url_prefix}/{mf}/
                correct_pattern = f"{new_url_prefix}/{mf}/"
                
                # Check various incorrect patterns and fix them
                for old_pattern in old_patterns:
                    # Pattern: {old_base}/{mf}/
                    wrong_pattern = f"{old_pattern}/{mf}/"
                    if wrong_pattern != correct_pattern and wrong_pattern in html:
                        count = html.count(wrong_pattern)
                        html = html.replace(wrong_pattern, correct_pattern)
                        file_replacements += count
            
            if file_replacements > 0:
                if dry_run:
                    print(f"  [DRY-RUN] {symbol}: would update {file_replacements} URL(s)")
                else:
                    html_path.write_text(html, encoding="utf-8")
                    print(f"  [UPDATED] {symbol}: fixed {file_replacements} URL(s)")
                
                updated_count += 1
                total_replacements += file_replacements
                
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")
    
    print(f"[INFO] Image URLs: {updated_count} files updated, {total_replacements} total replacements")
    return updated_count


def sync_redis_from_posts(new_news_root: str, dry_run: bool = False) -> bool:
    """
    Sync Redis cache from posts.json.
    
    IMPORTANT: This FLUSHES Redis db first to remove stale entries,
    then rebuilds from posts.json.
    
    Returns True if successful.
    """
    if dry_run:
        print("  [DRY-RUN] Would flush Redis db and sync cache from posts.json")
        return True
    
    try:
        # First, flush Redis db to remove any stale entries
        import redis
        
        # Reload config to get fresh values
        import importlib
        import config as cfg
        importlib.reload(cfg)
        
        redis_db = getattr(cfg, 'articles_redis_db', 3)
        redis_host = getattr(cfg, 'webserver_ip', 'localhost')
        
        print(f"  [INFO] Flushing Redis db {redis_db} to remove stale entries...")
        r = redis.Redis(host=redis_host, port=6379, db=redis_db)
        keys_before = r.dbsize()
        r.flushdb()
        print(f"  [INFO] Flushed {keys_before} keys from Redis db {redis_db}")
        
        # Now sync from posts.json
        from publish_article import sync_all_articles_to_redis
        print("  [INFO] Syncing all articles to Redis from posts.json...")
        sync_all_articles_to_redis()
        
        keys_after = r.dbsize()
        print(f"  [INFO] Redis sync completed - now has {keys_after} entries")
        return True
        
    except ImportError as e:
        print(f"[WARN] Could not import sync function: {e}")
        print("[WARN] Redis sync skipped - run manually: ")
        print('  redis-cli -n 3 FLUSHDB')
        print('  python3 -c "from publish_article import sync_all_articles_to_redis; sync_all_articles_to_redis()"')
        return False
        
    except Exception as e:
        print(f"[ERROR] Failed to sync Redis: {e}")
        raise


def rebuild_index_html(dry_run: bool = False) -> bool:
    """
    Rebuild index.html using rebuild_news_home.py
    
    Returns True if successful.
    """
    if dry_run:
        print("  [DRY-RUN] Would rebuild index.html via rebuild_news_home.build_home()")
        return True
    
    try:
        from rebuild_news_home import build_home
        print("[INFO] Rebuilding index.html...")
        build_home()
        print("[INFO] index.html rebuilt successfully")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to rebuild index.html: {e}")
        raise


def refresh_all_related_articles(new_news_root: str, dry_run: bool = False) -> bool:
    """
    Refresh related articles in ALL HTML files.
    
    This iterates through all articles in posts.json and updates their
    related articles sections with correct URLs.
    
    Returns True if successful.
    """
    if dry_run:
        print("  [DRY-RUN] Would refresh related articles for all articles")
        return True
    
    try:
        from refresh_related_articles import _load_catalog, _update_article_html
        
        print("[INFO] Refreshing related articles in all HTML files...")
        
        # Load all articles
        all_articles = _load_catalog()
        volume_csv_dir = getattr(config, "volume_csv_dir", "/home/flask/blog/volume_lists")
        
        refreshed = 0
        failed = 0
        
        for article in all_articles:
            try:
                ok = _update_article_html(
                    article, 
                    all_articles, 
                    volume_csv_dir, 
                    dry_run=False
                )
                if ok:
                    refreshed += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"[WARN] Failed to refresh {article.get('symbol', 'UNKNOWN')}: {e}")
                failed += 1
        
        print(f"[INFO] Related articles refreshed: {refreshed} success, {failed} failed")
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to refresh related articles: {e}")
        raise


def cleanup_empty_directories(news_root: Path, old_articles_subfolder: str, new_articles_subfolder: str):
    """
    Remove empty directories left after migration.
    
    If old_articles_subfolder was empty (files were at root), we need to clean up
    the old market_family directories (US, INDX, ETF, COMM, etc.) at root level.
    """
    # Known market family directories that might need cleanup
    known_market_families = ['US', 'INDX', 'ETF', 'COMM', 'FOREX', 'GBOND', 'CC', 'LSE', 'TO', 'KO', 'KQ']
    
    # If old subfolder was empty but new is not, the market_family dirs are at root
    if not old_articles_subfolder and new_articles_subfolder:
        for mf in known_market_families:
            mf_dir = news_root / mf
            if mf_dir.exists() and mf_dir.is_dir():
                # Check if truly empty (no files anywhere)
                has_files = any(f.is_file() for f in mf_dir.rglob('*'))
                if not has_files:
                    try:
                        shutil.rmtree(str(mf_dir))
                        print(f"[CLEANUP] Removed old directory: {mf_dir}")
                    except Exception as e:
                        print(f"[WARN] Could not remove {mf_dir}: {e}")
        
        # Also clean up legacy year folders like "2025" at root
        for item in news_root.iterdir():
            if item.is_dir() and item.name.isdigit() and len(item.name) == 4:
                has_files = any(f.is_file() for f in item.rglob('*'))
                if not has_files:
                    try:
                        shutil.rmtree(str(item))
                        print(f"[CLEANUP] Removed old legacy directory: {item}")
                    except Exception as e:
                        print(f"[WARN] Could not remove {item}: {e}")
    else:
        # Standard cleanup - walk and remove empty dirs
        if old_articles_subfolder:
            start_dir = news_root / old_articles_subfolder
        else:
            start_dir = news_root
        
        if not start_dir.exists():
            return
        
        # Walk bottom-up to remove empty directories
        for root, dirs, files in os.walk(start_dir, topdown=False):
            root_path = Path(root)
            if root_path != news_root and not any(root_path.iterdir()):
                try:
                    root_path.rmdir()
                    print(f"[CLEANUP] Removed empty directory: {root_path}")
                except OSError:
                    pass
    
    # Clean up .lock files
    for lock_file in news_root.glob("*.lock"):
        try:
            lock_file.unlink()
            print(f"[CLEANUP] Removed lock file: {lock_file}")
        except Exception as e:
            print(f"[WARN] Could not remove lock file {lock_file}: {e}")


# =============================================================================
# MAIN MIGRATION ORCHESTRATOR
# =============================================================================

def run_migration(old_news_root: str, old_articles_subfolder: str, old_news_website_url: str,
                  dry_run: bool = False):
    """
    Run the complete migration process.
    """
    # Get new config values
    new_news_root = getattr(config, 'news_root_folder', '')
    new_articles_subfolder = getattr(config, 'articles_subfolder', '').strip().strip("/")
    new_news_website_url = getattr(config, 'news_website_url', '')
    
    if not new_news_root:
        print("[ERROR] config.news_root_folder is not set")
        sys.exit(1)
    
    if not new_news_website_url:
        print("[ERROR] config.news_website_url is not set")
        sys.exit(1)
    
    print("=" * 70)
    print("NEWS PATH MIGRATION")
    print("=" * 70)
    
    if dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")
    
    print("OLD CONFIGURATION:")
    print(f"  news_root_folder:   {old_news_root}")
    print(f"  articles_subfolder: '{old_articles_subfolder}' (empty = no subfolder)")
    print(f"  news_website_url:   {old_news_website_url}")
    
    print("\nNEW CONFIGURATION (from config.py):")
    print(f"  news_root_folder:   {new_news_root}")
    print(f"  articles_subfolder: '{new_articles_subfolder}' (empty = no subfolder)")
    print(f"  news_website_url:   {new_news_website_url}")
    
    print("\n" + "-" * 70)
    
    # Step 1: Migrate content files
    print("\n[STEP 1/11] Migrating content files (HTML, images)...")
    moved_files = migrate_files(old_news_root, old_articles_subfolder,
                                new_news_root, new_articles_subfolder, dry_run)
    print(f"  Total files moved: {len(moved_files)}")
    
    # Step 2: Update posts.json
    print("\n[STEP 2/11] Updating posts.json...")
    posts_updated = update_posts_json(old_news_root, old_articles_subfolder, old_news_website_url,
                                      new_news_root, new_articles_subfolder, new_news_website_url, dry_run)
    print(f"  Entries updated: {posts_updated}")
    
    # Step 3: Update search_index.json
    print("\n[STEP 3/11] Updating search_index.json...")
    index_updated = update_search_index_json(old_news_root, old_articles_subfolder, old_news_website_url,
                                             new_news_root, new_articles_subfolder, new_news_website_url, dry_run)
    print(f"  Entries updated: {index_updated}")
    
    # Step 4: Migrate recent_titles.json
    print("\n[STEP 4/11] Migrating recent_titles.json...")
    migrate_recent_titles_json(old_news_root, new_news_root, dry_run)
    
    # Step 5: Migrate datasets folder
    print("\n[STEP 5/11] Migrating datasets folder...")
    datasets_moved = migrate_datasets_folder(old_news_root, new_news_root, dry_run)
    print(f"  Files moved: {datasets_moved}")
    
    # Step 6: Fix image URLs in HTML files
    print("\n[STEP 6/11] Fixing image URLs in HTML articles...")
    fix_image_urls_in_articles(old_news_website_url, old_articles_subfolder,
                               new_news_root, new_articles_subfolder, 
                               new_news_website_url, dry_run)
    
    # Step 7: Sync Redis from posts.json
    print("\n[STEP 7/11] Syncing Redis cache...")
    sync_redis_from_posts(new_news_root, dry_run)
    
    # Step 8: Rebuild index.html
    print("\n[STEP 8/11] Rebuilding index.html...")
    rebuild_index_html(dry_run)
    
    # Step 9: Refresh related articles
    print("\n[STEP 9/11] Refreshing related articles...")
    refresh_all_related_articles(new_news_root, dry_run)
    
    # Step 10: Cleanup empty directories
    print("\n[STEP 10/11] Cleaning up empty directories...")
    if not dry_run:
        cleanup_empty_directories(Path(old_news_root).resolve(), old_articles_subfolder, new_articles_subfolder)
    else:
        print("  [DRY-RUN] Would clean up empty directories and lock files")
    
    print("\n[STEP 11/11] Setting ownership to www-data...")
    if not dry_run:
        os.system(f"chown -R www-data:www-data {new_news_root}")
    else:
        print(f"  [DRY-RUN] Would run: chown -R www-data:www-data {new_news_root}")
    
    print("\n" + "=" * 70)
    if dry_run:
        print("DRY RUN COMPLETE - No changes were made")
        print("Run with --full-run to execute the migration")
    else:
        print("MIGRATION COMPLETE")
    print("=" * 70)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(
        description="Migrate news articles from old path structure to new path structure"
    )
    
    # Create mutually exclusive group for run mode - one is REQUIRED
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without executing them'
    )
    mode_group.add_argument(
        '--full-run',
        action='store_true',
        help='Execute the full migration (moves files, updates JSON, syncs Redis)'
    )
    
    # Optional: manual override of old config (for edge cases)
    parser.add_argument(
        '--old-root',
        type=str,
        help='Override auto-detected old news_root_folder'
    )
    parser.add_argument(
        '--old-subfolder',
        type=str,
        help='Override auto-detected old articles_subfolder'
    )
    parser.add_argument(
        '--old-url',
        type=str,
        help='Override auto-detected old news_website_url'
    )
    parser.add_argument(
        '--skip-confirm',
        action='store_true',
        help='Skip confirmation prompt (for scripted use)'
    )
    
    args = parser.parse_args()
    
    # Get new config from config.py
    new_news_root = getattr(config, 'news_root_folder', '')
    new_articles_subfolder = getattr(config, 'articles_subfolder', '').strip().strip("/")
    new_news_website_url = getattr(config, 'news_website_url', '')
    
    if not new_news_root:
        print("[ERROR] config.news_root_folder is not set in config.py")
        sys.exit(1)
    
    if not new_news_website_url:
        print("[ERROR] config.news_website_url is not set in config.py")
        sys.exit(1)
    
    new_config = {
        'news_root_folder': new_news_root,
        'articles_subfolder': new_articles_subfolder,
        'news_website_url': new_news_website_url,
    }
    
    # Auto-detect current (old) config from posts.json
    try:
        detected = detect_current_config()
    except Exception as e:
        print(f"[ERROR] Could not auto-detect current config: {e}")
        print("\nYou can manually specify the old config with:")
        print("  --old-root '/var/www/html/wordpress/news/'")
        print("  --old-subfolder 'articles'")
        print("  --old-url '192.168.1.151/news'")
        sys.exit(1)
    
    # Apply any manual overrides
    old_news_root = args.old_root if args.old_root else detected['news_root_folder']
    old_articles_subfolder = (args.old_subfolder.strip().strip("/") if args.old_subfolder is not None 
                              else detected['articles_subfolder'])
    old_news_website_url = args.old_url if args.old_url else detected['news_website_url']
    
    # Update detected dict with any overrides for display
    detected_display = detected.copy()
    if args.old_root:
        detected_display['news_root_folder'] = f"{args.old_root} (manual override)"
    if args.old_subfolder is not None:
        detected_display['articles_subfolder'] = f"{args.old_subfolder} (manual override)"
    if args.old_url:
        detected_display['news_website_url'] = f"{args.old_url} (manual override)"
    
    # Show detected vs new config
    print_detected_config(detected_display, new_config)
    
    # Confirmation prompt (unless skipped)
    if not args.skip_confirm and not args.dry_run:
        response = input("\nProceed with migration? [y/N]: ").strip().lower()
        if response != 'y':
            print("Migration cancelled.")
            sys.exit(0)
    
    # Determine dry_run mode
    dry_run = args.dry_run
    
    run_migration(
        old_news_root=old_news_root,
        old_articles_subfolder=old_articles_subfolder,
        old_news_website_url=old_news_website_url,
        dry_run=dry_run
    )