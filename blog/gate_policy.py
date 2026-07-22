"""
gate_policy.py
==============

Audit-first enforcement policy for the SMN pre-publication gates.

The gates (integrity_gate, citation_gate, editorial_review) are DETECTION. They
always run, and their full findings are always recorded to the audit trail and to
tracking. This module governs only ENFORCEMENT — whether a finding is allowed to
affect publication — so detection and enforcement are independently tunable.

This exists because the original design conflated the two: every finding was a
veto (fail-closed), so a single issue held the article, and with strict gates
*nothing* cleared the bar — SMN stopped publishing. Findings should be recorded
as signal for the audit/improvement cycles, not act as a kill switch.

Postures (config.article_gate_enforcement):
  "advisory"   - never blocks. Every article is publish-eligible; all findings are
                 still recorded. Nothing is dropped. Recommended default on the live
                 site: keeps SMN publishing while the audit corpus accumulates the
                 data needed to actually improve the generator.
  "quarantine" - publish-eligible UNLESS a finding matches one of
                 config.article_gate_quarantine_codes (case-insensitive substring
                 against the finding's code/text). Matched articles are HELD for
                 review (never dropped). Start with an empty list and add codes as
                 the audit data shows which defects are genuinely unpublishable.
  "strict"     - legacy fail-closed: any hard finding makes the article ineligible.
                 This is the CODE default so untouched callers and existing tests
                 keep their original behavior; the live config opts into "advisory".

Nothing here ever deletes or silently drops an article. The only two outcomes are
"publish-eligible" and "held for review".
"""
from __future__ import annotations

ADVISORY = "advisory"
QUARANTINE = "quarantine"
STRICT = "strict"
_VALID = (ADVISORY, QUARANTINE, STRICT)


def posture(config=None) -> str:
    """Active enforcement posture. Unknown/missing => 'strict' (safe legacy default)."""
    value = str(getattr(config, "article_gate_enforcement", STRICT) or STRICT).strip().lower()
    return value if value in _VALID else STRICT


def quarantine_codes(config=None) -> list:
    raw = getattr(config, "article_gate_quarantine_codes", None) or []
    return [str(c).strip().lower() for c in raw if str(c).strip()]


def _finding_text(finding) -> str:
    """Normalize one finding (prose string, or {code, detail} dict) to a string."""
    if isinstance(finding, dict):
        return " ".join(str(finding.get(k, "")) for k in ("code", "detail", "message")).strip()
    return str(finding)


def finding_texts(findings) -> list:
    """Normalize a mixed list of findings to non-empty strings."""
    return [t for t in (_finding_text(f) for f in (findings or [])) if t]


def blocking_findings(hard_findings, config=None) -> list:
    """Subset of hard findings that make an article ineligible under the active
    posture. Empty list => publish-eligible."""
    texts = finding_texts(hard_findings)
    p = posture(config)
    if p == ADVISORY:
        return []
    if p == STRICT:
        return texts
    # QUARANTINE: only findings whose text contains a configured code substring.
    codes = quarantine_codes(config)
    if not codes:
        return []
    return [t for t in texts if any(code in t.lower() for code in codes)]


def is_publish_eligible(hard_findings, config=None) -> bool:
    return not blocking_findings(hard_findings, config)


def gate_blocks_publication(gate_result, issue_key, config=None) -> bool:
    """Whether one recorded gate result (or receipt) should block publication
    under the active posture.

    Every downstream re-verification of a gate result — operator approval,
    publication-command validation, final publish gates — must route its veto
    through here so a recorded finding stays signal, not a kill switch. Receipt
    hash/tamper verification is the caller's job and is never posture-dependent.
    """
    result = gate_result if isinstance(gate_result, dict) else {}
    if result.get("ok") and not result.get(issue_key):
        return False
    findings = finding_texts(result.get(issue_key)) or ["gate recorded not ok"]
    return not is_publish_eligible(findings, config)
