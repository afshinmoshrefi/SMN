"""angle_pipeline.py — production orchestration for angle-engine articles (Phase 3).

Mirrors article_workflow.generate_news_article but for the angle path, reusing
the existing workflow steps wherever they exist:

  matrix + Angle Card ......... angle_engine.analyze (TW2 auth via config)
  charts + captions ........... article_images.create_article_images +
                                article_prompt._build_image_manifest
  hero image .................. article_workflow.generate_hero_image
  research .................... article_workflow.research_tavily +
                                article_prompt._filter_research_sources +
                                article_prompt._annotate_research_temporal +
                                EODHD price override (get_price_eod)
  PLAN/WRITE/gates ............ angle_writer.generate_angle_article
                                (integrity + editorial in the bounded loop)
  veto fallback ............... angle_engine.fallback_card (exactly once)
  SEO title ................... article_title.generate_unique_seo_title
  audit trail ................. article_audit (observer-only, never blocks)
  publish ..................... publish_article.publish_article_web, gated by
                                BOTH the publish argument AND
                                config.angle_publish_enabled (default OFF)

Research failure is a designed degradation, not an error: the article is
generated with research=None (no external claims — the QUIET-style discipline)
and the fact is recorded. Hero failure with require_hero=True holds the piece,
matching the existing workflow's fatal-hero rule.
"""
from __future__ import annotations

import datetime
import json
import secrets
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, '/home/flask')
try:
    import config
except Exception:                                            # off-box tests
    class _ConfigStub:
        pass
    config = _ConfigStub()

import angle_engine
import angle_writer

# Write-only audit trail; a broken audit module must never stop generation
# (same guarded pattern as article_workflow).
try:
    import article_audit
except Exception:
    class article_audit:                                     # type: ignore
        @staticmethod
        def begin(identity):
            return None

        @staticmethod
        def record(name, value):
            pass

        @staticmethod
        def finish(trail, tracking):
            return None


def _direction_label(cell: Dict[str, Any]) -> str:
    return "long" if cell.get("direction") == "bullish" else "short"


def _prepare_research(resource_id: str, symbol: str, company: str,
                      cdata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The existing research path: Tavily -> source filter -> temporal
    annotation -> EODHD price override. Returns None on any failure."""
    try:
        from article_workflow import research_tavily
        from article_prompt import (_filter_research_sources,
                                    _annotate_research_temporal)
        research = research_tavily(resource_id=resource_id, symbol=symbol,
                                   company=company, cdata=cdata)
        research = _filter_research_sources(research, symbol=symbol, company=company)
        research = _annotate_research_temporal(research, freshness_days=60)
        try:
            from get_price_eod import get_quote_details
            exchange = config.exchange_mapping.get(str(resource_id), "US")
            quote = get_quote_details(symbol, exchange, use_realtime=False)
            if quote and quote.get("close"):
                research.setdefault("price", {})
                research["price"]["last"] = quote["close"]
                research["price"]["change_percent"] = quote.get("change_p")
                research["price"]["source"] = "EODHD"
        except Exception as exc:
            print(f"[angle_pipeline] EODHD override skipped: {exc}")
        # Zero usable sources = no research: every external claim would be
        # uncitable (2026-07-21 XLK batch run was held for exactly this).
        # Returning None switches the writer to the no-external-claims mode.
        if not (research or {}).get("sources"):
            print("[angle_pipeline] research returned no usable sources; "
                  "generating with no external claims")
            return None
        return research
    except Exception as exc:
        print(f"[angle_pipeline] research unavailable ({exc}); "
              "generating with no external claims")
        return None


def _chart_images(resource_id: str, cell: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Story-cell charts via the existing chart engine, with the existing
    caption machinery."""
    from article_images import create_article_images
    from article_prompt import _build_image_manifest
    img_paths = create_article_images("x", str(resource_id), cell["anchor_date"],
                                      cell["symbol"], str(cell["days"]),
                                      cell["years"], "light")
    return _build_image_manifest(img_paths, cell["symbol"], cell["years"])


def generate_angle_news_article(resource_id: str, symbol: str, *,
                                anchor: Optional[str] = None,
                                news_headline: str = "",
                                news_date: str = "",
                                news_direction: str = "",
                                detected: Optional[List[Dict[str, Any]]] = None,
                                userid: int = 28,
                                publish: bool = False,
                                require_hero: bool = True,
                                run_editorial: bool = True,
                                angle_index: int = 0,
                                send_plan=None, send_write=None,
                                editorial_send=None) -> Dict[str, Any]:
    """End-to-end angle article. Returns the artifacts dict from angle_writer
    plus pipeline fields (research_used, hero_url, publish_result, timings).
    Publishing requires publish=True AND config.angle_publish_enabled."""
    start_time = time.time()
    anchor = anchor or datetime.date.today().isoformat()
    article_id = secrets.token_hex(4)

    try:
        from blog_tools import get_company_name
        company = get_company_name(resource_id, symbol) or symbol
    except Exception:
        company = symbol

    # ---- 1) Matrix + Angle Card ----
    analysis = angle_engine.analyze(resource_id, symbol, anchor,
                                    news_headline=news_headline,
                                    news_date=news_date,
                                    news_direction=news_direction,
                                    detected=detected)
    card = analysis["card"]
    # angle_index>0 re-cards on a lower-ranked candidate. Used to publish the
    # SAME symbol/day from a genuinely different (still eligible) story cell.
    if angle_index:
        _alt = angle_engine.fallback_card(analysis, angle_index)
        if _alt is None:
            return {"status": "no_alt_angle",
                    "detail": f"no candidate at index {angle_index}",
                    "symbol": symbol, "card": card}
        card = _alt
    trail = article_audit.begin({
        "article_id": article_id, "engine": "angle",
        "resource_id": str(resource_id), "symbol": symbol,
        "anchor_date": anchor, "angle": (card.get("angle") or {}).get("name"),
        "article_publish_date": datetime.date.today().isoformat(),
    })
    article_audit.record("angle_card.json", card)
    if not card.get("angle"):
        return {"status": "no_story", "detail": card.get("no_story", ""),
                "symbol": symbol, "card": card}
    cell = card["story_cell"]

    # ---- 2) Charts + captions (story cell) ----
    try:
        images = _chart_images(resource_id, cell)
    except Exception as exc:
        return {"status": "error", "detail": f"chart generation failed: {exc}",
                "symbol": symbol, "card": card}

    # ---- 3) Hero (existing workflow step; fatal per require_hero) ----
    hero_url = ""
    try:
        from article_workflow import generate_hero_image
        raw_paths = [dict(i) for i in images]
        hero_html, raw_paths = generate_hero_image(
            resource_id=str(resource_id), symbol=symbol,
            date=cell["anchor_date"], img_paths=raw_paths,
            direction=_direction_label(cell), article_id=article_id)
        hero_url = next((p.get("url", "") for p in raw_paths
                         if p.get("variant") == "hero"), "")
    except Exception as exc:
        if require_hero:
            return {"status": "hold", "detail": f"hero generation failed: {exc}",
                    "symbol": symbol, "card": card}
        print(f"[angle_pipeline] continuing without hero: {exc}")

    # ---- 4) Research (designed degradation on failure) ----
    research = _prepare_research(str(resource_id), symbol, company,
                                 {"stats": cell.get("stats_raw") or {}})
    article_audit.record("research.json", research)

    # ---- 5) PLAN -> WRITE -> gates (one veto fallback allowed) ----
    try:
        from blog_tools import convert_param_base64
        param = convert_param_base64(resource_id, symbol, cell["anchor_date"],
                                     cell["days"], cell["years"])
        cta_link = f"{getattr(config, 'domain_root', '')}wave-viewer?o={param}"
    except Exception:
        cta_link = ""
    methodology_url = (getattr(config, "news_website_url", "").rstrip('/')
                       + "/methodology.html"
                       if getattr(config, "news_website_url", "") else "")
    common = dict(research=research, images=images, hero_url=hero_url,
                  company=company,
                  byline="Analysis powered by the TradeWave quantitative engine.",
                  cta_link=cta_link,
                  methodology_url=methodology_url,
                  book_url=getattr(config, "book_amazon_url", ""),
                  run_editorial=run_editorial, send_plan=send_plan,
                  send_write=send_write, editorial_send=editorial_send)
    result = angle_writer.generate_angle_article(card, **common)
    if result.get("status") == "vetoed":
        fb = angle_engine.fallback_card(analysis)
        article_audit.record("veto.json", {"reason": result.get("detail"),
                                           "fallback": bool(fb)})
        if fb is None:
            result["status"] = "hold"
        else:
            card = fb
            article_audit.record("angle_card_fallback.json", card)
            result = angle_writer.generate_angle_article(card, **common)
            if result.get("status") == "vetoed":     # once, never more
                result["status"] = "hold"

    for name, key in (("plan.json", "plan"), ("prose.html", "prose"),
                      ("prose_revised.html", "prose_revised"),
                      ("gate1.json", "gate1"), ("gate2.json", "gate2")):
        if result.get(key) is not None:
            article_audit.record(name, result[key])

    result.update(symbol=symbol, company=company, article_id=article_id,
                  research_used=research is not None, hero_url=hero_url,
                  anchor_date=anchor)

    # ---- 6) SEO title (non-fatal) ----
    if result.get("status") == "ready" and result.get("html"):
        try:
            from article_title import generate_unique_seo_title
            from article_workflow import _replace_title_in_html
            pattern = {"resource_id": resource_id, "symbol": symbol,
                       "start_date": cell["anchor_date"], "days": cell["days"],
                       "years": cell["years"], "company": company,
                       "direction": _direction_label(cell)}
            new_title = generate_unique_seo_title(pattern, result["html"],
                                                  tavily=research, persist=True)
            result["html"] = _replace_title_in_html(result["html"], new_title)
            result["seo_title"] = new_title
        except Exception as exc:
            print(f"[angle_pipeline] SEO title step skipped: {exc}")

    article_audit.record("final.html", result.get("html", ""))

    # ---- 7) Publish (double-gated; default OFF) ----
    publish_enabled = bool(getattr(config, "angle_publish_enabled", False))
    if result.get("status") == "ready" and publish and publish_enabled:
        try:
            from publish_article import publish_article_web
            result["publish_result"] = publish_article_web(
                resource_id=resource_id, symbol=symbol,
                date=cell["anchor_date"], days=cell["days"],
                years=cell["years"], direction=_direction_label(cell),
                userid=userid, article_html=result["html"],
                hero_image=hero_url)
            article_audit.record("publish_result.json", result["publish_result"])
        except Exception as exc:
            result.update(status="error", detail=f"publish failed: {exc}")
    elif result.get("status") == "ready" and publish and not publish_enabled:
        result["publish_skipped"] = "config.angle_publish_enabled is off"

    result["duration_seconds"] = round(time.time() - start_time, 1)
    article_audit.finish(trail, {"status": result.get("status"),
                                 "duration_seconds": result["duration_seconds"]})
    return result
