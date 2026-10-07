"""Strictly local model access. No cloud model names or remote hosts are accepted."""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlparse

import httpx

CHAT_MODEL = "qwen3:4b"
EMBED_MODEL = "embeddinggemma:latest"
_lock = threading.Lock()
_cached_status: tuple[float, dict] = (0, {})


def base_url():
    value = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise ValueError("AI runtime must be a local loopback service.")
    return value


def disabled():
    return os.environ.get("VIPMBB_DISABLE_MODEL") == "1"


def status(refresh=False):
    global _cached_status
    if not disabled() and not refresh and time.monotonic() - _cached_status[0] < 15:
        return dict(_cached_status[1])
    result = {"available": False, "model": CHAT_MODEL, "embedding_model": EMBED_MODEL,
              "installed_models": [], "error": None, "local_only": True}
    if disabled():
        result["error"] = "Local model disabled; bounded data tools remain available."
        return result
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(base_url() + "/api/tags")
            response.raise_for_status()
            names = [m["name"] for m in response.json().get("models", [])]
        result["installed_models"] = sorted(set(n for n in names if not n.startswith("llamacpp:")))
        result["available"] = CHAT_MODEL in names
        if not result["available"]:
            result["error"] = "Chat model is not installed. Run npm run setup:models."
    except (httpx.HTTPError, ValueError, KeyError):
        result["error"] = "Local model service is offline. Start the app with npm run dev."
    _cached_status = (time.monotonic(), result)
    return dict(result)


def chat_json(messages, schema):
    if disabled():
        raise RuntimeError("Local model is disabled.")
    # One inference at a time bounds memory on a 16 GB laptop. No arbitrary tools.
    with _lock, httpx.Client(timeout=httpx.Timeout(150, connect=3), trust_env=False) as client:
        response = client.post(base_url() + "/api/chat", json={
            "model": CHAT_MODEL, "messages": messages, "stream": False,
            "think": False, "format": schema, "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 600},
        })
        response.raise_for_status()
        return json.loads(response.json()["message"]["content"])


def embed(texts):
    if disabled():
        raise RuntimeError("Local embeddings are disabled.")
    if not texts:
        return []
    with _lock, httpx.Client(timeout=httpx.Timeout(90, connect=3), trust_env=False) as client:
        response = client.post(base_url() + "/api/embed", json={
            "model": EMBED_MODEL, "input": [t[:6000] for t in texts], "keep_alive": 0,
        })
        response.raise_for_status()
        vectors = response.json()["embeddings"]
        if len(vectors) != len(texts):
            raise ValueError("Embedding count mismatch")
        return vectors
