"""
generate_about_page.py
======================
Generates /about.html for SeasonalMarketNews.com.

Run after any bio update, or once after initial deployment.

Usage:
    python generate_about_page.py
"""
import sys, json
from pathlib import Path

sys.path.insert(0, '/home/flask')
import config
from article_post_process import _inject_site_wrapper

SITE_URL = config.news_website_url.rstrip('/')


def generate_about_page():
    news_root = Path(config.news_root_folder).resolve()

    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Afshin Moshrefi",
        "jobTitle": "Founder and Quantitative Researcher",
        "email": "mailto:afshin@tradewave.ai",
        "url": "https://moshrefi.com/",
        "image": "https://moshrefi.com/assets/img/afshin-1024.webp",
        "worksFor": {
            "@type": "Organization",
            "name": "Tara Data Research LLC",
            "url": "https://taradataresearch.com/"
        },
        "sameAs": [
            "https://moshrefi.com/",
            "https://tradewave.ai/",
            "https://seasonalmarketnews.com/",
            "https://100yearprophecy.com/"
        ],
        "knowsAbout": [
            "Quantitative finance",
            "Seasonal market analysis",
            "Machine learning",
            "Financial data systems",
            "Presidential election cycle patterns"
        ]
    }

    person_ld_str = json.dumps(person_ld, indent=2)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>About Afshin Moshrefi | Seasonal Market News</title>
  <meta name="description" content="Afshin Moshrefi is the founder and quantitative researcher behind Seasonal Market News and TradeWave.ai, building data-driven seasonal market research systems since 2013.">
  <link rel="canonical" href="{SITE_URL}/about.html">
  <meta name="robots" content="max-snippet:-1, max-image-preview:large, max-video-preview:-1">

  <meta property="og:locale" content="en_US">
  <meta property="og:type" content="profile">
  <meta property="og:title" content="About Afshin Moshrefi | Seasonal Market News">
  <meta property="og:description" content="Founder and quantitative researcher behind Seasonal Market News and TradeWave.ai.">
  <meta property="og:url" content="{SITE_URL}/about.html">
  <meta property="og:image" content="https://moshrefi.com/assets/img/afshin-1024.webp">
  <meta property="og:site_name" content="Seasonal Market News">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="About Afshin Moshrefi | Seasonal Market News">
  <meta name="twitter:description" content="Founder and quantitative researcher behind Seasonal Market News and TradeWave.ai.">
  <meta name="twitter:image" content="https://moshrefi.com/assets/img/afshin-1024.webp">

  <script type="application/ld+json">
  {person_ld_str}
  </script>
</head>
<body>

<article class="smn-article">
  <div class="smn-article-inner">

    <div class="about-hero">
      <img
        src="https://moshrefi.com/assets/img/afshin-640.webp"
        srcset="https://moshrefi.com/assets/img/afshin-640.webp 640w, https://moshrefi.com/assets/img/afshin-1024.webp 1024w"
        sizes="(max-width: 600px) 120px, 160px"
        width="160" height="215"
        alt="Afshin Moshrefi"
        class="about-portrait">
      <div>
        <h1>Afshin Moshrefi</h1>
        <p class="about-role">Founder, Tara Data Research LLC &middot; Quantitative Researcher</p>
        <p class="about-contact"><a href="mailto:afshin@tradewave.ai">afshin@tradewave.ai</a></p>
      </div>
    </div>

    <p>
      Afshin Moshrefi is the founder-engineer and quantitative researcher behind
      <a href="https://seasonalmarketnews.com/">Seasonal Market News</a> and
      <a href="https://tradewave.ai/">TradeWave.ai</a>, operated under
      <a href="https://taradataresearch.com/">Tara Data Research LLC</a>.
      His work centers on building systems that surface historically consistent market patterns,
      with a focus on seasonality, regime behavior, and presidential election-cycle context,
      measured across US stocks, ETFs, indices, futures, and forex.
    </p>

    <h2>What does Afshin Moshrefi research?</h2>
    <p class="direct-answer">
      Moshrefi's research focuses on multi-decade seasonal market patterns, regime behavior,
      and election-cycle context. Every pattern is defined, measured, and tested across time.
      The goal is reproducible, evidence-first analysis rather than narrative-driven commentary.
    </p>

    <h2>What is the background behind Seasonal Market News?</h2>
    <p class="direct-answer">
      Seasonal Market News grew out of TradeWave's quantitative research platform.
      Each article combines over 100 years of seasonal data with current market news,
      producing institutional-style analysis grounded in measurable history rather than opinion.
    </p>

    <h2>Background</h2>
    <p>Moshrefi's career spans software engineering, invention, and applied machine learning
    across multiple industries:</p>
    <ul>
      <li>Built an early medical image management platform adopted across gastroenterology, dermatology, and dentistry.</li>
      <li>Created InstantWeb in the late 1990s, a website generator that allowed users to publish a web presence in minutes, years before mainstream blog-based builders became common.</li>
      <li>At Verizon, authored 16 invention disclosures, including work related to video communication over conventional telephony infrastructure.</li>
      <li>Began focused machine learning work in 2013 and completed formal training by 2017.</li>
      <li>Worked as an AI researcher in medical coding, developing production ML systems for real-world use.</li>
      <li>TradeWave began as a personal research project and evolved into a platform after the results proved unusually consistent and repeatable across time.</li>
    </ul>

    <h2>Why is this research different from traditional market analysis?</h2>
    <p class="direct-answer">
      The approach is engineer-led rather than finance-industry-led. Every claim is defined,
      measured, and auditable. Patterns must be reproducible. Vague narratives and
      untestable forecasts are excluded by design.
    </p>

    <h2>Published Work</h2>
    <p>
      Moshrefi is the author of <a href="{config.book_amazon_url}" target="_blank" rel="noopener"><em>The 100-Year Pattern</em></a>,
      a research-driven book documenting a long-horizon seasonal market pattern and the framework
      used to test it across regimes. The book is a public explanation of the thesis behind
      TradeWave's seasonality and election-cycle research.
    </p>

    <h2>Products and Projects</h2>
    <ul>
      <li><a href="https://tradewave.ai/">TradeWave.ai</a> — Quantitative seasonality and regime research platform for US stocks, ETFs, indices, futures, and forex.</li>
      <li><a href="https://seasonalmarketnews.com/">Seasonal Market News</a> — Institutional-style market news grounded in measurable seasonal history.</li>
      <li><a href="https://taradataresearch.com/">Tara Data Research LLC</a> — Parent company for all research and product work.</li>
    </ul>

    <section class="methodology-note">
      <h2>About the data behind Seasonal Market News</h2>
      <p>
        All seasonal pattern data is sourced from <a href="https://tradewave.ai/">TradeWave.ai</a>,
        covering US stocks, ETFs, indices, futures, and forex with up to 25 years of lookback.
        Read the full <a href="{SITE_URL}/methodology.html">data methodology</a> or the book
        <a href="{config.book_amazon_url}" target="_blank" rel="noopener"><em>The 100-Year Pattern</em></a>
        for the research framework behind these patterns.
      </p>
    </section>

  </div>
</article>

<style>
.about-hero {{
  display: flex;
  align-items: flex-start;
  gap: 24px;
  margin: 0 0 28px;
}}
.about-portrait {{
  width: 160px;
  height: auto;
  border-radius: 8px;
  flex-shrink: 0;
}}
.about-hero h1 {{
  margin: 0 0 6px;
  font-size: 1.9rem;
}}
.about-role {{
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 0.95rem;
}}
.about-contact {{
  margin: 0;
  font-size: 0.95rem;
}}
@media (max-width: 540px) {{
  .about-hero {{
    flex-direction: column;
    align-items: center;
    text-align: center;
  }}
}}
</style>

</body>
</html>'''

    html = _inject_site_wrapper(html, '', 'About Afshin Moshrefi')

    out_path = news_root / 'about.html'
    out_path.write_text(html, encoding='utf-8')
    print(f'[ABOUT] Generated {out_path}')


if __name__ == '__main__':
    generate_about_page()
