"""One chat client for every model provider we use.

Meta Model API, Baseten, Fireworks and our own vLLM server on Modal all speak the OpenAI-style
Chat Completions API, so a provider is just a base URL and a key. Each job ("planner",
"watcher") has an ordered list of models, written `provider:model`. The first one whose key is
set answers; if it fails (network error, rate limit, server error) the next one is tried.
Every answer records which model gave it, so the agent log shows exactly who decided what.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    # Environment variable that overrides base_url (for servers we deploy ourselves).
    url_env: str | None = None
    # Accepts the `reasoning_effort` request field (tested against the real API).
    reasoning_effort: bool = False

    def url(self, env: Mapping[str, str]) -> str:
        return (env.get(self.url_env) if self.url_env else None) or self.base_url


PROVIDERS = {
    p.name: p
    for p in (
        Provider("meta", "https://api.meta.ai/v1", "MODEL_API_KEY", reasoning_effort=True),
        Provider(
            "baseten", "https://inference.baseten.co/v1", "BASETEN_API_KEY", reasoning_effort=True
        ),
        Provider("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
        # Muse Glimmer served by vLLM on Modal: the URL comes from the deployment.
        Provider("modal", "", "MODAL_GLIMMER_KEY", url_env="MODAL_GLIMMER_URL"),
    )
}

# How long models think before answering. "low" made Muse Spark 1.3 3-5x faster (4-7 s instead
# of 11-23 s per question) with every answer still passing the fact check, on 30 Oct 2026.
# Override with PLANNER_REASONING_EFFORT / WATCHER_REASONING_EFFORT.
DEFAULT_REASONING_EFFORT = "low"

# Who does each job, best first. Override with PLANNER_MODELS / WATCHER_MODELS.
DEFAULT_MODELS = {
    "planner": "meta:muse-spark-1.3,baseten:meta-models/muse-spark-open-1026",
    "watcher": "modal:muse-glimmer,baseten:meta-models/muse-spark-open-1026",
}


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, text: str) -> ModelRef:
        provider, sep, model = text.strip().partition(":")
        if not sep or not model or provider not in PROVIDERS:
            raise ValueError(f"Model must be 'provider:model' with provider in {sorted(PROVIDERS)}")
        return cls(provider, model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def models_for(job: str, env: Mapping[str, str] = os.environ) -> list[ModelRef]:
    text = env.get(f"{job.upper()}_MODELS") or DEFAULT_MODELS[job]
    return [ModelRef.parse(part) for part in text.split(",") if part.strip()]


@dataclass
class ChatResult:
    message: dict  # the assistant message: content, tool_calls, maybe reasoning_content
    model: str  # "provider:model" that answered
    seconds: float
    usage: dict | None = None
    # Models tried before this one, and why each failed.
    failed: list[dict] = field(default_factory=list)


class ModelUnavailableError(RuntimeError):
    def __init__(self, message: str, failed: list[dict]) -> None:
        super().__init__(message)
        self.failed = failed


class ChatClient:
    def __init__(
        self,
        models: list[ModelRef],
        env: Mapping[str, str] = os.environ,
        http: httpx.Client | None = None,
        timeout: float = 90.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self.models = models
        self.reasoning_effort = reasoning_effort
        self._env = env
        self._http = http or httpx.Client(timeout=timeout)

    def available(self) -> list[ModelRef]:
        """Models whose provider has a key (and a URL) configured."""
        out = []
        for ref in self.models:
            provider = PROVIDERS[ref.provider]
            if self._env.get(provider.key_env) and provider.url(self._env):
                out.append(ref)
        return out

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params) -> ChatResult:
        """Ask the first available model; on failure, the next. Raises ModelUnavailableError."""
        available = self.available()
        if not available:
            keys = sorted({PROVIDERS[r.provider].key_env for r in self.models})
            raise ModelUnavailableError(f"No model configured: set one of {keys}", [])
        failed: list[dict] = []
        for ref in available:
            provider = PROVIDERS[ref.provider]
            body = {"model": ref.model, "messages": messages, **params}
            if self.reasoning_effort and provider.reasoning_effort:
                body.setdefault("reasoning_effort", self.reasoning_effort)
            if tools:
                body["tools"] = tools
            start = time.monotonic()
            try:
                response = self._http.post(
                    f"{provider.url(self._env).rstrip('/')}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._env[provider.key_env]}"},
                )
            except httpx.HTTPError as exc:
                failed.append({"model": str(ref), "error": f"{type(exc).__name__}: {exc}"})
                continue
            seconds = time.monotonic() - start
            if response.status_code != 200:
                error = f"HTTP {response.status_code}: {response.text[:200]}"
                failed.append({"model": str(ref), "error": error})
                continue
            try:
                payload = response.json()
                message = payload["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError):
                failed.append({"model": str(ref), "error": "unexpected response body"})
                continue
            return ChatResult(message, str(ref), seconds, payload.get("usage"), failed)
        raise ModelUnavailableError("Every configured model failed", failed)


def client_for(job: str, env: Mapping[str, str] = os.environ, **kwargs) -> ChatClient:
    effort = env.get(f"{job.upper()}_REASONING_EFFORT", DEFAULT_REASONING_EFFORT) or None
    return ChatClient(models_for(job, env), env, reasoning_effort=effort, **kwargs)
