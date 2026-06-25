"""
rebuild_search_page.py
======================
Generates the search page for SeasonalMarketNews.com.
Client-side search using posts.json data.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
import sys
sys.path.insert(0, '/home/flask')
import config

# =============================================================================
# CONFIGURATION (should match rebuild_news_home.py)
# =============================================================================

THEME = "light"  # Options: "light", "dark"

# =============================================================================

# Use same paths as rebuild_news_home.py
NEWS_ROOT = Path(config.news_root_folder)
SEARCH_HTML = NEWS_ROOT / "search.html"  # Same level as index.html

SITE_TITLE = "Seasonal Market News"

# Theme definitions (matching rebuild_news_home.py)
THEMES = {
    "light": {
        "bg_primary": "#ffffff",
        "bg_secondary": "#f8f9fa",
        "bg_tertiary": "#e9ecef",
        "text_primary": "#1a1a1a",
        "text_secondary": "#4a4a4a",
        "text_muted": "#6c757d",
        "accent_blue": "#0066cc",
        "accent_green": "#0d7a3e",
        "accent_red": "#c41e3a",
        "border_color": "#dee2e6",
        "card_shadow": "0 1px 3px rgba(0,0,0,0.08)",
        "card_hover_shadow": "0 4px 12px rgba(0,0,0,0.12)",
        "badge_bullish_bg": "rgba(13, 122, 62, 0.1)",
        "badge_bearish_bg": "rgba(196, 30, 58, 0.1)",
    },
    "dark": {
        "bg_primary": "#0a0a0b",
        "bg_secondary": "#111113",
        "bg_tertiary": "#1a1a1d",
        "text_primary": "#f5f5f7",
        "text_secondary": "#a1a1a6",
        "text_muted": "#6e6e73",
        "accent_blue": "#0a84ff",
        "accent_green": "#30d158",
        "accent_red": "#ff453a",
        "border_color": "#2c2c2e",
        "card_shadow": "none",
        "card_hover_shadow": "none",
        "badge_bullish_bg": "rgba(48, 209, 88, 0.15)",
        "badge_bearish_bg": "rgba(255, 69, 58, 0.15)",
    }
}


def build_search_page():
    """Build the search page HTML."""
    
    t = THEMES.get(THEME, THEMES["light"])
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/png" href="/smnfav.png">
    <title>Search | {SITE_TITLE}</title>
    <meta name="description" content="Search all seasonal market analysis articles.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: {t["bg_primary"]};
            --bg-secondary: {t["bg_secondary"]};
            --bg-tertiary: {t["bg_tertiary"]};
            --text-primary: {t["text_primary"]};
            --text-secondary: {t["text_secondary"]};
            --text-muted: {t["text_muted"]};
            --accent-blue: {t["accent_blue"]};
            --accent-green: {t["accent_green"]};
            --accent-red: {t["accent_red"]};
            --border-color: {t["border_color"]};
            --card-shadow: {t["card_shadow"]};
            --card-hover-shadow: {t["card_hover_shadow"]};
            --badge-bullish-bg: {t["badge_bullish_bg"]};
            --badge-bearish-bg: {t["badge_bearish_bg"]};
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        /* Header */
        header {{
            border-bottom: 1px solid var(--border-color);
            background: var(--bg-primary);
        }}

        .header-content {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .logo {{
            display: flex;
            align-items: baseline;
            gap: 2px;
            text-decoration: none;
        }}

        .logo-seasonal {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent-blue);
            letter-spacing: -0.5px;
        }}

        .logo-market {{
            font-size: 22px;
            font-weight: 700;
            color: var(--text-primary);
            letter-spacing: -0.5px;
        }}

        .logo-news {{
            font-size: 22px;
            font-weight: 400;
            color: var(--text-muted);
            letter-spacing: -0.5px;
        }}

        nav {{
            display: flex;
            gap: 28px;
        }}

        nav a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: color 0.2s ease;
        }}

        nav a:hover {{
            color: var(--text-primary);
        }}

        /* Search Section */
        .search-section {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 24px;
        }}

        .search-header {{
            margin-bottom: 32px;
        }}

        .search-header h1 {{
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
            color: var(--text-primary);
        }}

        .search-header p {{
            color: var(--text-muted);
            font-size: 15px;
        }}

        /* Filters */
        .filters {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 32px;
            padding: 20px;
            background: var(--bg-secondary);
            border-radius: 10px;
            border: 1px solid var(--border-color);
        }}

        .filter-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .filter-group label {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .filter-group input,
        .filter-group select {{
            padding: 10px 14px;
            font-size: 14px;
            font-family: inherit;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            outline: none;
            transition: border-color 0.2s ease;
        }}

        .filter-group input:focus,
        .filter-group select:focus {{
            border-color: var(--accent-blue);
        }}

        .filter-group input[type="text"] {{
            min-width: 250px;
        }}

        .filter-group input[type="date"] {{
            min-width: 150px;
        }}

        .filter-group select {{
            min-width: 140px;
        }}

        .filter-actions {{
            display: flex;
            align-items: flex-end;
            gap: 10px;
        }}

        .btn {{
            padding: 10px 20px;
            font-size: 14px;
            font-family: inherit;
            font-weight: 600;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.2s ease, transform 0.1s ease;
        }}

        .btn-primary {{
            background: var(--accent-blue);
            color: white;
        }}

        .btn-primary:hover {{
            background: #0052a3;
        }}

        .btn-secondary {{
            background: var(--bg-tertiary);
            color: var(--text-secondary);
        }}

        .btn-secondary:hover {{
            background: var(--border-color);
        }}

        /* Results Info */
        .results-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 2px solid var(--text-primary);
        }}

        .results-count {{
            font-size: 13px;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .results-stats {{
            display: flex;
            gap: 16px;
            font-size: 13px;
        }}

        .stat-bullish {{
            color: var(--accent-green);
            font-weight: 600;
        }}

        .stat-bearish {{
            color: var(--accent-red);
            font-weight: 600;
        }}

        /* Results List */
        .results-list {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}

        .result-item {{
            display: flex;
            gap: 16px;
            text-decoration: none;
            padding: 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
            box-shadow: var(--card-shadow);
        }}

        .result-item:hover {{
            border-color: var(--accent-blue);
            box-shadow: var(--card-hover-shadow);
        }}

        .result-image {{
            flex: 0 0 140px;
            height: 90px;
            background: var(--bg-tertiary);
            border-radius: 6px;
            overflow: hidden;
        }}

        .result-image img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
        }}

        .result-image .no-image {{
            width: 100%;
            height: 100%;
            background: var(--bg-tertiary);
        }}

        .result-content {{
            flex: 1;
            min-width: 0;
        }}

        .result-header {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin-bottom: 6px;
        }}

        .result-tag {{
            flex-shrink: 0;
            padding: 2px 6px;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-radius: 3px;
            margin-top: 3px;
        }}

        .result-tag.bullish {{
            background: var(--badge-bullish-bg);
            color: var(--accent-green);
        }}

        .result-tag.bearish {{
            background: var(--badge-bearish-bg);
            color: var(--accent-red);
        }}

        .result-content h3 {{
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin: 0;
        }}

        .result-excerpt {{
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
            margin: 6px 0;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .result-meta {{
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'IBM Plex Mono', monospace;
        }}

        /* Loading / Empty States */
        .loading, .no-results {{
            text-align: center;
            padding: 60px 20px;
            color: var(--text-muted);
        }}

        .loading {{
            font-size: 14px;
        }}

        .no-results h3 {{
            font-size: 18px;
            color: var(--text-primary);
            margin-bottom: 8px;
        }}

        .no-results p {{
            font-size: 14px;
        }}

        /* Footer */
        footer {{
            border-top: 1px solid var(--border-color);
            padding: 28px 24px;
            background: var(--bg-secondary);
            margin-top: 40px;
        }}

        .footer-content {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }}

        .footer-left {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        .footer-left a {{
            color: var(--text-muted);
            text-decoration: none;
        }}

        .footer-left a:hover {{
            color: var(--text-secondary);
        }}

        .footer-links {{
            display: flex;
            gap: 24px;
        }}

        .footer-links a {{
            font-size: 13px;
            color: var(--text-muted);
            text-decoration: none;
        }}

        .footer-links a:hover {{
            color: var(--text-secondary);
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            nav {{
                display: none;
            }}

            .filters {{
                flex-direction: column;
            }}

            .filter-group input[type="text"],
            .filter-group input[type="date"],
            .filter-group select {{
                min-width: 100%;
                width: 100%;
            }}

            .result-item {{
                flex-direction: column;
            }}

            .result-image {{
                flex: none;
                width: 100%;
                height: 160px;
            }}

            .footer-content {{
                flex-direction: column;
                text-align: center;
            }}

            .results-info {{
                flex-direction: column;
                gap: 8px;
                text-align: center;
            }}
        }}
    </style>
</head>
<body>
    <!-- Header -->
    <header>
        <div class="header-content">
            <a href="./" class="logo">
                <span class="logo-seasonal">Seasonal</span><span class="logo-market">Market</span><span class="logo-news">News</span>
            </a>
            <nav>
                <a href="./">Home</a>
                <a href="https://tradewave.ai" target="_blank">TradeWave</a>
            </nav>
        </div>
    </header>

    <!-- Search Section -->
    <section class="search-section">
        <div class="search-header">
            <h1>Search Articles</h1>
            <p>Find seasonal market analysis across all symbols and time periods.</p>
        </div>

        <!-- Filters -->
        <div class="filters">
            <div class="filter-group">
                <label>Search</label>
                <input type="text" id="searchQuery" placeholder="Keyword, symbol, or title...">
            </div>
            <div class="filter-group">
                <label>Symbol</label>
                <select id="filterSymbol">
                    <option value="">All Symbols</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Direction</label>
                <select id="filterDirection">
                    <option value="">All</option>
                    <option value="long">Bullish</option>
                    <option value="short">Bearish</option>
                </select>
            </div>
            <div class="filter-group">
                <label>From Date</label>
                <input type="date" id="filterDateFrom">
            </div>
            <div class="filter-group">
                <label>To Date</label>
                <input type="date" id="filterDateTo">
            </div>
            <div class="filter-actions">
                <button class="btn btn-primary" onclick="applyFilters()">Search</button>
                <button class="btn btn-secondary" onclick="clearFilters()">Clear</button>
            </div>
        </div>

        <!-- Results Info -->
        <div class="results-info">
            <span class="results-count" id="resultsCount">Loading...</span>
            <div class="results-stats">
                <span class="stat-bullish" id="statBullish"></span>
                <span class="stat-bearish" id="statBearish"></span>
            </div>
        </div>

        <!-- Results -->
        <div class="results-list" id="resultsList">
            <div class="loading">Loading articles...</div>
        </div>
    </section>

    <!-- Footer -->
    <footer>
        <div class="footer-content">
            <div class="footer-left">
                © {datetime.now().year} <a href="https://taradataresearch.com" target="_blank">Tara Data Research LLC</a>. All rights reserved.
            </div>
            <div class="footer-links">
                <a href="https://tradewave.ai" target="_blank">TradeWave</a>
            </div>
        </div>
    </footer>

    <script>
        let allArticles = [];
        let filteredArticles = [];

        // Load articles on page load
        async function loadArticles() {{
            try {{
                const response = await fetch('posts.json');
                allArticles = await response.json();
                
                // Sort by date descending
                allArticles.sort((a, b) => new Date(b.published_date) - new Date(a.published_date));
                
                // Populate symbol dropdown
                populateSymbols();
                
                // Initial display
                filteredArticles = allArticles;
                displayResults();
            }} catch (error) {{
                console.error('Error loading articles:', error);
                document.getElementById('resultsList').innerHTML = 
                    '<div class="no-results"><h3>Error loading articles</h3><p>Please try again later.</p></div>';
            }}
        }}

        function populateSymbols() {{
            const symbols = [...new Set(allArticles.map(a => a.symbol).filter(Boolean))].sort();
            const select = document.getElementById('filterSymbol');
            symbols.forEach(symbol => {{
                const option = document.createElement('option');
                option.value = symbol;
                option.textContent = symbol;
                select.appendChild(option);
            }});
        }}

        function applyFilters() {{
            const query = document.getElementById('searchQuery').value.toLowerCase().trim();
            const symbol = document.getElementById('filterSymbol').value;
            const direction = document.getElementById('filterDirection').value;
            const dateFrom = document.getElementById('filterDateFrom').value;
            const dateTo = document.getElementById('filterDateTo').value;

            console.log('Filtering:', {{ query, symbol, direction, dateFrom, dateTo, totalArticles: allArticles.length }});

            filteredArticles = allArticles.filter(article => {{
                // Text search
                if (query) {{
                    const searchText = [
                        article.title || '',
                        article.dek || '',
                        article.symbol || ''
                    ].join(' ').toLowerCase();
                    if (!searchText.includes(query)) return false;
                }}

                // Symbol filter
                if (symbol && article.symbol !== symbol) return false;

                // Direction filter (skip if article has no direction)
                if (direction && article.direction && article.direction !== direction) return false;

                // Date range
                if (dateFrom || dateTo) {{
                    const pubDate = article.published_date ? article.published_date.split('T')[0] : '';
                    if (dateFrom && pubDate < dateFrom) return false;
                    if (dateTo && pubDate > dateTo) return false;
                }}

                return true;
            }});

            console.log('Filtered results:', filteredArticles.length);
            displayResults();
        }}

        function clearFilters() {{
            document.getElementById('searchQuery').value = '';
            document.getElementById('filterSymbol').value = '';
            document.getElementById('filterDirection').value = '';
            document.getElementById('filterDateFrom').value = '';
            document.getElementById('filterDateTo').value = '';
            filteredArticles = allArticles;
            displayResults();
        }}

        function displayResults() {{
            const container = document.getElementById('resultsList');
            
            // Update counts
            const bullishCount = filteredArticles.filter(a => a.direction === 'long').length;
            const bearishCount = filteredArticles.filter(a => a.direction === 'short').length;
            
            document.getElementById('resultsCount').textContent = 
                `${{filteredArticles.length}} Article${{filteredArticles.length !== 1 ? 's' : ''}} Found`;
            document.getElementById('statBullish').textContent = `${{bullishCount}} Bullish`;
            document.getElementById('statBearish').textContent = `${{bearishCount}} Bearish`;

            if (filteredArticles.length === 0) {{
                container.innerHTML = '<div class="no-results"><h3>No articles found</h3><p>Try adjusting your filters.</p></div>';
                return;
            }}

            container.innerHTML = filteredArticles.map(article => {{
                const heroUrl = getHeroImageUrl(article.url, article.symbol);
                const imageHtml = heroUrl 
                    ? `<img src="${{heroUrl}}" alt="${{article.symbol}}">`
                    : '<div class="no-image"></div>';
                
                const tagHtml = article.direction 
                    ? `<span class="result-tag ${{article.direction === 'long' ? 'bullish' : 'bearish'}}">${{article.direction === 'long' ? 'Bullish' : 'Bearish'}}</span>`
                    : '';

                const date = formatDate(article.published_date);
                const metaParts = [date, article.symbol];
                if (article.pattern_days) metaParts.push(`${{article.pattern_days}}-Day Pattern`);
                const meta = metaParts.filter(Boolean).join(' • ');

                return `
                    <a href="${{article.url}}" class="result-item">
                        <div class="result-image">${{imageHtml}}</div>
                        <div class="result-content">
                            <div class="result-header">
                                ${{tagHtml}}
                                <h3>${{article.title || 'Untitled'}}</h3>
                            </div>
                            <p class="result-excerpt">${{article.dek || ''}}</p>
                            <div class="result-meta">${{meta}}</div>
                        </div>
                    </a>
                `;
            }}).join('');
        }}

        function getHeroImageUrl(articleUrl, symbol) {{
            if (!articleUrl || !symbol) return null;
            const lastSlash = articleUrl.lastIndexOf('/');
            if (lastSlash === -1) return null;
            const basePath = articleUrl.substring(0, lastSlash + 1);
            return `${{basePath}}hero_${{symbol.toUpperCase()}}.jpg`;
        }}

        function formatDate(isoDate) {{
            if (!isoDate) return '';
            try {{
                const date = new Date(isoDate);
                return date.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric', year: 'numeric' }});
            }} catch {{
                return '';
            }}
        }}

        // Allow Enter key to search
        document.getElementById('searchQuery').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') applyFilters();
        }});

        // Check for query parameter in URL and auto-search
        function checkUrlParams() {{
            const urlParams = new URLSearchParams(window.location.search);
            const query = urlParams.get('q');
            if (query) {{
                document.getElementById('searchQuery').value = query;
                applyFilters();
            }}
        }}

        // Load on page ready
        loadArticles().then(() => {{
            checkUrlParams();
        }});
    </script>
</body>
</html>
'''

    SEARCH_HTML.write_text(html, "utf-8")
    return {"wrote": str(SEARCH_HTML), "theme": THEME}


if __name__ == "__main__":
    out = build_search_page()
    print(out)