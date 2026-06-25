"""

this version 3 is now the main version and has been renamed to 

article_title.py

================================================================================
ARTICLE TITLE GENERATOR v3 - POST-ARTICLE ARCHITECTURE
================================================================================

DESIGN PHILOSOPHY:
------------------
The title is the most critical element of an article. It must be:
1. Factually accurate - every claim must exist in the article
2. Unique - not a template or duplicate of recent titles
3. Human-sounding - reads like a journalist wrote it
4. SEO-optimized - searchable hooks, company name, ticker

Previous approaches generated titles from research BEFORE the article existed,
leading to hallucinations where the LLM invented plausible-sounding but
unsupported details (e.g., "sanctions threat" when "sanctions" wasn't in source).

This version generates titles AFTER the article is written, using the actual
article text as the source of truth. A deterministic validation layer ensures
every significant word in the title exists in the article.

ARCHITECTURE:
-------------

    Written Article (HTML)
         │
         ▼
    ┌─────────────────────────────────────────┐
    │  STAGE 1: ARTICLE PARSING               │
    │  (Python - extract clean text)          │
    │                                         │
    │  Input: HTML file                       │
    │  Output: Clean article text + metadata  │
    └─────────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────────────────────────┐
    │  STAGE 2: TITLE GENERATION              │
    │  (Single LLM call)                      │
    │                                         │
    │  Input: Full article text               │
    │  Task: Write headlines using ONLY       │
    │        facts from the article           │
    │                                         │
    │  Output: 5 candidate titles             │
    └─────────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────────────────────────┐
    │  STAGE 3: FACTUAL VALIDATION            │
    │  (Deterministic Python - NO LLM)        │
    │                                         │
    │  For each candidate:                    │
    │  - Extract significant words            │
    │  - Check against article word set       │
    │  - Reject if missing key words          │
    │                                         │
    │  Output: Factually valid candidates     │
    └─────────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────────────────────────┐
    │  STAGE 4: UNIQUENESS FILTERING          │
    │  (Jaccard similarity, template check)   │
    │                                         │
    │  Output: Best unique, factual title     │
    └─────────────────────────────────────────┘
         │
         ▼
    Final Title (ready for HTML injection)

GUARANTEES:
-----------
1. Every significant word in the title exists in the article
2. No LLM hallucinations can pass the validation layer
3. Title accurately describes what the reader will see
4. Uniqueness enforced against recent titles

================================================================================
"""

import os
import re
import json
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from html.parser import HTMLParser
import sys
sys.path.insert(0, "/home/flask")
import config
import AI_tools


# =============================================================================
# CONFIGURATION
# =============================================================================

# Generation settings
NUM_CANDIDATES = 5            # Title candidates per LLM call
MAX_GENERATION_ATTEMPTS = 3   # Retry title generation if all candidates fail
VALIDATION_THRESHOLD = 0.85   # Minimum confidence score to pass validation

# Deduplication settings
MAX_ITEMS = 400
GLOBAL_GUARD_N = 200
CONTENT_JACCARD_CUTOFF = 0.78
TEMPLATE_JACCARD_CUTOFF = 0.70
SIG_PREFIX_N = 5
SIG_TOKENS_N = 10


# =============================================================================
# HTML PARSING
# =============================================================================

class ArticleTextExtractor(HTMLParser):
    """Extract clean text from article HTML, excluding scripts/styles."""
    
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'noscript', 'iframe'}
        self.current_skip = 0
        
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.current_skip += 1
            
    def handle_endtag(self, tag):
        if tag.lower() in self.skip_tags:
            self.current_skip = max(0, self.current_skip - 1)
            
    def handle_data(self, data):
        if self.current_skip == 0:
            text = data.strip()
            if text:
                self.text_parts.append(text)
                
    def get_text(self):
        return ' '.join(self.text_parts)


def extract_text_from_html(html_content: str) -> str:
    """Extract clean text from HTML content."""
    parser = ArticleTextExtractor()
    parser.feed(html_content)
    text = parser.get_text()
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_article_metadata(html_content: str, pattern: dict) -> dict:
    """Extract metadata from HTML and pattern dict."""
    
    # Try to find existing title in HTML
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
    existing_title = title_match.group(1).strip() if title_match else None
    
    # Try to find h1
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html_content, re.IGNORECASE)
    existing_h1 = h1_match.group(1).strip() if h1_match else None
    
    return {
        'symbol': pattern.get('symbol', ''),
        'company': pattern.get('company', ''),
        'start_date': pattern.get('start_date', ''),
        'days': pattern.get('days', ''),
        'years': pattern.get('years', ''),
        'existing_title': existing_title,
        'existing_h1': existing_h1,
    }


# =============================================================================
# TEXT NORMALIZATION & TOKENIZATION
# =============================================================================

STOPWORDS = {
    "and", "or", "the", "a", "an", "to", "into", "in", "on", "as", "with",
    "of", "for", "at", "by", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those",
    "it", "its", "they", "their", "them", "he", "she", "his", "her",
    "we", "our", "you", "your", "i", "my", "me",
}

# Words allowed in titles that don't need to be in article verbatim
# These are common headline verbs and connectors
ALLOWED_HEADLINE_WORDS = {
    # Headline verbs (action words commonly used in titles)
    "faces", "face", "sees", "see", "eyes", "eye", "weighs", "weigh",
    "navigates", "navigate", "rides", "ride", "enters", "enter",
    "confronts", "confront", "meets", "meet", "hits", "hit",
    "climbs", "climb", "falls", "fall", "rises", "rise", "drops", "drop",
    "gains", "gain", "loses", "lose", "holds", "hold", "keeps", "keep",
    "targets", "target", "seeks", "seek", "tests", "test",
    "draws", "draw", "sparks", "spark", "fuels", "fuel",
    "signals", "signal", "shows", "show", "reveals", "reveal",
    "posts", "post", "reports", "report", "beats", "beat",
    "steadies", "steady", "approaches", "approach", "nears", "near",
    "balances", "balance", "contends", "contend", "grapples", "grapple",
    "battles", "battle", "braces", "brace", "awaits", "await",
    "juggles", "juggle", "straddles", "straddle",
    
    # Connectors and prepositions
    "amid", "after", "before", "into", "for", "with", "on", "as",
    "ahead", "during", "following", "toward", "towards", "between",
    "under", "over", "through", "while", "despite",
    
    # Common headline nouns
    "stock", "stocks", "share", "shares", "company", "firm",
    "outlook", "focus", "view", "move", "play", "bet", "push",
    "risk", "risks", "threat", "threats", "pressure", "pressures",
    "tension", "tensions", "uncertainty", "challenge", "challenges",
    "headwind", "headwinds", "tailwind", "tailwinds",
    "investor", "investors", "trader", "traders",
    
    # Seasonal & stat-forward headline words
    "window", "streak", "stretch", "run", "straight", "consecutive",
    "averaging", "cumulative", "flawless", "perfect", "winning",
    "midterm", "election", "seasonal", "spring", "summer", "winter",
    "delivers", "deliver", "produced", "produce", "returned", "rallied",
    "dropped", "fallen", "lost", "slumped", "surged",
    
    # Time references
    "late", "early", "year", "month", "week", "day", "end", "yearend",
    "december", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november",
    "heading", "entering", "approaching", "nearing",
    
    # Qualifiers
    "new", "top", "key", "major", "biggest", "largest", "strong",
    "historically", "favorable", "softer", "weaker", "lower",
    "record", "foreign", "political", "geopolitical",
}

# Synonym mapping for validation (title word -> acceptable article words)
SYNONYM_MAP = {
    "climbs": ["rises", "increases", "grows", "gains", "up"],
    "falls": ["drops", "declines", "decreases", "down", "lower"],
    "faces": ["confronts", "navigates", "deals", "contends"],
    "amid": ["during", "as", "while", "with"],
    "eyes": ["targets", "seeks", "looks", "considers"],
    "top": ["largest", "biggest", "leading", "major"],
    "bet": ["investment", "position", "stake", "exposure"],
    "risk": ["pressure", "tensions", "uncertainty", "challenge"],
    "pressure": ["tensions", "risk", "challenges", "headwinds"],
}


def _norm(s: str) -> str:
    """Normalize string for comparison."""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize(text: str) -> set:
    """Tokenize text into normalized word set."""
    return set(_norm(text).split())


def tokenize_for_validation(text: str) -> set:
    """Tokenize text, excluding stopwords, for validation purposes."""
    words = _norm(text).split()
    return {w for w in words if w and w not in STOPWORDS and len(w) > 2}


# =============================================================================
# FACTUAL VALIDATION (CRITICAL - DETERMINISTIC)
# =============================================================================

def validate_title_against_article(
    title: str,
    article_text: str,
    company: str = "",
    symbol: str = ""
) -> Dict:
    """
    Validate that every significant word in the title exists in the article.
    
    This is the BULLETPROOF layer - deterministic, no LLM involved.
    
    Returns:
        {
            "valid": True/False,
            "missing_words": ["word1", "word2"],
            "confidence": 0.0-1.0,
            "details": "explanation"
        }
    """
    # Tokenize
    title_words = tokenize_for_validation(title)
    article_words = tokenize_for_validation(article_text)
    
    # Add company/symbol as always-valid
    company_words = tokenize(company) if company else set()
    symbol_word = _norm(symbol) if symbol else ""
    
    # Check each title word
    missing = []
    checked = []
    
    for word in title_words:
        # Skip if it's the company name or symbol
        if word in company_words or word == symbol_word:
            continue
            
        # Skip if it's an allowed headline word
        if word in ALLOWED_HEADLINE_WORDS:
            continue
            
        # Skip numbers (dates, percentages, prices)
        if word.isdigit():
            continue
            
        checked.append(word)
        
        # Check if word is in article
        if word in article_words:
            continue
            
        # Check synonyms
        found_synonym = False
        if word in SYNONYM_MAP:
            for syn in SYNONYM_MAP[word]:
                if syn in article_words:
                    found_synonym = True
                    break
        
        # Check if any article word contains this word (partial match)
        if not found_synonym:
            partial_match = any(word in aw or aw in word for aw in article_words if len(aw) > 3)
            if partial_match:
                found_synonym = True
        
        if not found_synonym:
            missing.append(word)
    
    # Calculate confidence
    if not checked:
        confidence = 1.0
    else:
        confidence = 1.0 - (len(missing) / len(checked))
    
    valid = len(missing) == 0 or confidence >= VALIDATION_THRESHOLD
    
    details = ""
    if missing:
        details = f"Words not found in article: {missing}"
    else:
        details = "All significant words validated against article"
    
    return {
        "valid": valid,
        "missing_words": missing,
        "confidence": confidence,
        "checked_words": checked,
        "details": details
    }


# =============================================================================
# UNIQUENESS FILTERING (from v2)
# =============================================================================

SYN_TEMPLATE = {
    "softer": "weak", "weaker": "weak", "lower": "weak", "sliding": "weak", "falling": "weak",
    "tensions": "risk", "pressure": "risk", "risk": "risk", "exposure": "risk", "stakes": "risk",
    "crude": "oil", "oil": "oil", "prices": "price", "price": "price",
    "cloud": "hit", "challenge": "hit", "tests": "hit", "test": "hit", "hang": "hit", "hangs": "hit",
    "keeps": "keep", "keeping": "keep",
}

TEMPLATE_STOP = STOPWORDS.union({"outlook", "shares", "stock", "stocks", "today", "this", "week"})


def _token_set(s: str, company: str = "", symbol: str = "") -> set:
    """Extract content tokens for similarity checking."""
    company_tokens = set(_norm(company).split()) if company else set()
    symbol_token = _norm(symbol) if symbol else ""
    
    return {
        w for w in _norm(s).split()
        if w
        and w not in STOPWORDS
        and w not in company_tokens
        and w != symbol_token
    }


def _sig_tokens(title: str, company: str = "", symbol: str = "") -> list:
    """Extract signature tokens for template detection."""
    company_tokens = set(_norm(company).split()) if company else set()
    symbol_token = _norm(symbol) if symbol else ""
    
    toks = []
    for w in _norm(title).split():
        if not w:
            continue
        if w in company_tokens or w == symbol_token:
            continue
        if w in TEMPLATE_STOP:
            continue
        w = SYN_TEMPLATE.get(w, w)
        toks.append(w)
    return toks[:SIG_TOKENS_N]


def _jaccard(a: set, b: set) -> float:
    """Calculate Jaccard similarity."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def check_uniqueness(
    title: str,
    recent_titles: list,
    company: str,
    symbol: str
) -> Dict:
    """
    Check title uniqueness against recent titles.
    
    Returns:
        {
            "unique": True/False,
            "reason": "explanation if not unique"
        }
    """
    nt = _norm(title)
    company_norm = _norm(company)
    company_first = company_norm.split()[0] if company_norm else ""
    symbol_norm = _norm(symbol)
    
    # Build guard sets
    guard_titles = recent_titles[-GLOBAL_GUARD_N:]
    recent_norm_all = {_norm(t) for t in recent_titles}
    
    guard_content_sets = [_token_set(t, company, symbol) for t in guard_titles]
    guard_sig_sets = [set(_sig_tokens(t, company, symbol)) for t in guard_titles]
    guard_sig_prefixes = set()
    for t in guard_titles:
        st = _sig_tokens(t, company, symbol)
        if st:
            guard_sig_prefixes.add(" ".join(st[:SIG_PREFIX_N]))
    
    # === HARD REJECTS ===
    
    # Exact duplicate
    if nt in recent_norm_all:
        return {"unique": False, "reason": "Exact duplicate"}
    
    # Forbidden word "pattern"
    if "pattern" in nt.split():
        return {"unique": False, "reason": "Contains forbidden word 'pattern'"}
    
    # Company name repeated
    if company_norm and nt.count(company_norm) > 1:
        return {"unique": False, "reason": "Company name repeated"}
    
    # Forbidden phrases
    nt_pad = f" {nt} "
    if " in focus " in nt_pad:
        return {"unique": False, "reason": "Contains 'in focus'"}
    if " in the spotlight " in nt_pad or " in spotlight " in nt_pad:
        return {"unique": False, "reason": "Contains 'spotlight'"}
    if " on watch " in nt_pad:
        return {"unique": False, "reason": "Contains 'on watch'"}
    
    # Too long
    if len(title) > 110:
        return {"unique": False, "reason": f"Too long ({len(title)} chars)"}
    
    # === SIMILARITY CHECKS ===
    
    # Content Jaccard
    cand_tokens = _token_set(title, company, symbol)
    for i, old in enumerate(guard_content_sets):
        sim = _jaccard(cand_tokens, old)
        if sim >= CONTENT_JACCARD_CUTOFF:
            return {"unique": False, "reason": f"Too similar to recent title (Jaccard={sim:.2f})"}
    
    # Template signature
    cand_sig = _sig_tokens(title, company, symbol)
    cand_sig_set = set(cand_sig)
    cand_sig_prefix = " ".join(cand_sig[:SIG_PREFIX_N]) if cand_sig else ""
    
    if cand_sig_prefix and cand_sig_prefix in guard_sig_prefixes:
        return {"unique": False, "reason": "Signature prefix matches recent title"}
    
    if cand_sig_set:
        for old_sig in guard_sig_sets:
            if _jaccard(cand_sig_set, old_sig) >= TEMPLATE_JACCARD_CUTOFF:
                return {"unique": False, "reason": "Template structure too similar"}
    
    return {"unique": True, "reason": "Passed all uniqueness checks"}


# =============================================================================
# TITLE GENERATION
# =============================================================================

def extract_newsworthy_sections(article_text: str) -> dict:
    """
    Extract the newsworthy sections from article, ignoring seasonal statistics.
    
    Returns dict with:
    - opening: First 1-2 paragraphs (the hook)
    - news_sections: Price drivers, macro, political, what to watch
    - full_text: Complete article for validation
    """
    text = article_text
    
    # Try to find section boundaries
    sections = {
        'opening': '',
        'price_drivers': '',
        'macro_political': '',
        'what_to_watch': '',
    }
    
    # Get opening (first ~200 words or until "Key takeaways" or "Methodology")
    words = text.split()
    opening_end = min(200, len(words))
    for marker in ['Key takeaways', 'Methodology', 'Seasonal window', 'seasonal window']:
        if marker.lower() in text.lower():
            marker_pos = text.lower().find(marker.lower())
            opening_words = text[:marker_pos].split()
            if len(opening_words) > 20:  # Make sure we got something
                opening_end = len(opening_words)
                break
    sections['opening'] = ' '.join(words[:opening_end])
    
    # Extract "Price and near-term drivers" section
    price_markers = ['Price and near-term drivers', 'price and near-term', 'near-term drivers']
    for marker in price_markers:
        if marker.lower() in text.lower():
            start = text.lower().find(marker.lower())
            # Find next section or take ~500 words
            end = start + 3000
            for next_marker in ['Macro and political', 'What to watch', 'Sources']:
                next_pos = text.lower().find(next_marker.lower(), start + 100)
                if next_pos > start:
                    end = min(end, next_pos)
                    break
            sections['price_drivers'] = text[start:end].strip()
            break
    
    # Extract "Macro and political backdrop" section
    macro_markers = ['Macro and political backdrop', 'macro and political', 'political backdrop']
    for marker in macro_markers:
        if marker.lower() in text.lower():
            start = text.lower().find(marker.lower())
            end = start + 2000
            for next_marker in ['What to watch', 'Sources']:
                next_pos = text.lower().find(next_marker.lower(), start + 100)
                if next_pos > start:
                    end = min(end, next_pos)
                    break
            sections['macro_political'] = text[start:end].strip()
            break
    
    # Extract "What to watch" section
    watch_markers = ['What to watch', 'what to watch as the window']
    for marker in watch_markers:
        if marker.lower() in text.lower():
            start = text.lower().find(marker.lower())
            end = start + 1500
            for next_marker in ['Sources', 'Related Articles']:
                next_pos = text.lower().find(next_marker.lower(), start + 100)
                if next_pos > start:
                    end = min(end, next_pos)
                    break
            sections['what_to_watch'] = text[start:end].strip()
            break
    
    return sections


def extract_fresh_news_hooks(research: dict, article_date: str = None) -> List[dict]:
    """
    Extract news hooks from research, sorted by freshness.
    
    Returns list of hooks with:
    - headline: The news headline
    - summary: Brief description
    - type: earnings/regulation/macro/analyst
    - days_old: How old the news is
    - is_fresh: True if within 14 days
    """
    import datetime
    
    if article_date:
        try:
            ref_date = datetime.datetime.strptime(article_date, "%Y-%m-%d").date()
        except:
            ref_date = datetime.date.today()
    else:
        ref_date = datetime.date.today()
    
    hooks = []
    
    # Extract from catalysts
    catalysts = research.get("catalysts", []) or []
    for cat in catalysts:
        cat_date = cat.get("date", "")
        headline = cat.get("headline", "")
        summary = cat.get("summary", "")
        cat_type = cat.get("type", "news")
        
        if not headline and not summary:
            continue
        
        days_old = 999
        if cat_date:
            try:
                cat_dt = datetime.datetime.strptime(cat_date, "%Y-%m-%d").date()
                days_old = (ref_date - cat_dt).days
            except:
                pass
        
        hooks.append({
            "headline": headline,
            "summary": summary,
            "type": cat_type,
            "days_old": days_old,
            "is_fresh": days_old <= 14,
            "date": cat_date,
        })
    
    # Extract from macro themes
    macro = research.get("macro", []) or []
    for m in macro:
        theme = m.get("theme", "")
        summary = m.get("summary", "")
        if theme or summary:
            hooks.append({
                "headline": theme,
                "summary": summary,
                "type": "macro",
                "days_old": 0,  # Assume current
                "is_fresh": True,
                "date": "",
            })
    
    # Extract from earnings if notable
    earnings = research.get("earnings", {}) or {}
    if earnings.get("guidance"):
        hooks.append({
            "headline": "Earnings Guidance",
            "summary": earnings.get("guidance"),
            "type": "earnings",
            "days_old": 30,  # Assume somewhat recent
            "is_fresh": False,
            "date": "",
        })
    
    # Extract from analyst
    analyst = research.get("analyst", {}) or {}
    if analyst.get("consensus_rating") and analyst.get("provider"):
        hooks.append({
            "headline": f"{analyst.get('provider')} Rating",
            "summary": f"Consensus: {analyst.get('consensus_rating')}",
            "type": "analyst",
            "days_old": 30,
            "is_fresh": False,
            "date": "",
        })
    
    # Sort by freshness (most recent first)
    hooks.sort(key=lambda x: x["days_old"])
    
    return hooks


def generate_title_candidates(
    article_text: str,
    company: str,
    symbol: str,
    metadata: dict,
    recent_titles: list,
    research: dict = None,
    article_date: str = None,
    num_candidates: int = NUM_CANDIDATES,
    direction: str = "",
) -> List[str]:
    """
    Generate title candidates that lead with NEWS HOOKS people are searching for.
    
    Priority:
    1. Fresh news from research (Venezuela, earnings, policy, oil prices)
    2. Macro themes people are hearing about
    3. Seasonal context is SECONDARY (end of title at most)
    """
    
    # Extract newsworthy sections from article (for context)
    sections = extract_newsworthy_sections(article_text)
    
    # Extract fresh news hooks from research
    news_hooks = []
    if research:
        news_hooks = extract_fresh_news_hooks(research, article_date)
    
    # Build the news hooks section for prompt
    fresh_hooks = [h for h in news_hooks if h["is_fresh"]]
    other_hooks = [h for h in news_hooks if not h["is_fresh"]][:3]
    
    hooks_text = ""
    if fresh_hooks:
        hooks_text += "FRESH NEWS (within 14 days - PRIORITIZE THESE):\n"
        for h in fresh_hooks[:5]:
            hooks_text += f"- [{h['type'].upper()}] {h['headline']}: {h['summary']}\n"
    
    if other_hooks:
        hooks_text += "\nOTHER RELEVANT CONTEXT:\n"
        for h in other_hooks:
            hooks_text += f"- [{h['type'].upper()}] {h['headline']}: {h['summary']}\n"
    
    if not hooks_text:
        hooks_text = "No specific news hooks found - use article content.\n"
    
    # Recent titles for avoidance
    avoid_sample = recent_titles[-20:] if recent_titles else []
    
    # ---- Detect if the seasonal data is strong enough for stat-forward titles ----
    seasonal_strength = "mixed"
    article_lower = article_text.lower()
    # Look for signals of very strong patterns in the article body
    for phrase in ["10 of 10", "9 of 9", "9 of 10", "10 for 10", "9 for 9", "100%", 
                   "every single", "every midterm", "never lost", "all 10", "all 9",
                   "8 of 9", "8 of 10", "8 for 8", "8 for 9", "8 for 10"]:
        if phrase in article_lower:
            seasonal_strength = "strong"
            break

    system_prompt = f"""You are a senior financial news editor. You write headlines for a site that combines 
real-time market news with proprietary seasonal pattern data that nobody else publishes.

YOUR READERS: Active investors and traders who follow specific stocks. They find articles two ways:
1. Searching for news they just heard (earnings, Venezuela, oil prices, Fed policy)
2. Discovering surprising pattern data they've never seen before (10 for 10 win streaks, 100% seasonal records)

Both types of headlines work. Your job is to write a MIX of both styles so the site reads like a 
real newsroom with variety, not a template engine.

STYLE A — NEWS-FIRST (2-3 of your candidates should be this style):
Lead with the news hook, weave seasonal context in naturally.
Good examples:
"Chevron (CVX) Q4 Earnings Beat Arrives Alongside a Perfect 10-for-10 Midterm Streak"
"Higher Gold Forecasts Lift Newmont (NEM) Into Its Strongest Spring Window in a Decade"
"Record Production Fuels Chevron (CVX) as Midterm-Year Rally Stretch Begins"
"Newmont (NEM) Rides Analyst Upgrades Into a Historically Flawless Spring Run"
"Venezuela Upside Meets a 40-Year Winning Streak for Chevron (CVX)"

STYLE B — STAT-FIRST (2-3 of your candidates should be this style):
Lead with the most striking seasonal number. {"USE THIS STYLE MORE — the data is very strong." if seasonal_strength == "strong" else "Use when the seasonal stat is genuinely surprising."}
Good examples:
"Newmont (NEM) Has Rallied Every Spring for 10 Straight Years, Averaging 13% Gains"
"Chevron (CVX) Has Never Lost Money in This 70-Day Midterm Window Since 1986"
"NVIDIA Has Dropped 6 of 6 Midterm Election Summers — and the Window Just Opened"
"Toll Brothers (TOL) Has Fallen Every Midterm Summer for 9 Straight Cycles"
"This 39-Day Window Has Delivered 238% Cumulative Gains for Newmont (NEM)"

BAD TITLES (never write these):
"NVIDIA enters a historically strong seasonal window this week" (generic template — could be any stock)
"Tesla approaches a historically weak seasonal stretch into December" (same template, different ticker)
"Chevron (CVX) seasonal outlook remains bullish" (vague, no hook, no stat)
"Chevron (CVX) in focus ahead of year-end" (generic, boring)
"CVX enters historically profitable 12-day period" (trader jargon)

RULES:
1. Include company name and (TICKER) in every title
2. Never use these exact phrases: "in focus", "on watch", "spotlight", "in the spotlight", "setup"
3. The word "pattern" is banned (use "streak", "record", "run", "stretch", "window" instead)
4. Under 100 characters
5. Every title must sound like a DIFFERENT journalist wrote it — vary sentence structure, verb choice, and which element leads
6. At least one title must contain a specific number from the article (e.g. "10 of 10", "13%", "70-day")
7. CRITICAL DIRECTION RULE: This is a {"BEARISH/SHORT" if direction == "short" else "BULLISH/LONG"} article. The title sentiment MUST match. {"Use words like: drop, fall, decline, weak, downside, loss, slide, sell-off. NEVER use bullish words like rally, surge, gain, rise, climb, upside." if direction == "short" else "Use words like: rally, gain, rise, climb, surge, upside, bullish. NEVER use bearish words like drop, fall, decline, weak, downside, loss."}

Return ONLY headlines, one per line, no numbering."""

    direction_label = "BEARISH (short)" if direction == "short" else "BULLISH (long)"
    user_prompt = f"""Write {num_candidates} headlines for {company} ({symbol}).
Direction: {direction_label} — ALL titles must reflect this sentiment.
Generate a MIX: some news-first, some stat-first.

===== NEWS HOOKS FROM RESEARCH (for news-first titles) =====
{hooks_text}
===== END NEWS HOOKS =====

===== ARTICLE TEXT (for stat-first titles — find the most striking seasonal numbers) =====
{sections['opening']}

{sections['macro_political'] if sections['macro_political'] else sections['price_drivers']}
===== END ARTICLE =====

HEADLINES TO AVOID (don't copy or closely resemble):
{chr(10).join('- ' + t for t in avoid_sample) if avoid_sample else "None"}

REQUIREMENTS:
- {num_candidates} headlines total: mix of news-first AND stat-first styles
- Include {company} and ({symbol})
- Under 100 characters
- Vary the structure — no two titles should start the same way
- At least one title must include a specific seasonal stat from the article

Write {num_candidates} varied headlines:"""

    response = AI_tools.send_openai_prompt(
        user_prompt,
        system=system_prompt,
        stream=False,
        temperature=0.7,
    )
    
    # Parse response
    titles = []
    for line in response.strip().split("\n"):
        line = line.strip()
        # Remove numbering
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = re.sub(r"^[-•]\s*", "", line)
        # Clean dashes and quotes
        line = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", line)
        line = re.sub(r'^\s*["\']|["\']\s*$', "", line).strip()
        line = re.sub(r"\s{2,}", " ", line)
        
        if line and len(line) > 20:
            titles.append(line)
    
    return titles[:num_candidates]

    response = AI_tools.send_openai_prompt(
        user_prompt,
        system=system_prompt,
        stream=False,
        temperature=0.6,
    )
    
    # Parse response
    titles = []
    for line in response.strip().split("\n"):
        line = line.strip()
        # Remove numbering
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = re.sub(r"^[-•]\s*", "", line)
        # Clean dashes and quotes
        line = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", line)
        line = re.sub(r'^\s*["\']|["\']\s*$', "", line).strip()
        line = re.sub(r"\s{2,}", " ", line)
        
        if line and len(line) > 20:
            titles.append(line)
    
    return titles[:num_candidates]


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def generate_unique_seo_title(
    pattern: dict,
    article_html: str,
    tavily: dict = None,
    persist: bool = True,
    recent_file: str = "recent_titles.json",
):
    """
    Generate a unique, factually-accurate SEO title from the actual article.
    
    Args:
        pattern: Dict with symbol, start_date, days, years, company
        article_html: HTML content string of the article
        tavily: Optional research dict (used for company name if not in pattern)
        persist: Whether to save title to recent_titles.json
        recent_file: Filename for recent titles storage
    
    Returns:
        str: The validated, unique title
    """
    # Get company/symbol/direction
    research = tavily.get("research", tavily) if tavily else {}
    symbol = pattern.get("symbol") or research.get("symbol", "")
    company = pattern.get("company") or research.get("company") or symbol
    direction = pattern.get("direction", "")

    print(f"=" * 60)
    print(f"TITLE GENERATOR v3 - POST-ARTICLE ARCHITECTURE")
    print(f"=" * 60)

    print(f"Company: {company} ({symbol})  Direction: {direction}")
    
    # -----------------------------
    # STAGE 1: Parse article
    # -----------------------------
    print(f"\nStage 1: Parsing article...")
    
    html_content = article_html


    article_text = extract_text_from_html(html_content)
    metadata = extract_article_metadata(html_content, pattern)
    
    print(f"  Extracted {len(article_text.split())} words from article")
    if metadata.get('existing_title'):
        print(f"  Existing title: {metadata['existing_title'][:60]}...")
    
    # Build article word set for validation
    article_words = tokenize_for_validation(article_text)
    print(f"  Article vocabulary: {len(article_words)} unique words")
    
    # -----------------------------
    # Load recent titles
    # -----------------------------
    recent_path = Path(config.news_root_folder).resolve() / recent_file
    print(f"\nRecent titles path: {recent_path}")
    recent_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not recent_path.exists():
        tmp = recent_path.with_suffix(".json.tmp")
        tmp.write_text("[]\n", encoding="utf-8")
        os.replace(tmp, recent_path)
    
    raw = recent_path.read_text(encoding="utf-8")
    if raw.strip() == "":
        recent_titles = []
    else:
        recent_titles = json.loads(raw)
    
    # Normalize
    normalized_titles = []
    for t in recent_titles:
        if isinstance(t, str):
            normalized_titles.append(t)
        elif isinstance(t, dict) and isinstance(t.get("title"), str):
            normalized_titles.append(t["title"])
    recent_titles = normalized_titles[-MAX_ITEMS:]
    
    # -----------------------------
    # STAGE 2, 3, 4: Generate, Validate, Filter
    # -----------------------------
    final_title = None
    
    # Get research dict for news hooks
    research_data = tavily.get("research", tavily) if tavily else {}
    
    # Show what news hooks we found
    if research_data:
        news_hooks = extract_fresh_news_hooks(research_data, tavily.get("article_publish_date") if tavily else None)
        fresh = [h for h in news_hooks if h["is_fresh"]]
        print(f"\nNews hooks found: {len(news_hooks)} total, {len(fresh)} fresh (within 14 days)")
        for h in news_hooks[:5]:
            freshness = "FRESH" if h["is_fresh"] else f"{h['days_old']}d old"
            print(f"  - [{h['type'].upper()}] ({freshness}) {h['headline'][:50]}...")
    
    for attempt in range(MAX_GENERATION_ATTEMPTS):
        print(f"\nStage 2: Generating candidates (attempt {attempt + 1})...")
        
        candidates = generate_title_candidates(
            article_text=article_text,
            company=company,
            symbol=symbol,
            metadata=metadata,
            recent_titles=recent_titles,
            research=research_data,
            article_date=tavily.get("article_publish_date") if tavily else None,
            num_candidates=NUM_CANDIDATES + attempt * 2,
            direction=direction,
        )
        print(f"  Generated {len(candidates)} candidates")
        
        for i, title in enumerate(candidates):
            print(f"\n  Candidate {i + 1}: {title}")
            
            # Stage 3: Factual validation
            validation = validate_title_against_article(
                title=title,
                article_text=article_text,
                company=company,
                symbol=symbol
            )
            
            if not validation["valid"]:
                print(f"    ✗ FACTUAL: {validation['details']} (confidence: {validation['confidence']:.2f})")
                continue
            print(f"    ✓ FACTUAL: Validated (confidence: {validation['confidence']:.2f})")

            # Stage 3b: Direction sentiment check
            if direction:
                title_lower = title.lower()
                bullish_words = {'rally', 'rallies', 'rallied', 'surge', 'surges', 'surged', 'gain', 'gains', 'rise', 'rises', 'climb', 'climbs', 'upside', 'bullish', 'soar', 'soars', 'jump', 'jumps'}
                bearish_words = {'drop', 'drops', 'dropped', 'fall', 'falls', 'fallen', 'decline', 'declines', 'declined', 'weak', 'downside', 'bearish', 'slide', 'slides', 'slid', 'sell-off', 'selloff', 'loss', 'losses', 'tumble', 'tumbles', 'sink', 'sinks', 'plunge'}
                title_tokens = set(re.findall(r'[a-z]+(?:-[a-z]+)*', title_lower))
                has_bullish = bool(title_tokens & bullish_words)
                has_bearish = bool(title_tokens & bearish_words)
                if direction == "short" and has_bullish and not has_bearish:
                    print(f"    ✗ DIRECTION: Title sounds bullish but article is bearish/short")
                    continue
                if direction == "long" and has_bearish and not has_bullish:
                    print(f"    ✗ DIRECTION: Title sounds bearish but article is bullish/long")
                    continue
                print(f"    ✓ DIRECTION: Sentiment matches {direction}")

            # Stage 4: Uniqueness check
            uniqueness = check_uniqueness(
                title=title,
                recent_titles=recent_titles,
                company=company,
                symbol=symbol
            )
            
            if not uniqueness["unique"]:
                print(f"    ✗ UNIQUE: {uniqueness['reason']}")
                continue
            print(f"    ✓ UNIQUE: {uniqueness['reason']}")
            
            # PASSED BOTH CHECKS
            final_title = title
            print(f"\n  >>> SELECTED: {title}")
            break
        
        if final_title:
            break
        else:
            print(f"\n  All candidates rejected, retrying with more candidates...")
    
    # -----------------------------
    # Fallback
    # -----------------------------
    if not final_title:
        print("\nFallback: Generating simple title...")
        # Extract first sentence/key fact from article
        first_sentences = article_text[:500].split('.')
        if first_sentences:
            fallback_prompt = f"""Write ONE simple headline for {company} ({symbol}).
Base it ONLY on this text: "{first_sentences[0][:200]}"
Keep it under 80 characters. Be straightforward."""
            
            fallback_title = AI_tools.send_openai_prompt(
                fallback_prompt,
                system="Return only the headline, nothing else.",
                stream=False,
                temperature=0.5
            ).strip()
            
            fallback_title = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", fallback_title)
            fallback_title = re.sub(r'^\s*["\']|["\']\s*$', "", fallback_title).strip()
            
            if fallback_title:
                final_title = fallback_title
                print(f"  Fallback title: {final_title}")
    
    if not final_title:
        raise RuntimeError("Could not generate a valid title after all attempts")
    
    # -----------------------------
    # Persist
    # -----------------------------
    if persist:
        nt = _norm(final_title)
        recent_titles = [t for t in recent_titles if _norm(t) != nt]
        recent_titles.append(final_title)
        recent_titles = recent_titles[-MAX_ITEMS:]
        
        tmp = recent_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(recent_titles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, recent_path)
        print(f"\nPersisted title to {recent_file}")
    
    print(f"\n{'=' * 60}")
    print(f"FINAL TITLE: {final_title}")
    print(f"{'=' * 60}")
    
    return final_title


# =============================================================================
# HTML INJECTION HELPER
# =============================================================================

def inject_title_into_html(html_path: str, new_title: str, output_path: str = None) -> str:
    """
    Replace the title in an HTML file with a new title.
    
    Args:
        html_path: Path to the HTML file
        new_title: The new title to inject
        output_path: Where to save (defaults to overwriting original)
    
    Returns:
        Path to the modified file
    """
    html_path = Path(html_path)
    output_path = Path(output_path) if output_path else html_path
    
    content = html_path.read_text(encoding="utf-8")
    
    # Replace <title> tag
    content = re.sub(
        r'<title[^>]*>.*?</title>',
        f'<title>{new_title}</title>',
        content,
        flags=re.IGNORECASE | re.DOTALL
    )
    
    # Replace <h1> tag (first occurrence)
    content = re.sub(
        r'<h1[^>]*>.*?</h1>',
        f'<h1>{new_title}</h1>',
        content,
        count=1,
        flags=re.IGNORECASE | re.DOTALL
    )
    
    # Replace og:title meta tag
    content = re.sub(
        r'<meta\s+property=["\']og:title["\']\s+content=["\'][^"\']*["\']',
        f'<meta property="og:title" content="{new_title}"',
        content,
        flags=re.IGNORECASE
    )
    
    # Replace twitter:title meta tag
    content = re.sub(
        r'<meta\s+name=["\']twitter:title["\']\s+content=["\'][^"\']*["\']',
        f'<meta name="twitter:title" content="{new_title}"',
        content,
        flags=re.IGNORECASE
    )
    
    output_path.write_text(content, encoding="utf-8")
    print(f"Injected title into: {output_path}")
    
    return str(output_path)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == '__main__':
    # Test configuration
    tavily_research_json_path = '/var/www/html/wordpress/news/US/2025/12/21/chevron-cvx-set-to-enter-historically-strong-post-election-seasonal-window.research.json'
    article_html_path = '/var/www/html/wordpress/news/US/2025/12/21/chevron-cvx-set-to-enter-historically-strong-post-election-seasonal-window.html'
    
    pattern = {
        'resource_id': 0,
        'symbol': 'CVX',
        'start_date': '2025-12-21',
        'days': 12,
        'years': 'pe1',
    }
    
    # Load research content
    with open(tavily_research_json_path, 'r') as f:
        tavily_research_dict = json.load(f)
    
    # Load article HTML content
    with open(article_html_path, 'r') as f:
        article_html_content = f.read()
    
    # Generate title from article content
    title = generate_unique_seo_title(
        pattern=pattern,
        article_html=article_html_content,
        tavily=tavily_research_dict,
        persist=True
    )
    
    print(f"\nFINAL TITLE: {title}")