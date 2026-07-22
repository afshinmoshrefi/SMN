"""
article_audit.py
================

Permanent, on-disk audit trail of every input and intermediate artifact used to
generate one SMN article: the fully-rendered LLM prompt, the raw Tavily research
(all queries, both search passes), the research-synthesis output, the raw model
article response *before* any of our own mutation, every gate verdict, the frozen
evidence/source contract, and the image-generation prompts.

Why this exists
---------------
Two things the live pipeline could not previously answer:

  1. Audit — reproduce and explain any article after the fact. The published HTML
     is heavily rewritten after generation (SEO title swap, editorial repair,
     post-processing), so the file on disk is NOT what the model produced. The
     only way to attribute a defect to the research, the prompt, the model, or
     our own post-processing is to keep each of those inputs. Today they are all
     local variables that get discarded.

  2. Enhancement — build a (prompt, research) -> output corpus for prompt
     iteration, model-migration regression testing, and few-shot mining.

Design constraints
------------------
  * Side-band and fail-open. Recording or writing an audit artifact must NEVER
    affect article generation. Every public entry point swallows its own errors
    (same contract as log_article_run / hero_failures.jsonl).

  * Context-scoped. begin() installs the active trail in a ContextVar so code
    deep in the call stack (Tavily research, the OpenAI wrapper, image
    generation) can record()/append() without threading an object through every
    signature. Outside a run the ContextVar is None and record() is a no-op.

  * Off by default. Gated by config.article_audit_enabled. When disabled, begin()
    returns None and nothing is collected or written.

  * NOT in the web docroot. Written under config.article_audit_dir
    (default /home/flask/blog/audit) — a sibling of logs/ that is neither served
    by the SMN site (news_root_folder = /var/www/smn) nor tracked by git. Raw
    prompts and research must never be publicly fetchable.

  * No secrets. Only response bodies and prompts are captured — never request
    headers or api_key fields. The manifest snapshots a fixed whitelist of
    non-secret config flags, never the config module itself.

On-disk layout (one directory per run):

    <article_audit_dir>/YYYY/MM/DD/<SYMBOL>_<start>_<days>_<years>_<article_id>/
        manifest.json               provenance + file index (bytes / sha256)
        prompt.txt                  the fully-rendered article prompt
        research_tavily_raw.json    raw Tavily responses (both queries, both passes)
        research_context.txt        formatted Tavily blob fed to synthesis
        research_synthesis_raw.txt  raw model output of the synthesis step
        research.json               parsed research JSON
        company_domains.json        official-domain lookup input + result
        article_llm_raw.html        raw article model response (pre-mutation)
        article_after_review.html   HTML after editorial review / repair
        final.html                  final held / approved reviewed HTML
        editorial_review.json       review decision + hard/soft issues
        citation_gate.json          citation gate verdict
        integrity_gate.json         integrity gate verdict
        evidence.json               frozen exact-data evidence bundle
        source_contract.json        frozen citation source contract
        image_prompts.json          image-generation prompts (hero + any AI images)
"""

import contextvars
import dataclasses
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

try:
    import config as _config  # prime sys.modules; real runs always have config
except Exception:  # pragma: no cover - config is always present in real runs
    _config = None


def _cfg():
    """Resolve the active config dynamically rather than from a stale import-time
    binding, so a runtime-swapped or test-patched sys.modules['config'] is always
    honored (e.g. hermetic tests that inject a no-side-effect config)."""
    return sys.modules.get("config", _config)

# The single active trail for the current context. None outside a run (or when
# auditing is disabled), which makes every record()/append() a cheap no-op.
_active: "contextvars.ContextVar" = contextvars.ContextVar("smn_article_audit", default=None)

# Non-secret config flags snapshotted into every manifest for reproducibility.
# NEVER add credential-bearing names here (no *_KEY / *_token / api_key).
_CONFIG_FLAG_WHITELIST = (
    "citation_gate_mode",
    "citation_check_liveness",
    "article_gate_enforcement",
    "review_mode",
    "review_fail_closed",
    "projection_uncertainty_mode",
    "projection_uncertainty_level",
    "projection_min_sample",
    "hero_image_generation_enabled",
    "hero_image_routing",
    "publication_enabled",
    "automatic_review_approval_enabled",
    "seo_enabled",
)

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _default_audit_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit")


def is_enabled() -> bool:
    """True only when config explicitly opts in."""
    cfg = _cfg()
    return bool(getattr(cfg, "article_audit_enabled", False)) if cfg else False


def is_active() -> bool:
    """True when a trail is collecting. Lets callers skip expensive capture prep."""
    return _active.get() is not None


def _jsonable(obj):
    """json.dump default handler: dataclasses -> dict, everything else -> str."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return str(obj)


def _safe(component) -> str:
    text = _SAFE_COMPONENT.sub("-", str(component)).strip("-")
    return text or "na"


def _git_commit() -> str:
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _provenance_models() -> dict:
    """Best-effort snapshot of which models produced each artifact."""
    models = {
        # Hardcoded in AI_tools.synthesize_research_with_grok / get_company_domains_with_grok.
        "research_synthesis": "gpt-5-mini",
        "company_domains": "gpt-5-nano",
    }
    try:
        import AI_tools
        models["article"] = getattr(AI_tools, "OPENAI_MODEL_DEFAULT", None)
        models["image_provider"] = getattr(AI_tools, "PREMIUM_IMAGE_PROVIDER", None)
        models["image_model"] = getattr(AI_tools, "DEFAULT_OPENAI_IMAGE_MODEL", None)
    except Exception:
        pass
    return {k: v for k, v in models.items() if v is not None}


class ArticleAuditTrail:
    """Accumulates artifacts for one article run and writes them once at the end."""

    SCHEMA_VERSION = 1

    def __init__(self, identity: dict):
        self.identity = dict(identity or {})
        self.article_id = str(self.identity.get("article_id") or "")
        self._sections: "dict[str, tuple[str, object]]" = {}
        self._started_at = datetime.datetime.now(datetime.timezone.utc)
        self._token = None  # ContextVar reset token, set by begin()

    # -- collection -------------------------------------------------------
    @staticmethod
    def _kind_for(name: str) -> str:
        return "json" if name.endswith(".json") else "text"

    def record(self, name: str, value) -> None:
        """Store a single artifact under `name`. None is ignored (no empty file)."""
        if value is None:
            return
        self._sections[name] = (self._kind_for(name), value)

    def append(self, name: str, value) -> None:
        """Append `value` to a list-valued JSON artifact under `name`."""
        if value is None:
            return
        kind, existing = self._sections.get(name, ("json", []))
        if not isinstance(existing, list):
            existing = [existing]
        existing.append(value)
        self._sections[name] = ("json", existing)

    # -- serialization ----------------------------------------------------
    def _target_dir(self) -> str:
        cfg = _cfg()
        base = (getattr(cfg, "article_audit_dir", None) if cfg else None) or _default_audit_dir()
        pub_date = str(self.identity.get("article_publish_date") or "")
        parts = pub_date.split("-") if re.match(r"^\d{4}-\d{2}-\d{2}", pub_date) else []
        if len(parts) >= 3:
            yyyy, mm, dd = parts[0], parts[1], parts[2]
        else:
            today = datetime.date.today().isoformat().split("-")
            yyyy, mm, dd = today[0], today[1], today[2]
        run_name = "_".join(_safe(self.identity.get(k, "")) for k in
                            ("symbol", "start_date", "days", "years")) + f"_{_safe(self.article_id)}"
        return os.path.join(base, yyyy, mm, dd, run_name)

    def _build_manifest(self, tracking: dict, files: list) -> dict:
        tracking = tracking if isinstance(tracking, dict) else {}
        finished = datetime.datetime.now(datetime.timezone.utc)
        duration = tracking.get("duration_seconds")
        if duration is None:
            duration = round((finished - self._started_at).total_seconds(), 1)
        flags = {}
        cfg = _cfg()
        if cfg is not None:
            for name in _CONFIG_FLAG_WHITELIST:
                if hasattr(cfg, name):
                    flags[name] = getattr(cfg, name)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "article_id": self.article_id,
            "identity": self.identity,
            "run": {
                "started_at": self._started_at.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_seconds": duration,
                "status": tracking.get("status"),
                "error_step": tracking.get("error_step"),
                "error_message": tracking.get("error_message"),
                "steps": tracking.get("steps"),
            },
            "provenance": {
                "git_commit": _git_commit(),
                "models": _provenance_models(),
                "config_flags": flags,
            },
            "files": files,
        }

    def write(self, tracking: dict) -> "str | None":
        """Serialize every collected artifact to disk. Fail-open: returns the run
        directory on success, None on any error (never raises)."""
        try:
            target = self._target_dir()
            os.makedirs(target, exist_ok=True)
            files = []
            for name, (kind, value) in sorted(self._sections.items()):
                try:
                    if kind == "json":
                        payload = json.dumps(value, ensure_ascii=False, indent=2,
                                             default=_jsonable).encode("utf-8")
                    else:
                        payload = str(value).encode("utf-8")
                    with open(os.path.join(target, name), "wb") as fh:
                        fh.write(payload)
                    files.append({"name": name, "bytes": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest()})
                except Exception as exc:
                    print(f"[AUDIT] failed to write {name}: {exc}")
            manifest = self._build_manifest(tracking, files)
            with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2, default=_jsonable)
            print(f"[AUDIT] Saved article audit trail -> {target} ({len(files)} artifact(s))")
            return target
        except Exception as exc:
            print(f"[AUDIT] Could not persist audit trail: {exc}")
            return None


# ----------------------------------------------------------------------
# Module-level API — safe to call from anywhere in the generation stack.
# ----------------------------------------------------------------------

def begin(identity: dict) -> "ArticleAuditTrail | None":
    """Start collecting for one run and install it as the active trail.

    Returns the trail (to be passed to finish() later), or None when auditing is
    disabled. Never raises."""
    try:
        if not is_enabled():
            return None
        trail = ArticleAuditTrail(identity)
        trail._token = _active.set(trail)
        return trail
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[AUDIT] begin() failed, auditing disabled for this run: {exc}")
        return None


def record(name: str, value) -> None:
    """Store a single artifact on the active trail. No-op if none / disabled."""
    trail = _active.get()
    if trail is None:
        return
    try:
        trail.record(name, value)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[AUDIT] record({name}) failed: {exc}")


def append(name: str, value) -> None:
    """Append to a list-valued artifact on the active trail. No-op if none."""
    trail = _active.get()
    if trail is None:
        return
    try:
        trail.append(name, value)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[AUDIT] append({name}) failed: {exc}")


def finish(trail: "ArticleAuditTrail | None", tracking: dict) -> "str | None":
    """Write the trail to disk and clear the active context. Never raises."""
    try:
        if trail is None:
            return None
        return trail.write(tracking)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[AUDIT] finish() failed: {exc}")
        return None
    finally:
        try:
            if trail is not None and trail._token is not None:
                _active.reset(trail._token)
            else:
                _active.set(None)
        except Exception:
            _active.set(None)
