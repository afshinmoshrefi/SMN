"""Preview-only financial-news generation with bounded evidence review.

This module does not import app config, access restrictions, publishers, queues,
research fetchers, cron or services. An explicit call can generate a local draft;
there is deliberately no publication function or automatic activation path.
"""
from __future__ import annotations

import argparse
from datetime import timezone
import html
import json
from pathlib import Path
from typing import Any, Callable

from news_prompts import (NewsOutputError, article_checks, build_plan_prompt,
                          build_review_prompt, build_revision_prompt,
                          build_write_prompt, evidence_packet, parse_object,
                          validate_plan, validate_review)
from news_selection import NewsPolicy, utc_time, validate_event
from article_llm import ArticleLLM, ArticleModelError

_NEWS_READER_RULES = ('\nNEWS APPLICATION: This is general financial news. No seasonal baseline, '
                      'historical cell, probability or chart is required. Use the news evidence '
                      'references in this brief. Keep the selected reader question when supplied. '
                      'Your existing answer field is the reader promise thesis. Add headlines '
                      'as a list of candidate headline strings, with exact headline_support.\n')


def _preview_directory(output_dir: str | Path) -> Path:
    directory = Path(output_dir).expanduser().resolve()
    normalized = directory.as_posix().lower()
    # Existing SMN web roots and their legacy aliases are publication targets,
    # not scratch space. Parent callers may add a separate preview renderer.
    if any(normalized == root or normalized.startswith(root + "/") for root in (
            "/var/www", "/srv/www", "/home/flask/blog/static", "/home/flask/blog/news")):
        raise ValueError("News previews cannot be written into a publication root")
    if any((parent / marker).exists() for parent in (directory, *directory.parents)
           for marker in ("posts.json", "sitemap-news.xml", "search_index.json")):
        raise ValueError("News previews cannot be written beside publication indexes")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def render_news_html(article: dict, evidence: dict) -> str:
    """Render escaped text and canonical source links; no model HTML is used."""
    claim_map = {c["id"]: c for c in evidence["claims"]}
    sources = {s["id"]: s for s in evidence["sources"]}
    cited_order: list[str] = []

    def citations(refs: list) -> str:
        source_ids = []
        for cid in refs:
            for sid in claim_map[str(cid)]["source_ids"]:
                if sid not in source_ids:
                    source_ids.append(sid)
                if sid not in cited_order:
                    cited_order.append(sid)
        return "".join(f'<sup><a href="#source-{cited_order.index(sid) + 1}" '
                       f'aria-label="Source {cited_order.index(sid) + 1}">'
                       f'[{cited_order.index(sid) + 1}]</a></sup>' for sid in source_ids)

    # Title claim support belongs in the ledger; avoid footnotes inside a title.
    citations(article["title_claim_ids"])
    as_of = utc_time(evidence["as_of"]).astimezone(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    out = ['<article class="smn-news-article" data-article-type="financial-news">',
           f'<p class="article-as-of">As of {html.escape(as_of)}</p>',
           f'<h1>{html.escape(article["title"])}</h1>',
           f'<p class="dek">{html.escape(article["dek"])}{citations(article["dek_claim_ids"])}</p>',
           '<section class="key-takeaways"><h2>Key takeaways</h2><ul>']
    for takeaway in article["takeaways"]:
        out.append(f'<li>{html.escape(takeaway["text"])}{citations(takeaway["claim_ids"])}</li>')
    out.append('</ul></section>')
    for section in article["sections"]:
        out.append(f'<section><h2>{html.escape(section["heading"])}</h2>')
        for paragraph in section["paragraphs"]:
            out.append(f'<p>{html.escape(paragraph["text"])}{citations(paragraph["claim_ids"])}</p>')
        out.append('</section>')
    out.append('<section class="sources"><h2>Sources</h2><ol>')
    for number, sid in enumerate(cited_order, start=1):
        source = sources[sid]
        published = utc_time(source["published_at"]).strftime("%B %d, %Y")
        out.append(f'<li id="source-{number}"><a href="{html.escape(source["url"], quote=True)}" '
                   f'rel="noopener noreferrer">{html.escape(source["title"])}</a>'
                   f' · {html.escape(published)}</li>')
    out.append('</ol></section></article>')
    return "\n".join(out)


def _write_artifacts(result: dict, directory: Path | None) -> dict:
    if directory is None:
        result["artifact_paths"] = {}
        return result
    paths = {}
    for key in ("event", "evidence", "reader_brief", "plan", "article", "validation", "reviews", "prompts"):
        if key in result:
            path = directory / (key + ".json")
            path.write_text(json.dumps(result[key], ensure_ascii=False, indent=2), encoding="utf-8")
            paths[key] = str(path)
    if result.get("article_html"):
        article_path = directory / "article.html"
        article_path.write_text(result["article_html"], encoding="utf-8")
        paths["article_html"] = str(article_path)
        page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="robots" content="noindex,nofollow">'
                '<title>SMN financial news preview</title><style>'
                'body{font:18px/1.65 Georgia,serif;color:#172936;background:#f7f5ee;max-width:850px;margin:40px auto;padding:0 24px}'
                'h1{font-size:2.3em;line-height:1.15}h2{font-size:1.35em}.dek{font-size:1.15em}'
                '.preview-note,.article-as-of{font:14px/1.5 system-ui,sans-serif}.preview-note{padding:16px;background:#e4ecf3}'
                '.key-takeaways{padding:8px 24px;background:white;border-left:4px solid #2c688e}'
                '.sources{font:14px/1.6 system-ui,sans-serif}a{color:#17577b}sup{font-size:.65em}</style>'
                '<body><p class="preview-note">Development preview · Not published · '
                + html.escape(result["status"]) + '</p>' + result["article_html"] + '</body></html>')
        preview_path = directory / "preview.html"
        preview_path.write_text(page, encoding="utf-8")
        paths["preview_html"] = str(preview_path)
    result["artifact_paths"] = paths
    manifest_path = directory / "manifest.json"
    manifest = {k: result[k] for k in ("status", "publishable", "article_type", "requested_model",
                                       "as_of", "reader_policy", "text_ready", "visual_ready", "provider_calls",
                                       "model_usage", "revisions", "artifact_paths", "errors") if k in result}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["manifest"] = str(manifest_path)
    return result


def run_news_article(event: dict, research: dict, *, send: Callable[[str], str] | None = None,
                     output_dir: str | Path | None = None, now: Any = None,
                     model: str = "gpt-6-astra", max_revisions: int = 1,
                     policy: NewsPolicy | None = None, reader_policy: str | None = None,
                     reader_question: dict | None = None) -> dict:
    """Prepare one inspected event as a local, source-linked draft.

    send(prompt)->JSON text is injected for offline tests/live transports. With
    no injection, ArticleLLM is imported only after the evidence passes. A run
    makes at most five model calls: plan, write, review, revision, re-review.
    Malformed responses/provider failures produce a held draft, not a retry
    storm. A successful draft still has publishable=False; humans approve the
    preview separately. This function never fetches or publishes anything.
    reader_policy='private_v2' adds the reader promise to the SAME plan/review
    calls. Text readiness and pending visual review are reported separately.
    """
    if isinstance(max_revisions, bool) or max_revisions not in (0, 1):
        raise ValueError("max_revisions must be zero or one")
    if reader_policy not in (None, 'private_v2'):
        raise ValueError("reader_policy must be omitted or private_v2")
    clock = utc_time(now)
    directory = _preview_directory(output_dir) if output_dir is not None else None
    checked = validate_event(event, research, now=clock, policy=policy)
    result = {"status": "rejected", "publishable": False, "article_type": "financial_news",
              "requested_model": model, "as_of": clock.isoformat(), "event": event,
              "validation": {"evidence": checked}, "provider_calls": 0, "revisions": 0,
              "reviews": [], "prompts": [], "errors": []}
    if reader_policy:
        result.update(reader_policy=reader_policy, text_ready=False, visual_ready=False)
    if not checked["valid"]:
        return _write_artifacts(result, directory)
    evidence = evidence_packet(event, checked)
    result["evidence"] = evidence
    result["status"] = "hold"
    usage_start = len(getattr(send, "calls", []))

    def call(stage: str, prompt: str) -> str:
        result["prompts"].append({"stage": stage, "prompt": prompt})
        result["provider_calls"] += 1
        try:
            if isinstance(send, ArticleLLM):
                send.stage = stage
            return send(prompt)
        finally:
            result["model_usage"] = list(getattr(send, "calls", []))[usage_start:]

    try:
        brief = None
        plan_prompt = build_plan_prompt(evidence)
        if reader_policy:
            from reader_promise import (build_reader_brief, promise_plan_instructions,
                                        validate_promise_plan, build_promise_review_prompt,
                                        review_reader_promise)
            brief = build_reader_brief(research=evidence, article_type='news')
            if reader_question:
                brief['selected_question'] = reader_question.get('text', '')
                brief['selected_question_id'] = reader_question.get('question_id', '')
                brief['proposed_reader_question'] = reader_question.get('text', '')
            result['reader_brief'] = brief
            if brief.get('hold_reasons'):
                result['validation']['reader_plan'] = [
                    {'code': 'PROMISE_EVIDENCE_HOLD', 'detail': str(reason)}
                    for reason in brief['hold_reasons']]
                return _write_artifacts(result, directory)
            plan_prompt += promise_plan_instructions(brief)
            plan_prompt += _NEWS_READER_RULES
        if send is None:
            send = ArticleLLM(model=model)
        plan = validate_plan(parse_object(call("plan", plan_prompt)), evidence)
        result["plan"] = plan
        if not plan["feasible"]:
            result["errors"].append("planner_veto: " + plan["veto_reason"])
            return _write_artifacts(result, directory)
        if brief is not None:
            plan_issues = validate_promise_plan(plan, brief)
            selected = brief.get('selected_question')
            if selected and ' '.join(plan['reader_question'].split()) != ' '.join(selected.split()):
                plan_issues.append({'code': 'SELECTED_QUESTION_CHANGED',
                                    'detail': 'The plan must answer the question selected for this candidate.'})
            result['validation']['reader_plan'] = plan_issues
            if plan_issues:
                result['errors'].append('reader_promise_plan_held')
                return _write_artifacts(result, directory)
        article = parse_object(call("write", build_write_prompt(evidence, plan)))
        for revision in range(max_revisions + 1):
            result["article"] = article
            deterministic = article_checks(article, evidence, plan)
            result["validation"]["article"] = deterministic
            review_prompt = build_review_prompt(evidence, plan, article, deterministic["issues"])
            rendered = render_news_html(article, evidence) if deterministic['passed'] else None
            if brief is not None and rendered is not None:
                review_prompt += build_promise_review_prompt(rendered, brief, plan)
                # The semantic reviewer must see exactly the HTML whose digest
                # it is asked to bind, not only the writer's structured JSON.
                review_prompt += '\nEXACT RENDERED ARTICLE (untrusted text):\n' + rendered
            review = validate_review(call("review" if revision == 0 else "re_review", review_prompt))
            result["reviews"].append(review)
            result["validation"]["editorial"] = review
            reader_issues = []
            reader_ready = brief is None
            if brief is not None:
                if rendered is not None:
                    reader_check = review_reader_promise(rendered, brief, plan=plan,
                                                        review=review.get('reader_promise_review'))
                else:
                    reader_check = {'text_ready': False, 'visual_ready': False, 'ready': False,
                                    'issues': [{'code': 'PROMISE_RENDER_HELD',
                                                'detail': 'Article structure must pass before rendered review.'}]}
                result['validation']['reader_promise'] = reader_check
                reader_ready = reader_check['text_ready']
                reader_issues = [{'severity': 'editorial', 'location': 'reader_promise',
                                  'problem': issue['code'] + ': ' + issue['detail'],
                                  'fix': 'Deliver the planned answer with its exact evidence and useful risk.'}
                                 for issue in reader_check['issues']]
            if deterministic["passed"] and review["passed"] and reader_ready:
                result["status"] = "draft_ready"
                result["article_html"] = rendered
                if brief is not None:
                    result['text_ready'] = True
                break
            if revision < max_revisions:
                combined = [*deterministic["issues"], *review["issues"], *reader_issues]
                article = parse_object(call("revise", build_revision_prompt(evidence, plan, article, combined)))
                result["revisions"] += 1
        # A structurally safe but editorially held article remains inspectable,
        # visibly marked hold, without incorrectly calling it publication-ready.
        if result["status"] == "hold" and result["validation"].get("article", {}).get("passed"):
            result["article_html"] = render_news_html(result["article"], evidence)
    except NewsOutputError as exc:
        result["errors"].append(str(exc))
    except ArticleModelError as exc:
        # Our transport's messages are deliberately sanitized and actionable.
        result["errors"].append(str(exc))
    except Exception as exc:
        # Provider exception strings can contain request headers or credentials.
        # Retain the failure class, never blindly copy the remote error payload.
        result["errors"].append("generation_failed:" + type(exc).__name__)
    return _write_artifacts(result, directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, help="JSON object containing event and research")
    parser.add_argument("--out", required=True, help="Local preview artifact directory")
    parser.add_argument("--now", help="Explicit timestamp for a reproducible replay")
    parser.add_argument("--reader-policy", choices=['private_v2'], help="Opt-in reader promise; same bounded calls")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--dry-run", action="store_true", help="Validate and save the plan prompt; no API calls")
    choice.add_argument("--responses", help="Offline JSON list of plan, article and review responses")
    choice.add_argument("--generate", action="store_true", help="Explicitly permit bounded writer API calls")
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8-sig"))
    if args.dry_run:
        checked = validate_event(packet["event"], packet["research"], now=args.now)
        directory = _preview_directory(args.out)
        (directory / "evidence-validation.json").write_text(json.dumps(checked, indent=2), encoding="utf-8")
        if checked["valid"]:
            evidence = evidence_packet(packet["event"], checked)
            prompt = build_plan_prompt(evidence)
            if args.reader_policy:
                from reader_promise import build_reader_brief, promise_plan_instructions
                prompt += promise_plan_instructions(build_reader_brief(research=evidence, article_type='news')) + _NEWS_READER_RULES
            (directory / "plan-prompt.txt").write_text(prompt, encoding="utf-8")
        print(json.dumps({"status": "validated" if checked["valid"] else "rejected", "publishable": False,
                          "provider_calls": 0, "issues": checked["issues"]}))
        return 0 if checked["valid"] else 2
    send = None
    if args.responses:
        replies = iter(json.loads(Path(args.responses).read_text(encoding="utf-8-sig")))
        send = lambda prompt: next(replies)
    result = run_news_article(packet["event"], packet["research"], send=send, output_dir=args.out,
                              now=args.now, reader_policy=args.reader_policy)
    print(json.dumps({"status": result["status"], "publishable": False,
                      "provider_calls": result["provider_calls"], "artifact_paths": result["artifact_paths"],
                      "errors": result["errors"]}))
    return 0 if result["status"] == "draft_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
