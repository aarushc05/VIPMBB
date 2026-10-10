"""A local chatbot must fail closed rather than transmitting data elsewhere."""

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://example.com:11434",
        "http://127.0.0.1.example.com:11434",
        "http://user:password@localhost:11434",
        "file:///tmp/model",
        "https://localhost:11434",
    ],
)
def test_model_runtime_rejects_remote_or_credentialed_url(monkeypatch, url):
    from server.app import models

    monkeypatch.setenv("OLLAMA_URL", url)
    with pytest.raises(ValueError):
        models.base_url()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11435",
        "http://localhost:11434/api",
        "http://localhost:11434?secret=x",
        "http://localhost:11434#fragment",
        "http://host.docker.internal:11434",
        "http://localhost",
    ],
)
def test_model_endpoint_is_exact_local_service(monkeypatch, url):
    from server.app import models

    monkeypatch.setenv("OLLAMA_URL", url)
    with pytest.raises(ValueError):
        models.base_url()


def test_docker_host_bridge_requires_explicit_opt_in(monkeypatch):
    from server.app import models

    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434")
    monkeypatch.setenv("VIPMBB_DOCKER_HOST_MODEL", "1")
    assert models.base_url() == "http://host.docker.internal:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://untrusted.example:11434")
    with pytest.raises(ValueError):
        models.base_url()


def test_disabled_models_never_make_network_requests(monkeypatch):
    from server.app import models

    def forbidden(*args, **kwargs):
        raise AssertionError("A disabled local model attempted network access")

    monkeypatch.setattr(models.httpx, "Client", forbidden)
    assert models.status(refresh=True)["available"] is False
    with pytest.raises(RuntimeError):
        models.chat_json([], {})
    with pytest.raises(RuntimeError):
        models.embed(["Private athlete note"])


def test_disabling_model_overrides_cached_available_status(monkeypatch):
    from server.app import models
    import time

    monkeypatch.setattr(
        models, "_cached_status", (time.monotonic(), {"available": True})
    )
    assert models.status()["available"] is False
