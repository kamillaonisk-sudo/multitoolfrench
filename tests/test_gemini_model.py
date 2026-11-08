import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from app import gemini
from app.gemini import GeminiClient, choose_model


def test_choose_prefers_stable_flash_lite_newest():
    names = [
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.9-flash-lite-preview",
        "gemini-3.8-flash-lite-tts", "gemini-embedding-001", "gemini-3.1-flash-lite-image",
    ]
    assert choose_model(names) == "gemini-3.5-flash-lite"


def test_choose_falls_back_to_flash_then_preview():
    assert choose_model(["gemini-2.5-pro", "gemini-3.8-flash"]) == "gemini-3.8-flash"
    assert choose_model(["gemini-4-flash-preview", "gemini-embedding-2"]) == "gemini-4-flash-preview"
    assert choose_model(["text-bison", "gemini-embedding-001"]) is None


class FakeModels:
    def __init__(self, available):
        self.available = available
        self.used = []

    async def list(self, config=None):
        async def gen():
            for name in self.available:
                yield SimpleNamespace(name=f"models/{name}", supported_actions=["generateContent"])
            yield SimpleNamespace(name="models/gemini-embedding-001", supported_actions=["embedContent"])
        return gen()

    async def generate_content(self, model, contents, config):
        self.used.append(model)
        if model not in self.available:
            raise errors.APIError(404, {"error": {"message": "not found", "status": "NOT_FOUND"}})
        return SimpleNamespace(text=f"ok from {model}")


def make_client(models, model=""):
    c = GeminiClient(api_key=f"key-{id(models)}", model=model or None)
    c.model = model
    c._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return c


@pytest.fixture(autouse=True)
def clear_cache():
    gemini._resolved_models.clear()
    gemini._resolve_lock = None
    yield
    gemini._resolved_models.clear()


CONFIG = types.GenerateContentConfig()


def test_auto_model_is_picked_and_cached():
    models = FakeModels(["gemini-2.5-flash", "gemini-2.5-flash-lite"])
    c = make_client(models)
    assert asyncio.run(c.generate("hi", CONFIG)) == "ok from gemini-2.5-flash-lite"
    assert asyncio.run(c.generate("hi", CONFIG)) == "ok from gemini-2.5-flash-lite"
    assert list(gemini._resolved_models.values()) == ["gemini-2.5-flash-lite"]


def test_missing_configured_model_falls_back_to_auto():
    models = FakeModels(["gemini-3.5-flash-lite"])
    c = make_client(models, model="gemini-flash-lite-latest")
    assert asyncio.run(c.generate("hi", CONFIG)) == "ok from gemini-3.5-flash-lite"
    assert models.used == ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]
    assert c.model == ""  # дальше сразу автоматический выбор


def test_configured_model_that_exists_is_used():
    models = FakeModels(["gemini-2.5-flash", "gemini-3.5-flash-lite"])
    c = make_client(models, model="gemini-2.5-flash")
    assert asyncio.run(c.generate("hi", CONFIG)) == "ok from gemini-2.5-flash"
