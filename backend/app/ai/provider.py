"""LLM provider abstraction and selection.

    LLMProvider
        ├── MockProvider     deterministic, always available, no network
        ├── OllamaProvider   local open-source models
        ├── GroqProvider     free tier, open-weight models, no install
        ├── GeminiProvider   free tier via Google AI Studio
        └── ClaudeProvider   optional, off by default

`LLM_PROVIDER=auto` resolves in this order: a reachable local Ollama first
(free, private, no rate limit), then whichever free-tier cloud key is
configured (Groq, then Gemini, then Claude), and finally the deterministic
planner.

Commerce never depends on any of them. If the provider fails mid-request the
agent falls back to deterministic planning and the flow continues (see
`agent.py`), and no provider can change a price, a policy decision or a payment
outcome — it only chooses wording.
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("ai.provider")


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    ok: bool = True
    error: str | None = None


class LLMUnavailable(Exception):
    """The provider could not produce a response. Callers degrade gracefully."""


class _GroqJsonModeError(Exception):
    """Groq rejected its own JSON-mode generation; retry without the constraint."""


class LLMProvider(abc.ABC):
    name: str = "abstract"
    model: str = ""
    #: True when this provider produces text without any model call.
    deterministic: bool = False

    @abc.abstractmethod
    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        ...

    @abc.abstractmethod
    def health(self) -> dict:
        ...

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model,
                "deterministic": self.deterministic}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
class OllamaProvider(LLMProvider):
    name = "ollama"
    deterministic = False

    def __init__(self, base_url: str, model: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": temperature,
                                    "num_predict": max_tokens},
                    },
                )
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code >= 400:
                raise LLMUnavailable(f"Ollama returned HTTP {resp.status_code}")
            content = (resp.json().get("message") or {}).get("content", "")
            if not content.strip():
                raise LLMUnavailable("Ollama returned an empty completion.")
            return LLMResponse(text=content, provider=self.name, model=self.model,
                               latency_ms=latency)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama unreachable: {type(exc).__name__}: {exc}") from exc

    def health(self) -> dict:
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
            if resp.status_code >= 400:
                return {"provider": self.name, "available": False,
                        "error": f"HTTP {resp.status_code}"}
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            has_model = any(m == self.model or m.startswith(f"{self.model}:")
                            for m in models)
            return {"provider": self.name, "available": has_model, "model": self.model,
                    "models_installed": models[:20],
                    "error": None if has_model else
                             f"Model '{self.model}' is not pulled. Run: ollama pull {self.model}"}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "available": False,
                    "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Groq — free tier, OpenAI-compatible, very fast
# ---------------------------------------------------------------------------
class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat completions API.

    Free tier at https://console.groq.com — no card required. Runs open-weight
    models (Llama, Mixtral, Gemma), so this keeps the open-source spirit while
    removing the need to install anything locally.
    """
    name = "groq"
    deterministic = False
    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0):
        if not api_key:
            raise LLMUnavailable("GROQ_API_KEY is not set.")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        # Groq's JSON mode occasionally rejects its own generation with a 400
        # ("Failed to validate JSON"). Falling straight back to the
        # deterministic planner would waste a usable model, so retry once
        # without the constraint — `contract.extract_json` already pulls a JSON
        # object out of fenced or prose-wrapped output.
        try:
            return self._request(system, user, max_tokens, temperature, json_mode=True)
        except _GroqJsonModeError:
            log.info("groq json mode failed; retrying without response_format")
            return self._request(system, user, max_tokens, temperature, json_mode=False)

    def _request(self, system: str, user: str, max_tokens: int,
                 temperature: float, *, json_mode: bool) -> LLMResponse:
        started = time.perf_counter()
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            # The prompt already demands a JSON object, which is the
            # precondition for Groq's JSON mode.
            body["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    self.API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body,
                )
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code >= 400:
                detail = _error_detail(resp)
                if json_mode and resp.status_code == 400 and "json" in detail.lower():
                    raise _GroqJsonModeError(detail)
                if resp.status_code == 404:
                    # A model the account cannot reach would otherwise degrade
                    # every turn silently. Name the models it *can* use.
                    raise LLMUnavailable(
                        f"Groq model '{self.model}' is not available on this account. "
                        f"Set GROQ_MODEL to one of: {', '.join(self._usable_models()) or 'see console.groq.com'}."
                    )
                raise LLMUnavailable(f"Groq returned HTTP {resp.status_code}: {detail}")
            choices = resp.json().get("choices", [])
            text = (choices[0].get("message", {}).get("content", "")) if choices else ""
            if not text.strip():
                raise LLMUnavailable("Groq returned an empty completion.")
            return LLMResponse(text=text, provider=self.name, model=self.model,
                               latency_ms=latency)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Groq unreachable: {type(exc).__name__}: {exc}") from exc

    #: Models Groq exposes that are not general chat models — filtered out of
    #: the "try one of these" hint so the suggestion is actually usable.
    _NON_CHAT = ("whisper", "prompt-guard", "orpheus", "tts", "safeguard")

    def _usable_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.get("https://api.groq.com/openai/v1/models",
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            if resp.status_code >= 400:
                return []
            return [
                m["id"] for m in resp.json().get("data", [])
                if not any(bad in m.get("id", "").lower() for bad in self._NON_CHAT)
            ][:5]
        except httpx.HTTPError:
            return []

    def health(self) -> dict:
        if not self.api_key:
            return {"provider": self.name, "available": False,
                    "error": "GROQ_API_KEY not set"}
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.get("https://api.groq.com/openai/v1/models",
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            if resp.status_code == 401:
                return {"provider": self.name, "available": False, "model": self.model,
                        "error": "GROQ_API_KEY was rejected (401). Check the key."}
            if resp.status_code >= 400:
                return {"provider": self.name, "available": False, "model": self.model,
                        "error": f"HTTP {resp.status_code}"}
            models = [m.get("id", "") for m in resp.json().get("data", [])]
            has_model = self.model in models
            return {"provider": self.name, "available": True, "model": self.model,
                    "models_installed": models[:20],
                    "error": None if has_model else
                             f"Model '{self.model}' not in this account's list; "
                             f"try one of: {', '.join(models[:4])}"}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "available": False,
                    "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Google Gemini — free tier via AI Studio
# ---------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    """Google's Gemini API. Free tier key from https://aistudio.google.com/apikey."""
    name = "gemini"
    deterministic = False
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0):
        if not api_key:
            raise LLMUnavailable("GEMINI_API_KEY is not set.")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.BASE_URL}/{self.model}:generateContent",
                    headers={"x-goog-api-key": self.api_key,
                             "Content-Type": "application/json"},
                    json={
                        "system_instruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens,
                            "responseMimeType": "application/json",
                            # Gemini 3.x reasons before answering by default,
                            # which pushed this call past 20s. The model is only
                            # choosing wording over an already-ranked shortlist,
                            # so the deliberation buys nothing and costs a
                            # timeout.
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                )
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code >= 400:
                raise LLMUnavailable(
                    f"Gemini returned HTTP {resp.status_code}: {_error_detail(resp)}")
            candidates = resp.json().get("candidates", [])
            parts = (candidates[0].get("content", {}).get("parts", [])
                     if candidates else [])
            text = "".join(p.get("text", "") for p in parts)
            if not text.strip():
                raise LLMUnavailable("Gemini returned an empty completion.")
            return LLMResponse(text=text, provider=self.name, model=self.model,
                               latency_ms=latency)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Gemini unreachable: {type(exc).__name__}: {exc}") from exc

    def health(self) -> dict:
        if not self.api_key:
            return {"provider": self.name, "available": False,
                    "error": "GEMINI_API_KEY not set"}
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.get(f"{self.BASE_URL}/{self.model}",
                                  headers={"x-goog-api-key": self.api_key})
            if resp.status_code in (401, 403):
                return {"provider": self.name, "available": False, "model": self.model,
                        "error": "GEMINI_API_KEY was rejected. Check the key."}
            if resp.status_code == 404:
                return {"provider": self.name, "available": False, "model": self.model,
                        "error": f"Model '{self.model}' not found. Try gemini-2.0-flash."}
            if resp.status_code >= 400:
                return {"provider": self.name, "available": False, "model": self.model,
                        "error": f"HTTP {resp.status_code}"}
            return {"provider": self.name, "available": True, "model": self.model,
                    "error": None}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "available": False,
                    "error": f"{type(exc).__name__}: {exc}"}


def _error_detail(resp: httpx.Response) -> str:
    """Short, non-sensitive summary of a provider error body."""
    try:
        body = resp.json()
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message", err))[:200]
        return str(err)[:200]
    except Exception:
        return resp.text[:200]


# ---------------------------------------------------------------------------
# Claude (optional)
# ---------------------------------------------------------------------------
class ClaudeProvider(LLMProvider):
    """Optional. Nothing in the demo requires it; there is no paid dependency."""
    name = "claude"
    deterministic = False
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0):
        if not api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set.")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    self.API_URL,
                    headers={"x-api-key": self.api_key,
                             "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": self.model, "max_tokens": max_tokens,
                          "temperature": temperature, "system": system,
                          "messages": [{"role": "user", "content": user}]},
                )
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code >= 400:
                raise LLMUnavailable(f"Claude API returned HTTP {resp.status_code}")
            blocks = resp.json().get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            if not text.strip():
                raise LLMUnavailable("Claude returned an empty completion.")
            return LLMResponse(text=text, provider=self.name, model=self.model,
                               latency_ms=latency)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Claude unreachable: {type(exc).__name__}: {exc}") from exc

    def health(self) -> dict:
        return {"provider": self.name, "available": bool(self.api_key),
                "model": self.model,
                "error": None if self.api_key else "ANTHROPIC_API_KEY not set"}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
_provider_cache: LLMProvider | None = None
_probe_cache: tuple[float, dict] | None = None
_PROBE_TTL = 30.0


def _ollama_health(base_url: str, model: str) -> dict:
    """Probe Ollama, cached briefly.

    Without the cache every buyer turn and every dashboard health poll pays a
    fresh connection timeout when Ollama is not installed, which is the common
    case — a 3-second stall on an endpoint the UI polls.
    """
    global _probe_cache
    now = time.time()
    if _probe_cache and now - _probe_cache[0] < _PROBE_TTL:
        return _probe_cache[1]
    health = OllamaProvider(base_url, model).health()
    _probe_cache = (now, health)
    return health


def _ollama_reachable(base_url: str, model: str) -> bool:
    return _ollama_health(base_url, model).get("available", False)


def get_llm_provider(force: str | None = None) -> LLMProvider:
    """Resolve the active provider. Never raises — always returns something usable."""
    global _provider_cache
    choice = (force or settings.llm_provider or "auto").lower()

    if force is None and _provider_cache is not None and choice != "auto":
        if _provider_cache.name == choice:
            return _provider_cache

    from .mock_provider import MockProvider  # local import avoids a cycle

    def _explicit(name: str) -> LLMProvider:
        """Build a named provider, degrading to Mock rather than failing."""
        try:
            if name == "ollama":
                return OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                                      settings.llm_timeout_seconds)
            if name == "groq":
                return GroqProvider(settings.groq_api_key, settings.groq_model,
                                    settings.llm_timeout_seconds)
            if name == "gemini":
                return GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                                      settings.llm_timeout_seconds)
            if name == "claude":
                return ClaudeProvider(settings.anthropic_api_key,
                                      settings.anthropic_model,
                                      settings.llm_timeout_seconds)
        except LLMUnavailable as exc:
            log.warning("%s requested but unusable (%s); using the deterministic "
                        "planner instead.", name, exc)
        return MockProvider()

    provider: LLMProvider
    if choice == "mock":
        provider = MockProvider()
    elif choice in ("ollama", "groq", "gemini", "claude"):
        provider = _explicit(choice)
    else:
        # auto — local first (free, private, no rate limit), then whichever
        # free-tier cloud key is configured, then the deterministic planner.
        if _ollama_reachable(settings.ollama_base_url, settings.ollama_model):
            provider = OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                                      settings.llm_timeout_seconds)
        elif settings.groq_api_key:
            provider = GroqProvider(settings.groq_api_key, settings.groq_model,
                                    settings.llm_timeout_seconds)
        elif settings.gemini_api_key:
            provider = GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                                      settings.llm_timeout_seconds)
        elif settings.anthropic_api_key:
            provider = ClaudeProvider(settings.anthropic_api_key,
                                      settings.anthropic_model,
                                      settings.llm_timeout_seconds)
        else:
            provider = MockProvider()
        log.info("LLM provider (auto): %s/%s", provider.name, provider.model)

    if force is None:
        _provider_cache = provider
    return provider


def reset_llm_provider() -> None:
    global _provider_cache, _probe_cache
    _provider_cache = None
    _probe_cache = None


def provider_status() -> dict:
    """Full picture for the merchant console's AI panel."""
    from .mock_provider import MockProvider

    active = get_llm_provider()
    ollama = _ollama_health(settings.ollama_base_url, settings.ollama_model)

    def _keyed(name: str, key: str, model: str, hint: str) -> dict:
        """Report a cloud provider without making a network call per poll."""
        if not key:
            return {"provider": name, "available": False, "model": model,
                    "error": f"no API key set — free key at {hint}"}
        return {"provider": name, "available": True, "model": model, "error": None}

    return {
        "configured": settings.llm_provider,
        "active": active.describe(),
        "fallback": MockProvider().describe(),
        "providers": {
            "ollama": ollama,
            "groq": _keyed("groq", settings.groq_api_key, settings.groq_model,
                           "console.groq.com"),
            "gemini": _keyed("gemini", settings.gemini_api_key, settings.gemini_model,
                             "aistudio.google.com/apikey"),
            "mock": {"provider": "mock", "available": True, "deterministic": True},
            "claude": _keyed("claude", settings.anthropic_api_key,
                             settings.anthropic_model, "console.anthropic.com"),
        },
        "degraded": active.deterministic and settings.llm_provider != "mock",
        "note": ("Commerce does not depend on the LLM. If no model is reachable, "
                 "catalog search, recommendations, cart and checkout continue to "
                 "work using deterministic ranking."),
    }


__all__ = ["LLMProvider", "LLMResponse", "LLMUnavailable", "OllamaProvider",
           "GroqProvider", "GeminiProvider", "ClaudeProvider", "get_llm_provider",
           "reset_llm_provider", "provider_status"]
