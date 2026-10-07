"""A local chatbot must fail closed rather than transmitting data elsewhere."""
import pytest


@pytest.mark.parametrize("url", ["https://api.example.com", "http://example.com:11434",
                                 "http://127.0.0.1.example.com:11434", "http://user:password@localhost:11434",
                                 "file:///tmp/model", "https://localhost:11434"])
def test_model_runtime_rejects_remote_or_credentialed_url(monkeypatch, url):
    from local_app import models
    monkeypatch.setenv("OLLAMA_URL", url)
    with pytest.raises(ValueError):
        models.base_url()


def test_disabled_models_never_make_network_requests(monkeypatch):
    from local_app import models
    def forbidden(*args, **kwargs):
        raise AssertionError("A disabled local model attempted network access")
    monkeypatch.setattr(models.httpx, "Client", forbidden)
    assert models.status(refresh=True)["available"] is False
    with pytest.raises(RuntimeError):
        models.chat_json([], {})
    with pytest.raises(RuntimeError):
        models.embed(["Private athlete note"])


def test_disabling_model_overrides_cached_available_status(monkeypatch):
    from local_app import models
    import time
    monkeypatch.setattr(models, "_cached_status", (time.monotonic(), {"available": True}))
    assert models.status()["available"] is False
