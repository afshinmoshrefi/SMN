"""Bounded article-writing transport with explicit model and usage provenance.

Importing this module performs no I/O and reads no application configuration.
The callable is injectable into both seasonal and general-news workflows.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_ARTICLE_MODEL = "gpt-6-astra"
DEFAULT_ARTICLE_EFFORT = "low"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class ArticleModelError(RuntimeError):
    pass


def chat_payload(prompt: str, *, model: str = DEFAULT_ARTICLE_MODEL,
                 system: str = "", stream: bool = False, **options: Any) -> dict:
    """Build an Astra-compatible request; also supports existing OpenAI callers."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages, "stream": stream}
    payload.update({key: value for key, value in options.items() if value is not None})
    if model.startswith("gpt-6-astra"):
        for key in ("temperature", "top_p", "top_logprobs", "logprobs"):
            payload.pop(key, None)
        effort = payload.get("reasoning_effort", DEFAULT_ARTICLE_EFFORT)
        if effort in ("none", "minimal"):
            effort = "low"
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("Unsupported article reasoning effort")
        payload["reasoning_effort"] = effort
    return payload


class ArticleLLM:
    """One article stage. Calls record usage, never credentials or request headers.

    There are no automatic paid retries. The workflow owns its bounded repair
    policy. Truncated/refused/empty responses cannot silently become articles.
    """

    def __init__(self, *, model: str | None = None, effort: str | None = None,
                 system: str = "Return only the requested article content or JSON.",
                 stage: str = "write", max_completion_tokens: int = 8192,
                 timeout: float = 180, api_key: str | None = None,
                 opener: Callable | None = None):
        self.model = model or os.getenv("SMN_ARTICLE_MODEL") or DEFAULT_ARTICLE_MODEL
        self.effort = effort or os.getenv("SMN_ARTICLE_REASONING_EFFORT") or DEFAULT_ARTICLE_EFFORT
        self.system, self.stage = system, stage
        self.max_completion_tokens = max_completion_tokens
        self.timeout, self._api_key = timeout, api_key
        self._opener = opener or urllib.request.urlopen
        self.calls: list[dict] = []

    def __call__(self, prompt: str) -> str:
        key = self._api_key or os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")
        if not key:
            raise ArticleModelError("OpenAI credential is not configured")
        payload = chat_payload(
            prompt, model=self.model, system=self.system,
            reasoning_effort=self.effort, max_completion_tokens=self.max_completion_tokens,
            store=False, service_tier="default")
        request = urllib.request.Request(
            OPENAI_CHAT_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            method="POST")
        started = time.monotonic()
        record = {"stage": self.stage, "requested_model": self.model,
                  "reasoning_effort": payload.get("reasoning_effort"),
                  "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
        try:
            with self._opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            record.update(model=data.get("model"), usage=data.get("usage") or {},
                          finish_reason=choice.get("finish_reason"),
                          service_tier=data.get("service_tier"))
            if message.get("refusal"):
                raise ArticleModelError("Article request was refused")
            if choice.get("finish_reason") != "stop":
                raise ArticleModelError("Article response was incomplete")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ArticleModelError("Article response was empty")
            record["status"] = "ok"
            return content
        except urllib.error.HTTPError as exc:
            record.update(status="error", http_status=exc.code)
            raise ArticleModelError(f"OpenAI article request failed (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            record["status"] = "error"
            raise ArticleModelError("OpenAI article request could not complete") from None
        except (ValueError, KeyError, TypeError) as exc:
            record["status"] = "error"
            raise ArticleModelError("OpenAI returned an invalid article response") from None
        except ArticleModelError:
            record["status"] = "error"
            raise
        finally:
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            self.calls.append(record)


def collect_model_usage(**stages: Any) -> dict:
    """Collect actual calls; an injected provider is never mislabeled as Astra."""
    return {name: list(getattr(provider, "calls", []))
            for name, provider in stages.items()}
