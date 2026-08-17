"""Deterministic source handling for SMN articles.

The research synthesizer assigns source IDs before SMN removes broken,
duplicate, or off-target sources.  Filtering can therefore leave IDs such as
``1, 6, 8`` while an HTML ordered list is displayed as ``1, 2, 3``.  This
module keeps that data contract contiguous and renders the final Sources
section from the canonical research packet instead of asking the writer to
reproduce URLs.

Both operations are local transformations.  They do not make an LLM call and
they do not rewrite article prose.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit


_SOURCES_SECTION_RE = re.compile(
    r"(?P<open><section\b[^>]*\bclass\s*=\s*[\"'][^\"']*\bsources\b[^\"']*[\"'][^>]*>)"
    r".*?</section\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _as_source_id(value: Any) -> Optional[int]:
    """Return a positive integer source ID, accepting JSON numeric strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            return parsed if parsed > 0 else None
    return None


def _remap_single_reference(value: Any, mapping: Dict[int, int]) -> Any:
    old_id = _as_source_id(value)
    if old_id is None:
        return value
    # A reference to a filtered-out source must not survive as a valid-looking
    # number.  None tells the writer that the claim no longer has a source.
    return mapping.get(old_id)


def _remap_reference_list(values: Any, mapping: Dict[int, int]) -> Any:
    if not isinstance(values, list):
        return _remap_single_reference(values, mapping)

    remapped: List[Any] = []
    for item in values:
        old_id = _as_source_id(item)
        if old_id is not None:
            new_id = mapping.get(old_id)
            if new_id is not None and new_id not in remapped:
                remapped.append(new_id)
            continue
        if isinstance(item, (dict, list)):
            _remap_reference_tree(item, mapping)
        remapped.append(item)
    return remapped


def _remap_reference_tree(value: Any, mapping: Dict[int, int], *, root: bool = False) -> None:
    """Update source-reference fields in a JSON-compatible object in place."""
    if isinstance(value, dict):
        for key, child in list(value.items()):
            key_lower = str(key).lower()
            if key_lower == "source_id" or key_lower.endswith("_source_id"):
                value[key] = _remap_single_reference(child, mapping)
            elif key_lower == "source_ids" or key_lower.endswith("_source_ids"):
                value[key] = _remap_reference_list(child, mapping)
            elif key_lower == "sources" and not root:
                # The synthesis schema uses nested fields such as
                # earnings.sources and analyst.sources for lists of source IDs.
                value[key] = _remap_reference_list(child, mapping)
            else:
                _remap_reference_tree(child, mapping)
    elif isinstance(value, list):
        for child in value:
            _remap_reference_tree(child, mapping)


def normalize_research_source_ids(research: Dict[str, Any]) -> Dict[str, Any]:
    """Renumber ``research['sources']`` to 1..N and update all references.

    The object is intentionally updated in place because the same research
    packet is subsequently used by the prompt, citation gate, audit trail, and
    canonical Sources renderer.  Returning it keeps the helper convenient in a
    transformation pipeline.
    """
    if not isinstance(research, dict):
        return research

    sources = research.get("sources")
    if not isinstance(sources, list):
        return research

    mapping: Dict[int, int] = {}
    new_id = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        new_id += 1
        old_id = _as_source_id(source.get("id"))
        if old_id is not None and old_id not in mapping:
            mapping[old_id] = new_id
        source["id"] = new_id

    _remap_reference_tree(research, mapping, root=True)
    return research


def is_valid_http_source_url(url: Any) -> bool:
    """Return True only for an absolute HTTP(S) URL with a hostname."""
    try:
        parsed = urlsplit(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _canonical_source_items(research: Dict[str, Any]) -> List[str]:
    sources = research.get("sources") if isinstance(research, dict) else None
    if not isinstance(sources, list):
        return []

    items: List[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not is_valid_http_source_url(url):
            continue

        publisher = str(source.get("publisher") or "").strip()
        if not publisher:
            publisher = (urlsplit(url).hostname or "Source").removeprefix("www.")
        title = str(source.get("title") or "").strip() or publisher
        label = title if title.casefold() == publisher.casefold() else f"{publisher} - {title}"
        items.append(
            f'          <li><a href="{html.escape(url, quote=True)}">'
            f"{html.escape(label)}</a></li>"
        )
    return items


def _render_sources_section(research: Dict[str, Any], opening_tag: str) -> str:
    items = _canonical_source_items(research)
    return (
        f"{opening_tag}\n"
        "        <h3>Sources</h3>\n"
        "        <ol>\n"
        + "\n".join(items)
        + "\n        </ol>\n"
        "      </section>"
    )


def canonicalize_sources_section(article_html: str, research: Dict[str, Any]) -> str:
    """Replace only the article's Sources section with canonical research URLs.

    Existing section attributes are preserved.  Duplicate Sources sections are
    removed.  If the writer omitted the section, it is inserted before the
    closing article/body/html tag (in that priority order).  When no valid
    canonical source exists, the original article is returned unchanged.
    """
    if not isinstance(article_html, str) or not article_html:
        return article_html
    if not _canonical_source_items(research):
        return article_html

    matches = list(_SOURCES_SECTION_RE.finditer(article_html))
    if matches:
        first = matches[0]
        rendered = _render_sources_section(research, first.group("open"))
        pieces = [article_html[: first.start()], rendered]
        cursor = first.end()
        for duplicate in matches[1:]:
            pieces.append(article_html[cursor : duplicate.start()])
            cursor = duplicate.end()
        pieces.append(article_html[cursor:])
        return "".join(pieces)

    rendered = _render_sources_section(research, '<section class="sources">')
    for closing_tag in ("article", "body", "html"):
        closing = re.search(rf"</{closing_tag}\s*>", article_html, re.IGNORECASE)
        if closing:
            return article_html[: closing.start()] + rendered + "\n" + article_html[closing.start() :]
    return article_html.rstrip() + "\n" + rendered


_SUP_MARKER_RE = re.compile(
    r"<sup\b[^>]*>\s*\[?\s*(\d{1,3})\s*\]?\s*</sup\s*>",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*>", re.IGNORECASE)


def count_rendered_sources(article_html: str) -> int:
    """Number of entries in the article's rendered Sources list."""
    match = _SOURCES_SECTION_RE.search(article_html or "")
    if not match:
        return 0
    return len(_LIST_ITEM_RE.findall(match.group(0)))


def drop_orphan_citation_markers(article_html: str) -> Tuple[str, List[int]]:
    """Remove ``<sup>[n]</sup>`` markers that point at no source.

    Measured on the production audit corpus: 31 of 103 published articles (30%)
    carry at least one inline marker with no matching Sources entry, and five
    carry markers with no Sources section at all.  The prompt has forbidden this
    since the citation-integrity rule was added and the writer does it anyway, so
    the reconciliation is done in code rather than asked for again.

    The claim text is deliberately kept.  An uncited sentence is a weaker
    article; a superscript pointing at a source that does not exist tells the
    reader a specific falsehood about where the number came from.

    Must run AFTER :func:`canonicalize_sources_section`, which is itself allowed
    to shrink the list when a source fails validation - a marker that was valid
    against the writer's list can be orphaned by that step.

    Returns the repaired HTML and the sorted marker numbers that were dropped,
    so the caller can record them to the audit trail.
    """
    if not article_html:
        return article_html, []

    limit = count_rendered_sources(article_html)
    match = _SOURCES_SECTION_RE.search(article_html)
    # Markers inside the Sources section itself are not citations; leave the
    # rendered list untouched no matter what it contains.
    body_end = match.start() if match else len(article_html)

    dropped: List[int] = []
    pieces: List[str] = []
    cursor = 0

    for match in _SUP_MARKER_RE.finditer(article_html):
        if match.start() >= body_end:
            break
        number = int(match.group(1))
        if 1 <= number <= limit:
            continue

        dropped.append(number)
        lead = article_html[cursor : match.start()]
        following = article_html[match.end() : match.end() + 1]
        # Whitespace is repaired only at the seam the removal creates; the rest
        # of the document, including its indentation, is left byte-for-byte.
        if following and following in ".,;:!?)":
            lead = lead.rstrip(" \t")
        elif lead[-1:] in (" ", "\t") and following in (" ", "\t"):
            lead = lead.rstrip(" \t")
        pieces.append(lead)
        cursor = match.end()

    if not dropped:
        return article_html, []

    pieces.append(article_html[cursor:])
    return "".join(pieces), sorted(set(dropped))


__all__ = [
    "canonicalize_sources_section",
    "count_rendered_sources",
    "drop_orphan_citation_markers",
    "is_valid_http_source_url",
    "normalize_research_source_ids",
]
