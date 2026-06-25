"""
html_injection.py
=================

Utilities for injecting related articles HTML into article pages.

Provides functions to:
1. Generate semantic HTML for related articles section
2. Find optimal injection point in article HTML
3. Generate JSON-LD schema for related articles
"""

from typing import List, Dict, Any
import re




def strip_existing_related_articles_schema(html: str) -> str:
    """
    Remove existing related articles ItemList schema from <head>.
    Makes schema injection idempotent.
    """
    pattern = r'<script\s+type=["\']application/ld\+json["\'][^>]*>\s*\{[^}]*"@type"\s*:\s*"ItemList".*?</script>\s*'
    cleaned = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)
    return cleaned
    

def generate_related_articles_html(related_articles: List[Dict[str, Any]]) -> str:
    """
    Generate semantic HTML for related articles section.
    
    Creates a <section> with:
    - Proper ARIA labels
    - Semantic <nav> structure
    - Links with title and symbol
    - SEO-friendly markup
    
    Args:
        related_articles: List of related article dicts with url, title, symbol
    
    Returns:
        HTML string for related articles section
    """
    if not related_articles:
        return ""
    
    html_parts = []
    
    # Section wrapper with semantic HTML
    html_parts.append('<section class="related-articles" aria-labelledby="related-articles-heading">')
    html_parts.append('  <h2 id="related-articles-heading">Related Articles</h2>')
    html_parts.append('  <nav aria-label="Related articles navigation">')
    html_parts.append('    <ul class="related-articles-list">')
    
    for article in related_articles:
        url = article.get('url', '')
        title = article.get('title', '')
        symbol = article.get('symbol', '')
        relation_type = article.get('relation_type', 'thematic')
        
        # Clean title for display
        display_title = title.replace('"', '&quot;')
        
        # Add relation type as data attribute for potential styling
        html_parts.append(f'      <li data-relation-type="{relation_type}">')
        html_parts.append(f'        <a href="{url}" rel="related">')
        html_parts.append(f'          <span class="article-symbol">{symbol}</span>')
        html_parts.append(f'          <span class="article-title">{display_title}</span>')
        html_parts.append('        </a>')
        html_parts.append('      </li>')
    
    html_parts.append('    </ul>')
    html_parts.append('  </nav>')
    html_parts.append('</section>')
    
    # Add inline CSS for basic styling (can be overridden by site stylesheet)
    html_parts.append('''
<style>
.related-articles {
  margin: 2rem 0 1rem;
  padding: 1.5rem;
  background: #f8f9fa;
  border-left: 4px solid #007bff;
}

.related-articles h2 {
  margin: 0 0 1.5rem 0;
  font-size: 1.5rem;
  color: #333;
}

.related-articles-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.related-articles-list li {
  margin-bottom: 0.25rem;
}

.related-articles-list a {
  display: flex;
  align-items: baseline;
  text-decoration: none;
  color: #007bff;
  transition: color 0.2s;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
}

.related-articles-list a:hover {
  background: #e9ecef;
  color: #0056b3;
}

.article-symbol {
  font-size: 0.95rem;
  font-weight: 700;
  color: #333;
  margin-right: 0.5rem;
}

.article-title {
  flex: 1;
  font-size: 0.9rem;
  font-weight: 400;
  color: #6c757d;
  line-height: 1.4;
}

/* Mobile responsive */
@media (max-width: 640px) {
  .related-articles {
    padding: 1.5rem 1rem;
  }
  
  .related-articles-list a {
    flex-direction: column;
    align-items: flex-start;
  }
  
  .article-symbol {
    margin-bottom: 0.25rem;
  }
}
</style>
''')
    
    return '\n'.join(html_parts)


def generate_related_articles_schema(
    base_article_url: str,
    related_articles: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Generate JSON-LD schema for related articles.
    
    Uses the 'relatedLink' property to indicate related content.
    This helps search engines understand content relationships.
    
    Args:
        base_article_url: URL of the main article
        related_articles: List of related article dicts
    
    Returns:
        Dict suitable for JSON-LD injection
    """
    if not related_articles:
        return {}
    
    related_urls = [article.get('url') for article in related_articles if article.get('url')]
    
    # Return schema snippet that can be merged with existing Article schema
    # or used standalone
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx + 1,
                "url": url
            }
            for idx, url in enumerate(related_urls)
        ]
    }


def find_injection_point(html: str) -> int:
    """
    Find the optimal injection point for related articles in HTML.
    
    Strategy (in order of preference):
    1. Just before </article> closing tag
    2. Just before </main> closing tag
    3. Just before </body> closing tag
    4. If none found, return -1 (caller should handle)
    
    Returns:
        Integer position where related articles HTML should be inserted,
        or -1 if no suitable location found.
    """
    # Try </article> first (most semantic for news articles)
    article_close = html.lower().rfind('</article>')
    if article_close != -1:
        return article_close
    
    # Try </main>
    main_close = html.lower().rfind('</main>')
    if main_close != -1:
        return main_close
    
    # Fallback to </body>
    body_close = html.lower().rfind('</body>')
    if body_close != -1:
        return body_close
    
    # No suitable location found
    return -1


def inject_related_articles_html(
    original_html: str,
    related_articles: List[Dict[str, Any]]
) -> str:
    """
    Inject related articles HTML into the original article HTML.
    
    Finds optimal injection point and inserts the related articles section.
    
    Args:
        original_html: Original article HTML
        related_articles: List of related article dicts
    
    Returns:
        Modified HTML with related articles injected
    """
    if not related_articles:
        return original_html
    
    # Generate the HTML
    related_html = generate_related_articles_html(related_articles)
    
    # Find injection point
    inject_pos = find_injection_point(original_html)
    
    if inject_pos == -1:
        # No suitable location found - append before </html>
        html_close = original_html.lower().rfind('</html>')
        if html_close != -1:
            inject_pos = html_close
        else:
            # Last resort: append to end
            return original_html + '\n' + related_html
    
    # Inject the HTML
    return original_html[:inject_pos] + '\n' + related_html + '\n' + original_html[inject_pos:]


def strip_existing_related_articles(html: str) -> str:
    """
    Remove existing related articles section from HTML.
    
    This makes the injection idempotent - you can re-run it without
    accumulating duplicate sections.
    
    Args:
        html: HTML string potentially containing old related articles
    
    Returns:
        HTML with related articles section removed
    """
    # Pattern to match the entire related articles section
    pattern = r'<section class="related-articles"[^>]*>.*?</section>\s*<style>.*?</style>'
    
    # Remove all matches (there should only be one, but be safe)
    cleaned = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)
    
    return cleaned


# ---------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------

if __name__ == "__main__":
    """
    Smoketest for HTML generation
    """
    
    # Sample related articles
    related = [
        {
            "url": "http://example.com/news/US/2025/12/03/goog-seasonal.html",
            "title": "Alphabet Inc. (GOOG) trades inside a historically strong seasonal window",
            "symbol": "GOOG",
            "relation_type": "same_ticker",
        },
        {
            "url": "http://example.com/news/US/2025/11/25/msft-cloud-ai.html",
            "title": "Microsoft (MSFT) holds firm near highs as cloud and AI drive earnings",
            "symbol": "MSFT",
            "relation_type": "thematic",
        },
        {
            "url": "http://example.com/news/INDX/2025/12/03/spx-post-election.html",
            "title": "S&P 500 (SPX) holds near records as sector rotation tests breadth",
            "symbol": "SPX",
            "relation_type": "index_macro",
        },
    ]
    
    # Generate HTML
    html = generate_related_articles_html(related)
    print("=== Generated HTML ===")
    print(html)
    
    print("\n=== Generated Schema ===")
    schema = generate_related_articles_schema(
        "http://example.com/base-article.html",
        related
    )
    import json
    print(json.dumps(schema, indent=2))