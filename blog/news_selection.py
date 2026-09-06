"""Auditable event selection for the *disabled*, preview-only news workflow.

No fetching, LLM, config, database or publication imports. Callers supply evidence
they have inspected. ``verified=True`` records that upstream inspection; this
module checks the evidence contract, not the truth of a remote webpage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class NewsPolicy:
    enabled: bool = False
    automatic_publication: bool = False
    baseline_limit: int = 2
    major_event_overflow: int = 2
    max_event_age_hours: int = 48
    max_source_age_hours: int = 96
    max_verification_age_hours: int = 48
    minimum_score: float = 55.0
    second_story_minimum: float = 65.0


def utc_time(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Use a timestamp with an explicit timezone")
    return value.astimezone(timezone.utc)


def canonical_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            return ""
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                           parsed.path.rstrip("/"), parsed.query, ""))
    except ValueError:
        return ""


def _list_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(x) for x in value if isinstance(x, (str, int))
                             and not isinstance(x, bool) and str(x).strip()))


def _score_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
        return score if math.isfinite(score) and 0 <= score <= 5 else None
    except (ValueError, TypeError):
        return None


def _has_grounded_development(event: dict, validation: dict, existing: dict) -> bool:
    """Require changed source evidence and newly covered grounded text.

    Re-extracting a different quote from an unchanged article is not a new
    development. Legacy coverage without evidence snapshots remains skipped
    for equal date-only timestamps instead of guessing that new facts exist.
    """
    source_map = {s["id"]: s for s in validation["sources"]}
    selected = {str(cid) for cid in event.get("claim_ids", [])}
    claims = [c for c in validation["claims"] if c["id"] in selected]
    normalize = lambda value: re.sub(r"\s+", " ", value).strip()
    prior_hashes = existing.get("source_body_hashes")
    prior_texts = existing.get("covered_claim_texts")
    if (not isinstance(prior_hashes, dict) or not prior_hashes or not isinstance(prior_texts, list)
            or not prior_texts or not all(isinstance(text, str) and text.strip() for text in prior_texts)):
        return False
    previously_covered = [normalize(text) for text in prior_texts]
    new_evidence = False
    if not claims or len(claims) != len(selected):
        return False
    for claim in claims:
        support = claim.get("support")
        if not isinstance(support, list) or not support:
            return False
        matched = False
        for span in support:
            if not isinstance(span, dict) or str(span.get("source_id")) not in source_map:
                return False
            quote = span.get("quote")
            if (not isinstance(quote, str) or normalize(quote) != normalize(claim["text"])
                    or normalize(quote) not in normalize(source_map[str(span["source_id"])]["excerpt"])):
                return False
            source = source_map[str(span["source_id"])]
            url = source["url"]
            body_hash = source.get("provenance", {}).get("body_sha256")
            old_hash = prior_hashes.get(url)
            if (isinstance(body_hash, str) and re.fullmatch(r"[0-9a-f]{64}", body_hash)
                    and isinstance(old_hash, str) and re.fullmatch(r"[0-9a-f]{64}", old_hash)
                    and body_hash != old_hash
                    and (event.get("source_body_hashes") or {}).get(url) == body_hash
                    and not any(normalize(quote) in text for text in previously_covered)):
                new_evidence = True
            matched = True
        if not matched:
            return False
    identity = "\n".join(sorted(normalize(c["text"]) for c in claims))
    return (new_evidence and event.get("development_id") ==
            "development-" + hashlib.sha256(identity.encode()).hexdigest()[:16])


def validate_event(event: dict, research: dict, *, now: Any = None,
                   policy: NewsPolicy | None = None) -> dict:
    """Return accepted sources/claims and explicit rejection reasons.

    Source ``published_at`` describes the document. ``event_time`` describes
    what happened. Retrieval/verification timestamps cannot make old news new.
    Background sources may be older only when explicitly role='context'; they
    cannot verify the event's freshness on their own.
    """
    policy, clock = policy or NewsPolicy(), utc_time(now)
    issues: list[str] = []
    if not isinstance(event, dict) or not isinstance(research, dict):
        return {"valid": False, "issues": ["event_and_research_must_be_objects"],
                "sources": [], "claims": [], "source_issues": []}
    for key in ("event_id", "development_id", "headline", "significance_reason", "relevance_reason"):
        if not isinstance(event.get(key), str) or not event[key].strip():
            issues.append("missing_" + key)
    event_at = None
    try:
        event_at = utc_time(event.get("event_time")) if event.get("event_time") else None
        if event_at is None:
            issues.append("missing_event_time")
        elif event_at > clock + timedelta(minutes=5):
            issues.append("event_time_in_future")
        elif clock - event_at > timedelta(hours=policy.max_event_age_hours):
            issues.append("stale_event")
    except ValueError:
        issues.append("invalid_event_time")
    for key in ("significance", "audience_relevance"):
        if _score_value(event.get(key)) is None:
            issues.append("invalid_" + key)
    if event.get("material_development") is not True:
        issues.append("no_material_new_development")
    if event.get("event_time_basis") not in ("reported_event", "official_release"):
        issues.append("event_time_must_describe_reported_event_or_official_release")

    raw_sources, raw_claims = research.get("sources"), research.get("claims")
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 12:
        issues.append("sources_must_contain_one_to_twelve_items")
        raw_sources = []
    if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 80:
        issues.append("claims_must_contain_one_to_eighty_items")
        raw_claims = []
    accepted, source_issues, seen_ids, seen_urls = [], [], set(), set()
    evidence_characters = 0
    for source in raw_sources:
        if not isinstance(source, dict):
            source_issues.append({"id": None, "issues": ["source_must_be_object"]})
            continue
        source_id = str(source.get("id", ""))
        problems = []
        url = canonical_url(source.get("url"))
        excerpt = source.get("excerpt") or source.get("content")
        if not source_id or source_id in seen_ids:
            problems.append("missing_or_duplicate_source_id")
        if not url or url in seen_urls:
            problems.append("invalid_or_duplicate_source_url")
        if source.get("verified") is not True:
            problems.append("source_not_verified")
        if source.get("source_type") not in ("primary", "reporting"):
            problems.append("unsupported_source_type")
        if not isinstance(source.get("title"), str) or not source["title"].strip():
            problems.append("missing_source_title")
        if not isinstance(excerpt, str) or len(excerpt.strip()) < 30:
            problems.append("source_evidence_missing")
        elif len(excerpt) > 18000:
            problems.append("source_excerpt_exceeds_18000_characters")
        else:
            evidence_characters += len(excerpt)
        for field in ("published_at", "verified_at"):
            try:
                stamp = utc_time(source[field])
                if stamp > clock + timedelta(minutes=5):
                    problems.append(field + "_in_future")
                max_age = (policy.max_verification_age_hours if field == "verified_at"
                           else policy.max_source_age_hours)
                if clock - stamp > timedelta(hours=max_age) and not (
                        field == "published_at" and source.get("role") == "context"):
                    problems.append("stale_" + field)
            except (KeyError, TypeError, ValueError):
                problems.append("missing_or_invalid_" + field)
        if source.get("page_type") in ("generic", "quote", "estimates", "search", "landing"):
            problems.append("generic_page_is_not_event_evidence")
        seen_ids.add(source_id)
        if url:
            seen_urls.add(url)
        if problems:
            source_issues.append({"id": source_id, "issues": problems})
        else:
            accepted.append({**source, "id": source_id, "url": url, "excerpt": excerpt.strip()})

    if evidence_characters > 60000:
        issues.append("evidence_packet_exceeds_60000_characters")

    source_map = {s["id"]: s for s in accepted}
    event_sources = _list_ids(event.get("source_ids"))
    if not event_sources or any(s not in source_map for s in event_sources):
        issues.append("event_references_missing_or_rejected_source")
    fresh_peg = [source_map[s] for s in event_sources if s in source_map
                 and source_map[s].get("role", "event") != "context"]
    if not fresh_peg:
        issues.append("no_fresh_event_source")
    # A primary release may establish an event alone. Otherwise require two
    # distinct reporting domains, not two syndications on the same website.
    domains = {urlsplit(s["url"]).hostname for s in fresh_peg}
    if fresh_peg and not any(s["source_type"] == "primary" for s in fresh_peg) and len(domains) < 2:
        issues.append("event_requires_primary_source_or_two_reporting_domains")

    claims, seen_claims = [], set()
    for claim in raw_claims:
        if not isinstance(claim, dict):
            issues.append("claim_must_be_object")
            continue
        cid, refs = str(claim.get("id", "")), _list_ids(claim.get("source_ids"))
        if (not cid or cid in seen_claims or not isinstance(claim.get("text"), str)
                or not claim["text"].strip() or len(claim["text"]) > 2000
                or not refs or any(s not in source_map for s in refs)):
            issues.append("invalid_or_unsupported_claim:" + cid)
            continue
        seen_claims.add(cid)
        claims.append({**claim, "id": cid, "source_ids": refs})
    event_claims = _list_ids(event.get("claim_ids"))
    if not event_claims or any(cid not in seen_claims for cid in event_claims):
        issues.append("event_references_missing_claim")
    if event_claims and not any(set(c["source_ids"]) & set(event_sources)
                                for c in claims if c["id"] in event_claims):
        issues.append("event_claims_do_not_reference_event_evidence")
    return {"valid": not issues, "issues": list(dict.fromkeys(issues)),
            "sources": accepted, "claims": claims, "source_issues": source_issues,
            "as_of": clock.isoformat(), "event_time": event_at.isoformat() if event_at else None}


def score_event(event: dict, validation: dict, *, now: Any = None) -> dict:
    """An editorial priority heuristic, never a financial prediction score."""
    if not validation.get("valid"):
        return {"score": 0.0, "components": {}, "reasons": validation.get("issues", [])}
    hours = max(0.0, (utc_time(now) - utc_time(event["event_time"])).total_seconds() / 3600)
    source_map = {s["id"]: s for s in validation["sources"]}
    primary = any(source_map[s]["source_type"] == "primary" for s in _list_ids(event["source_ids"]))
    components = {
        "significance": 7 * float(event["significance"]),
        "audience_relevance": 5 * float(event["audience_relevance"]),
        "freshness": max(0.0, 20.0 * (1 - hours / 48.0)),
        "source_quality": 10.0 if primary else 7.0,
        "material_development": 10.0,
    }
    return {"score": round(sum(components.values()), 2), "components": components,
            "reasons": [event["significance_reason"], event["relevance_reason"],
                        "Freshness uses event time; verification time does not reset it."]}


def select_news_events(events: list[dict], research: dict, *, coverage: list[dict] | None = None,
                       now: Any = None, policy: NewsPolicy | None = None) -> dict:
    """Return draft candidates; never enqueue or publish.

    Stable upstream event identity is required. Coverage rows contain event_id,
    development_ids (already covered), article_id, and last_event_time. A new
    development in an existing event updates that article. A different event
    concerning the same ticker is eligible immediately.
    """
    policy, clock = policy or NewsPolicy(), utc_time(now)
    coverage_map = {r["event_id"]: r for r in (coverage or []) if r.get("event_id")}
    decisions, candidates, seen = [], [], set()
    for event in events:
        if not isinstance(event, dict):
            decisions.append({"action": "reject", "reasons": ["event_must_be_object"]})
            continue
        validation = validate_event(event, research, now=clock, policy=policy)
        scored = score_event(event, validation, now=clock)
        row = {"event_id": event.get("event_id"), "development_id": event.get("development_id"),
               **scored, "validation": validation, "publishable": False}
        if not validation["valid"]:
            decisions.append({**row, "action": "reject"})
            continue
        identity = (event["event_id"], event["development_id"])
        existing = coverage_map.get(event["event_id"], {})
        if identity in seen or event["development_id"] in existing.get("development_ids", []):
            decisions.append({**row, "action": "skip", "reasons": ["development_already_covered"]})
            continue
        seen.add(identity)
        if existing.get("last_event_time"):
            try:
                event_at, prior_at = utc_time(event["event_time"]), utc_time(existing["last_event_time"])
                grounded_same_day = (event_at == prior_at and event.get("event_time_precision") == "date"
                                     and event.get("material_development") is True
                                     and _has_grounded_development(event, validation, existing))
                if event_at < prior_at or (event_at == prior_at and not grounded_same_day):
                    decisions.append({**row, "action": "skip", "reasons": ["older_than_existing_coverage"]})
                    continue
            except ValueError:
                decisions.append({**row, "action": "reject", "reasons": ["invalid_coverage_timestamp"]})
                continue
        if row["score"] < policy.minimum_score:
            decisions.append({**row, "action": "skip", "reasons": row["reasons"] + ["below_priority_threshold"]})
            continue
        action = "update_draft" if existing.get("article_id") else "new_draft"
        candidates.append({**row, "action": action, "event": event,
                           "update_article_id": existing.get("article_id")})
    # Prefer the latest development within one event, then rank events. Earlier
    # same-batch developments remain visible in the audit, not separate drafts.
    latest = {}
    for row in sorted(candidates, key=lambda r: utc_time(r["event"]["event_time"]), reverse=True):
        if row["event_id"] in latest:
            decisions.append({**row, "action": "skip", "reasons": ["superseded_by_newer_event_development"]})
        else:
            latest[row["event_id"]] = row
    ranked = sorted(latest.values(), key=lambda r: (-r["score"], r["event_id"]))
    selected, overflow_used = [], 0
    for row in ranked:
        baseline_ok = len(selected) < max(0, policy.baseline_limit) and (
            not selected or row["score"] >= policy.second_story_minimum)
        major_ok = (row["event"].get("major_event") is True and
                    float(row["event"]["significance"]) >= 4 and
                    overflow_used < max(0, policy.major_event_overflow))
        if baseline_ok or major_ok:
            if not baseline_ok:
                overflow_used += 1
            selected.append({**row, "slot": "lead" if not selected else (
                "additional" if baseline_ok else "major_event_overflow")})
        else:
            decisions.append({**row, "action": "defer", "reasons": row["reasons"] + ["draft_capacity_or_second_story_threshold"]})
    return {"mode": "preview", "publishable": False, "as_of": clock.isoformat(),
            "policy": asdict(policy), "selected": selected, "decisions": decisions,
            "selection_method": "supplied_editorial_assessment_plus_deterministic_evidence_checks"}
