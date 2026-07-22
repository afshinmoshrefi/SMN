"""
article_workflow.py
===================

Purpose:
    This module provides the full end-to-end workflow for generating and publishing
    a complete AI-driven news article for the Seasonal Market News system.
    It is the single orchestration layer that runs ALL required steps:

        1) Generate TradeWave seasonal charts
        2) Generate hero image
        3) Run Tavily + Grok research pipeline
        4) Build the article prompt
        5) Generate the final article HTML with OpenAI
        6) Publish the article

    Each step is wrapped in structured tracking so the caller (sync or async)
    can see exactly which step succeeded, which step failed, and why.

    This script does NOT talk to Redis or queues directly.
    blog_processor.py owns job_id creation and Redis status updates.
    article_workflow.py only returns a tracking dict describing the run.


Key Behaviors:
    • A failure in ANY step stops the workflow and returns tracking["status"] = "error".
    • 'years' is always treated as a STRING in all TradeWave + SMN contexts.
    • Tracking includes per-step status, error messages, and the root exception.
    • The workflow is designed to be fully async-safe, but also callable directly.


Smoke Test:
    The bottom of this file includes a __main__ section that runs a local
    smoke test. This test is critical during development because it verifies
    all steps of the pipeline (charts, hero, Tavily, Grok, prompt, OpenAI,
    and publish) WITHOUT involving Redis or the async queue system.

    To run the smoke test:

        python3 article_workflow.py

    The test will:
        • Print detailed logs from each step.
        • Produce a tracking JSON dictionary showing success/failure.
        • Allow rapid debugging before hooking this file into the async processor.

Usage Notes:
    • Once validated with the smoke test, blog_processor.py should call:

            generate_news_article(...)
    
      using normalized parameters extracted from the queue message.
    • Any job-level tracking (Redis, job_id, failed-job lists, etc.)
      must be handled by the caller, not this module.

"""

import json
import datetime
import os
import secrets
from typing import Dict, Any, List, Tuple, Optional
import time
from article_images import create_article_images
from blog_queue import inject_hero_into_article
from article_prompt import create_article_prompt, get_opp_data, detect_market_family, WHITELISTED_SOURCE_DOMAINS
    

from article_hero_image import hero_image_workflow, HERO_WIDTH_ATTR, HERO_HEIGHT_ATTR
from blog_tools import get_company_name
import AI_tools
from publish_article import publish_article_web

# Write-only audit observer (article_audit.py). Recording must NEVER affect
# generation: every article_audit entry point swallows its own errors, and this
# import falls back to a no-op shim so even a broken module cannot stop publishing.
try:
    import article_audit
except Exception as _audit_import_error:  # pragma: no cover - defensive
    print(f"[AUDIT] article_audit unavailable, auditing disabled: {_audit_import_error}")
    class _NoopAudit:
        @staticmethod
        def begin(identity): return None
        @staticmethod
        def record(name, value): pass
        @staticmethod
        def append(name, value): pass
        @staticmethod
        def finish(trail, tracking): return None
    article_audit = _NoopAudit()

STORE_RESEARCH_JSON_SIDECAR = False  # Disabled for now


# ----------------------------------------------------------------------
# Helper: tracking structure
# ----------------------------------------------------------------------

def _replace_title_in_html(html: str, new_title: str) -> str:
    """Replace <title> and first <h1> with new_title."""
    import re
    html = re.sub(r'<title>.*?</title>', f'<title>{new_title}</title>', html, count=1, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r'<h1[^>]*>.*?</h1>', f'<h1>{new_title}</h1>', html, count=1, flags=re.IGNORECASE | re.DOTALL)
    return html


def init_tracking(resource_id: str,
                  symbol: str,
                  date: str,
                  days: int,
                  years: str,
                  direction: str,
                  article_publish_date: Optional[str] = None,
                  mode: str = "2") -> Dict[str, Any]:
    return {
        "resource_id": str(resource_id),
        "symbol": symbol,
        "start_date": date,
        "days": int(days),
        "years": str(years),
        "direction": str(direction),
        "article_publish_date": article_publish_date or datetime.date.today().isoformat(),
        "mode": mode,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "steps": {
            "generate_tradewave_charts": {"status": "pending", "error": None},
            "generate_hero_image": {"status": "pending", "error": None},
            "research_tavily": {"status": "pending", "error": None},
            "article_prompt": {"status": "pending", "error": None},
            "write_article": {"status": "pending", "error": None},
            "citation_gate": {"status": "pending", "error": None},
            "publish_article": {"status": "pending", "error": None},
        },
        "status": "pending",
        "error_message": None,
        "error_step": None,
    }


def mark_step_success(tracking: Dict[str, Any], step: str) -> None:
    tracking["steps"][step]["status"] = "success"
    tracking["steps"][step]["error"] = None


def mark_step_error(tracking: Dict[str, Any], step: str, exc: Exception) -> None:
    tracking["steps"][step]["status"] = "error"
    tracking["steps"][step]["error"] = str(exc)
    tracking["status"] = "error"
    tracking["error_message"] = str(exc)
    tracking["error_step"] = step


# ----------------------------------------------------------------------
# Step 1: TradeWave charts and core data
# ----------------------------------------------------------------------

def generate_tradewave_charts(image_size_key: str,
                              resource_id: str,
                              symbol: str,
                              date: str,
                              days: int,
                              years: str,
                              theme: str = "light") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate the article chart images and core seasonal opportunity data.
    Returns:
        img_paths: list of image metadata dicts (from create_article_images).
        cdata: seasonal opportunity data from get_opp_data.
    """
    img_paths = create_article_images(
        size_key=image_size_key,
        resource_id=resource_id,
        date=date,
        symbol=symbol,
        days=days,
        years=years,
        theme=theme,
    )

    cdata = get_opp_data(resource_id, date, symbol, days, years, True)
    return img_paths, cdata


# ----------------------------------------------------------------------
# Step 2: Hero image
# ----------------------------------------------------------------------

def generate_hero_image(resource_id: str,
                        symbol: str,
                        date: str,
                        img_paths: List[Dict[str, Any]],
                        direction: str = "long",
                        article_id: str = "") -> Tuple[str, List[Dict[str, Any]]]:
    """
    Generate hero image HTML and append hero entry to img_paths.
    Returns:
        hero_html: HTML <figure> block or empty string.
        img_paths: updated list including hero variant on success.
    """
    hero_html = ""
    try:
        sentiment = "bullish" if direction == "long" else "bearish" if direction == "short" else "neutral"
        hero_info = hero_image_workflow(resource_id=str(resource_id), symbol=symbol, date=date, sentiment=sentiment, article_id=article_id)
        if hero_info and hero_info.get("image_url"):
            alt_text = f"{get_company_name(resource_id, symbol) or symbol} ({symbol}) market analysis and seasonal trends - TradeWave.ai"
            hero_html = (
                f'<figure class="hero">'
                f'<img src="{hero_info["image_url"]}" '
                f'width="{HERO_WIDTH_ATTR}" height="{HERO_HEIGHT_ATTR}" '
                f'alt="{alt_text}"></figure>'
            )
            img_paths.append(
                {
                    "variant": "hero",
                    "url": hero_info["image_url"],
                    "path": hero_info.get("image_path", ""),
                    "rel": "",
                    "alt": alt_text,
                }
            )
            print(f"[SUCCESS] Hero image generated: {hero_info['image_url']}")
        else:
            print("[WARN] No hero image generated or missing URL.")
    except Exception as e:
        print(f"[WARN] Hero image generation failed (non-fatal): {e}")
        raise  # re-raise so the caller can mark the step accurately
    return hero_html, img_paths


# ----------------------------------------------------------------------
# Step 3: Research with Tavily + Grok
# ----------------------------------------------------------------------

def research_tavily(resource_id: str,
                    symbol: str,
                    company: str,
                    cdata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the Tavily + Grok research pipeline and return structured research JSON
    suitable for create_article_prompt.
    """
    import json as _json

    market_family, resource_name = detect_market_family(resource_id)
    print(f"\n--- STARTING INTELLIGENT RESEARCH PIPELINE ({market_family}) ---")

    print(f"[Grok] Identifying official data sources for {company} ({symbol})")
    specific_company_domains = AI_tools.get_company_domains_with_grok(symbol, company)
    print(f"[Grok] Identified official domains: {specific_company_domains}")
    article_audit.record("company_domains.json", {
        "symbol": symbol, "company": company, "domains": specific_company_domains})

    dynamic_whitelist = list(set(WHITELISTED_SOURCE_DOMAINS + specific_company_domains))

    query_a = f"{company} ({symbol}) stock price news earnings analyst ratings"
    query_b = f"{company} ({symbol}) insider trading unusual options short interest technical analysis"

    print(f"[Tavily] Running double search on {len(dynamic_whitelist)} trusted domains")
    resp_a = AI_tools.search_tavily(query=query_a, include_domains=dynamic_whitelist, days=365)
    resp_b = AI_tools.search_tavily(query=query_b, include_domains=dynamic_whitelist, days=365)

    combined_results = resp_a.get("results", []) + resp_b.get("results", [])
    tavily_resp = {"results": combined_results}
    raw_context_text = AI_tools.format_tavily_results(tavily_resp)
    count = len(combined_results)
    print(f"[Tavily] Retrieved {count} high quality sources (combined)")

    # if count == 0:
    #     print("[WARN] Strict search returned 0 results. Retrying with open search...")
    #     tavily_resp = AI_tools.search_tavily(query=query_a, include_domains=None, days=30)
    #     raw_context_text = AI_tools.format_tavily_results(tavily_resp)

    if count == 0:
        print("[WARN] Strict search returned 0 results. Retrying with open search...")

        resp_a = AI_tools.search_tavily(query=query_a, include_domains=None, days=365)
        resp_b = AI_tools.search_tavily(query=query_b, include_domains=None, days=365)

        combined_results = resp_a.get("results", []) + resp_b.get("results", [])
        tavily_resp = {"results": combined_results}

        raw_context_text = AI_tools.format_tavily_results(tavily_resp)

    article_audit.record("research_tavily_raw.json", {
        "queries": [query_a, query_b],
        "include_domains": dynamic_whitelist,
        "responses": [resp_a, resp_b],
    })
    article_audit.record("research_context.txt", raw_context_text)

    target_schema = f"""
    {{
      "symbol": "{symbol}",
      "company": "{company}",
      "market_family": "{market_family}",

      "price": {{
        "last": null,
        "change_percent": null,
        "ytd_percent": null,
        "range_52w_high": null,
        "range_52w_low": null
      }},

      "catalysts": [
        {{
          "type": "earnings/product/regulation/macro",
          "date": "YYYY-MM-DD or null",
          "headline": "Short headline",
          "summary": "1-2 sentences on impact.",
          "source_id": 1
        }}
      ],

      "earnings": {{
        "next_earnings_date": "YYYY-MM-DD or null",
        "fiscal_period": "e.g., Q3 2025",
        "recent_results": "Revenue/EPS vs est & key quotes.",
        "guidance": "Forward guidance summary.",
        "sources": []
      }},

      "analyst": {{
        "consensus_rating": "Buy/Hold/Sell",
        "price_target_consensus": null,
        "provider": "e.g. FactSet via CNBC",
        "sources": []
      }},

      "special_signals": {{
        "unusual_options": {{ "summary": "Search text for 'unusual options'. If none, null.", "source_id": null }},
        "insider_activity": {{ "summary": "Search text for 'insider trading'. If none, null.", "source_id": null }},
        "volume_spike": {{ "summary": "Search text for 'high volume'. If none, null.", "source_id": null }},
        "short_interest_change": {{ "summary": "Search text for 'short interest'. If none, null.", "source_id": null }}
      }},

      "macro": [ {{ "theme": "string", "summary": "string", "source_id": null }} ],
      "sector": [ {{ "theme": "string", "summary": "string", "source_id": null }} ],

      "equity_etf_index": {{ "etf_flows": [], "index_context": [] }},
      "futures_commodities": {{ "term_structure": {{}}, "positioning": {{}}, "inventory": {{}} }},

      "sources": [
        {{
          "id": 1,
          "publisher": "Name",
          "title": "Title",
          "url": "https://...",
          "date": "YYYY-MM-DD",
          "domain_tier": "1",
          "justification": "Why this source is trusted."
        }}
      ]
    }}
    """

    print("[Grok] Synthesizing research JSON from Tavily context")
    research_json_str = AI_tools.synthesize_research_with_grok(
        raw_text_context=raw_context_text,
        json_schema_str=target_schema,
        symbol=symbol,
        company=company,
    )

    article_audit.record("research_synthesis_raw.txt", research_json_str)
    clean = research_json_str.replace("```json", "").replace("```", "").strip()
    try:
        research_json = _json.loads(clean)
    except _json.JSONDecodeError:
        # Try extracting the first {...} block in case of leading/trailing prose
        import re as _re
        m = _re.search(r'\{.*\}', clean, _re.DOTALL)
        if m:
            research_json = _json.loads(m.group(0))
        else:
            raise ValueError(f"Grok returned unparseable research JSON: {clean[:200]}")
    print("RESEARCH JSON KEYS:", list(research_json.keys()))
    article_audit.record("research.json", research_json)
    return research_json


# ----------------------------------------------------------------------
# Step 4: Build article prompt
# ----------------------------------------------------------------------

def build_article_prompt(resource_id: str,
                         symbol: str,
                         date: str,
                         days: int,
                         years: str,
                         cdata: Dict[str, Any],
                         img_paths: List[Dict[str, Any]],
                         hero_html: str,
                         mode: str,
                         research: Optional[Dict[str, Any]],
                         pattern_mode: str = "consecutive") -> str:
    byline = "Analysis powered by the TradeWave quantitative engine."
    ai_disclosure = False
    variant_index = 1

    try:
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol

    # For PE patterns, convert plain years (e.g. '10') to the pe2-N format
    # (e.g. 'pe2-10') that create_article_prompt and the appserver both expect.
    # MUST be lowercase — appserver custom_year_filter does sv_code == 'pe2' (case-sensitive).
    if pattern_mode == 'pe':
        pe_phase = datetime.date.today().year % 4
        prompt_years = f'pe{pe_phase}-{years}'
    else:
        prompt_years = years

    prompt = create_article_prompt(
        symbol=symbol,
        date=date,
        days=days,
        years=prompt_years,
        cdata=cdata,
        img_paths=img_paths,
        company=company,
        resource_id=resource_id,
        variant_index=variant_index,
        byline=byline,
        ai_disclosure=ai_disclosure,
        hero_html=hero_html,
        mode=mode,
        research=research,
    )
    return prompt


# ----------------------------------------------------------------------
# Step 5: Write article with OpenAI
# ----------------------------------------------------------------------

def write_article(article_prompt_text: str, hero_html: str) -> str:
    """
    Call OpenAI through AI_tools and inject hero into final HTML.
    """
    article_html = AI_tools.send_openai_prompt(
        article_prompt_text,
        system=None,
        stream=False,
        temperature=0.0,
    )
    article_html = inject_hero_into_article(article_html, hero_html)
    return article_html


# ----------------------------------------------------------------------
# Step 6: Publish article
# ----------------------------------------------------------------------

def publish_article(resource_id: str,
                    symbol: str,
                    date: str,
                    days: int,
                    years: str,
                    direction: str,
                    article_publish_date: str,
                    article_html: str,
                    note: str,
                    userid: int,
                    hero_image: str = "") -> Dict[str, Any]:
    """
    Wrapper around existing publish logic.
    Adjust to match your article_publish.py API.
    """
    result = publish_article_web(
        resource_id=resource_id,
        symbol=symbol,
        date=date,
        days=days,
        years=years,
        direction=direction,
        userid=userid,
        article_html=article_html,
        hero_image=hero_image,
    )
    return result


# ----------------------------------------------------------------------
# Orchestrator: generate_news_article
# ----------------------------------------------------------------------

def generate_news_article(resource_id: str,
                          symbol: str,
                          date: str,
                          days: int,
                          years: str,
                          direction: str,
                          article_publish_date: Optional[str],
                          userid: int,
                          mode: str = "2",
                          pattern_mode: str = "consecutive",
                          note: str = "") -> Dict[str, Any]:
    """
    End to end workflow:
    1. Generate TradeWave charts
    2. Generate hero image
    3. Run Tavily + Grok research (for modes "1" or "2")
    4. Build article prompt
    5. Write article HTML with OpenAI
    6. Publish article

    Returns a tracking dict containing:
      - core identifiers
      - step statuses
      - final article_html and publish_result on success
    """

    start_time = time.time()

    tracking = init_tracking(
        resource_id=resource_id,
        symbol=symbol,
        date=date,
        days=days,
        years=years,
        direction=direction,
        article_publish_date=article_publish_date,
        mode=mode,
    )

    article_html: Optional[str] = None
    publish_result: Optional[Dict[str, Any]] = None
    article_id: str = secrets.token_hex(4)   # unique per article, used to avoid hero filename collisions

    # Write-only audit trail (observer only — can never affect generation).
    audit_trail = article_audit.begin({
        "article_id": article_id, "resource_id": str(resource_id), "symbol": symbol,
        "start_date": date, "days": days, "years": years, "direction": direction,
        "article_publish_date": article_publish_date, "mode": mode,
        "pattern_mode": pattern_mode,
    })

    # For PE patterns, convert plain years (e.g. '6') to pe2-N format (e.g. 'pe2-6').
    # Both charts AND the article prompt need this — charts use it for the ChartData4
    # API call which filters to PE-cycle years only.  Without it, charts show
    # consecutive data even when the pattern is PE-mode.
    # MUST be lowercase — appserver custom_year_filter does sv_code == 'pe2' (case-sensitive).
    if pattern_mode == 'pe':
        pe_phase = datetime.date.today().year % 4
        chart_years = f'pe{pe_phase}-{years}'
    else:
        chart_years = years

    try:
        # 1) TradeWave charts
        try:
            img_paths, cdata = generate_tradewave_charts(
                image_size_key="x",
                resource_id=resource_id,
                symbol=symbol,
                date=date,
                days=days,
                years=chart_years,
                theme="light",
            )
            mark_step_success(tracking, "generate_tradewave_charts")
        except Exception as e:
            mark_step_error(tracking, "generate_tradewave_charts", e)
            return tracking

        # 2) Hero image (non-fatal — article proceeds without hero on failure)
        hero_html = ""
        try:
            hero_html, img_paths = generate_hero_image(
                resource_id=resource_id,
                symbol=symbol,
                date=date,
                img_paths=img_paths,
                direction=direction,
                article_id=article_id,
            )
            mark_step_success(tracking, "generate_hero_image")
        except Exception as e:
            print(f"[ERROR] Hero image step failed; article will not publish: {e}")
            tracking["steps"]["generate_hero_image"]["status"] = "error"
            tracking["steps"]["generate_hero_image"]["error"] = str(e)
            try:
                failure_log = os.path.join(os.path.dirname(__file__), "logs", "hero_failures.jsonl")
                os.makedirs(os.path.dirname(failure_log), exist_ok=True)
                with open(failure_log, "a") as fh:
                    fh.write(json.dumps({
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "symbol": symbol,
                        "article_id": article_id,
                        "resource_id": resource_id,
                        "error": str(e),
                    }) + "\n")
            except Exception as log_error:
                print(f"[ERROR] Could not persist hero failure: {log_error}")
            tracking["status"] = "error"
            return tracking

        if not hero_html or not any(item.get("variant") == "hero" and item.get("path") and os.path.isfile(item.get("path")) for item in img_paths):
            error = RuntimeError("Hero image missing after all provider attempts")
            print(f"[ERROR] {error}; article will not publish")
            tracking["steps"]["generate_hero_image"]["status"] = "error"
            tracking["steps"]["generate_hero_image"]["error"] = str(error)
            try:
                failure_log = os.path.join(os.path.dirname(__file__), "logs", "hero_failures.jsonl")
                os.makedirs(os.path.dirname(failure_log), exist_ok=True)
                with open(failure_log, "a") as fh:
                    fh.write(json.dumps({
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "symbol": symbol,
                        "article_id": article_id,
                        "resource_id": resource_id,
                        "error": str(error),
                    }) + "\n")
            except Exception as log_error:
                print(f"[ERROR] Could not persist hero failure: {log_error}")
            tracking["status"] = "error"
            return tracking

        # 3) Research (only for modes "1" and "2")
        research = None
        if mode in ("1", "2"):
            try:
                company = get_company_name(resource_id, symbol) or symbol
                research = research_tavily(
                    resource_id=resource_id,
                    symbol=symbol,
                    company=company,
                    cdata=cdata,
                )
                article_audit.record("research.json", research)
                mark_step_success(tracking, "research_tavily")
            except Exception as e:
                mark_step_error(tracking, "research_tavily", e)
                return tracking
        else:
            mark_step_success(tracking, "research_tavily")

        # 4) Article prompt
        try:
            article_prompt_text = build_article_prompt(
                resource_id=resource_id,
                symbol=symbol,
                date=date,
                days=days,
                years=years,
                cdata=cdata,
                img_paths=img_paths,
                hero_html=hero_html,
                mode=mode,
                research=research,
                pattern_mode=pattern_mode,
            )
            article_audit.record("prompt.txt", article_prompt_text)
            mark_step_success(tracking, "article_prompt")
        except Exception as e:
            mark_step_error(tracking, "article_prompt", e)
            return tracking

        # 5) Write article
        try:
            article_html = write_article(article_prompt_text, hero_html)
            article_audit.record("article_llm_raw.html", article_html)

            # Replace LLM title with SEO-optimized title
            if research is not None:
                from article_title import generate_unique_seo_title
                pattern = {
                    'resource_id': resource_id,
                    'symbol': symbol,
                    'start_date': date,
                    'days': days,
                    'years': years,
                    'company': company,
                    'direction': direction,
                }
                new_title = generate_unique_seo_title(pattern, article_html, tavily=research, persist=True)
                print(f"[SEO TITLE] Generated: {new_title}")
                article_html = _replace_title_in_html(article_html, new_title)

            tracking["article_html"] = article_html
            mark_step_success(tracking, "write_article")

        except Exception as e:
            import traceback
            traceback.print_exc()  # This will print the full stack trace
            mark_step_error(tracking, "write_article", e)
            return tracking

        # 5b) Citation gate — advisory in 'warn'/'off' (NEVER aborts a generation);
        # only an explicit 'block'-mode failure aborts before publish.
        try:
            import config as _cfg
            _cg_mode = getattr(_cfg, "citation_gate_mode", "warn")
        except Exception:
            _cfg = None; _cg_mode = "off"
        try:
            if _cg_mode != "off":
                from citation_gate import validate_citations
                cg = validate_citations(article_html, research=research, symbol=symbol, company=company,
                                        check_liveness=getattr(_cfg, "citation_check_liveness", False))
                article_audit.record("citation_gate.json", cg)
                if cg.get("violations"):
                    print(f"[CITATION GATE] {symbol}: {len(cg['violations'])} violation(s): {cg['violations'][:5]}")
                if cg.get("warnings"):
                    print(f"[CITATION GATE] {symbol}: warnings: {cg['warnings'][:5]}")
                if not cg.get("ok") and _cg_mode == "block":
                    raise ValueError("citation gate failed: " + "; ".join(cg.get("violations", [])[:5]))
            mark_step_success(tracking, "citation_gate")
        except Exception as e:
            if _cg_mode == "block":
                mark_step_error(tracking, "citation_gate", e)
                return tracking
            # advisory mode: log + mark skipped, but let the article publish
            print(f"[CITATION GATE] non-fatal ({_cg_mode}) error for {symbol}: {e}")
            try:
                tracking["steps"]["citation_gate"]["status"] = "skipped"
                tracking["steps"]["citation_gate"]["error"] = str(e)
            except Exception:
                pass

        # 6) Publish article — use chart_years (PE-qualified) so the Redis key
        # matches what the portfolio page uses for the article_exists check.
        hero_url = next((p["url"] for p in img_paths if p.get("variant") == "hero"), "")
        article_audit.record("final.html", article_html)
        try:
            publish_result = publish_article(
                resource_id=resource_id,
                symbol=symbol,
                date=date,
                days=days,
                years=chart_years,
                direction=direction,
                article_publish_date=tracking["article_publish_date"],
                article_html=article_html,
                note=note,
                userid=userid,
                hero_image=hero_url,
            )
            tracking["publish_result"] = publish_result
            article_audit.record("publish_result.json", publish_result)
            mark_step_success(tracking, "publish_article")
        except Exception as e:
            mark_step_error(tracking, "publish_article", e)
            return tracking


        try:
            from refresh_related_articles import refresh_today_only
            print("[INFO] Refreshing related articles for today's articles...")
            refresh_today_only(dry_run=False)
        except Exception as e:
            print(f"[WARN] Failed to refresh today's related articles: {e}")

        

        tracking["status"] = "success"
        tracking["duration_seconds"] = round(time.time() - start_time, 0)
        return tracking

    except Exception as e:
        tracking["status"] = "error"
        tracking["error_message"] = str(e)
        return tracking
    finally:
        # Persist the audit trail on every exit path (success, error, exception).
        # finish() never raises; a failed audit write only logs a warning.
        article_audit.finish(audit_trail, tracking)

#--------------------------------------------------------------------------------------------
if __name__ == "__main__":

    print("\n================= ARTICLE WORKFLOW SMOKE TEST =================\n")


    resource_id = "2"
    symbol = "GOOG"
    date = "2025-12-03"
    days = 62
    years = "10"
    article_publish_date = "2025-12-14"
    direction = 'long'
    userid = 1
    mode = "2"
    note = "smoke_test"


    tracking = generate_news_article(
        resource_id=resource_id,
        symbol=symbol,
        date=date,
        days=days,
        years=years,
        direction=direction,
        article_publish_date=article_publish_date,
        userid=userid,
        mode=mode,
        note=note
    )

    print("\n================= TRACKING RESULT =================\n")
    print(json.dumps(tracking, indent=2))
    print("\n================= END SMOKE TEST ===================\n")
