import asyncio

from fastapi.testclient import TestClient
from google import genai
from google.genai import types

from app.main import app, get_translator
from app.translator import GeminiTranslator, TranslationError, detect_source


class FakeTranslator:
    def __init__(self):
        self.calls = []

    async def translate(self, text, source, target):
        self.calls.append((text, source, target))
        return f"[{source}->{target}] {text}"


def make_client(translator):
    app.dependency_overrides[get_translator] = lambda: translator
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_detect_source():
    assert detect_source("Bonjour, comment ça va ?") == "fr"
    assert detect_source("Привет, как дела?") == "ru"
    assert detect_source("Привет, Paris!") == "ru"


def test_auto_direction_french():
    fake = FakeTranslator()
    res = make_client(fake).post("/api/translate", json={"text": "Bonjour"})
    assert res.status_code == 200
    assert res.json() == {"translation": "[fr->ru] Bonjour", "source": "fr", "target": "ru"}


def test_auto_direction_russian():
    fake = FakeTranslator()
    res = make_client(fake).post("/api/translate", json={"text": "Привет"})
    assert res.json()["source"] == "ru"
    assert res.json()["target"] == "fr"


def test_explicit_direction_wins():
    fake = FakeTranslator()
    res = make_client(fake).post("/api/translate", json={"text": "Привет", "direction": "fr-ru"})
    assert fake.calls == [("Привет", "fr", "ru")]
    assert res.status_code == 200


def test_empty_text_rejected():
    fake = FakeTranslator()
    client = make_client(fake)
    assert client.post("/api/translate", json={"text": ""}).status_code == 422
    assert client.post("/api/translate", json={"text": "   "}).status_code == 400
    assert fake.calls == []


def test_too_long_text_rejected():
    res = make_client(FakeTranslator()).post("/api/translate", json={"text": "a" * 20_001})
    assert res.status_code == 422


def test_missing_api_key_gives_clear_message():
    res = make_client(GeminiTranslator(api_key="")).post("/api/translate", json={"text": "Bonjour"})
    assert res.status_code == 500
    assert "GEMINI_API_KEY" in res.json()["detail"]


def test_translation_error_passed_to_user():
    class Failing:
        async def translate(self, *_):
            raise TranslationError("Лимит исчерпан", status_code=502)

    res = make_client(Failing()).post("/api/translate", json={"text": "Bonjour"})
    assert res.status_code == 502
    assert res.json() == {"detail": "Лимит исчерпан"}


def test_index_page_served():
    res = make_client(FakeTranslator()).get("/")
    assert res.status_code == 200
    assert "Переводчик" in res.text


def test_network_error_gives_clear_message():
    t = GeminiTranslator(api_key="fake")
    t._client = genai.Client(
        api_key="fake", http_options=types.HttpOptions(base_url="https://127.0.0.1:9")
    )
    try:
        asyncio.run(t.translate("Bonjour", "fr", "ru", max_wait=0))
    except TranslationError as e:
        assert "Нет связи" in e.message
    else:
        raise AssertionError("ожидалась ошибка")
