"""Minimal OpenAI chat client for the ops + improver agents (stdlib + requests only).

Reads the API key from the environment or a local .env so no secret lives in the repo.
Degrades gracefully: if there's no key or the call fails, ``chat`` returns None and the
agents fall back to their mechanical (non-LLM) behaviour instead of crashing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


def load_env(path: Path) -> None:
    """Load KEY=VALUE lines from a .env into os.environ (without overriding existing vars)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def api_key() -> str | None:
    return (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("WC_OPENAI_API_KEY")
        or os.environ.get("FAST_OPENAI_API_KEY")
        or None
    )


def have_key() -> bool:
    return bool(api_key())


def chat(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.4,
    timeout: float = 60.0,
) -> str | None:
    """One chat completion. Returns the assistant text, or None if unavailable/failed."""
    key = api_key()
    if not key:
        return None
    model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    try:
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
    except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError):
        return None
