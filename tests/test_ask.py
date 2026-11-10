import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.ask import GeminiAsker, build_materials
from app.errors import AppError
from app.main import app, get_asker


def note(i, title, overview, paragraphs=None, text=None, topic="История"):
    return {
        "id": f"job-{i}", "title": title, "topic": topic, "created": "2026-11-08T10:00:00Z",
        "filename": f"rec{i}.mp3",
        "summary": {"overview": overview, "sections": [
            {"title": "Раздел", "start": "00:30", "points": ["пункт"]}], "takeaways": ["вывод"]},
        "paragraphs": paragraphs, "text": text,
    }


LYON = note(1, "Лионская кухня", "Подкаст о кухне Лиона",
            [{"start": 65, "original": "On mange des quenelles à Lyon."}])
REVO = note(2, "Революция", "Лекция о революции 1789 года",
            [{"start": 0, "original": "La Révolution commence en 1789."}])


def test_materials_include_summary_and_transcript():
    text, ids, full = build_materials([LYON, REVO], "Что едят в Лионе?")
    assert ids == {"n1": "job-1", "n2": "job-2"}
    assert full == 2
    assert '<note id="n1" title="Лионская кухня"' in text
    assert "[01:05] On mange des quenelles à Lyon." in text
    assert "Подкаст о кухне Лиона" in text
    assert text.index('id="n1"') < text.index('id="n2"')  # исходный порядок


def test_materials_respect_limit_and_prefer_relevant():
    big = [{"start": i, "original": "blabla " * 50} for i in range(200)]
    lyon = note(1, "Лионская кухня", "Подкаст о кухне Лиона и кнелях", big)
    revo = note(2, "Революция", "Лекция о революции", big)
    text, ids, full = build_materials([revo, lyon], "Расскажи про кнели Лиона", limit=80_000)
    assert len(text) <= 80_000
    assert set(ids) == {"n1", "n2"}  # конспекты обеих записей влезли
    assert full == 1
    # полный текст достался самой похожей записи — про Лион (n2)
    lyon_block = text[text.index('id="n2"'):]
    assert "Расшифровка (оригинал)" in lyon_block


def test_text_notes_use_text():
    text, _, full = build_materials([note(3, "Заметка", "о", text="Bonjour tout le monde")], "?")
    assert "Текст:\nBonjour tout le monde" in text and full == 1


class FakeAsker(GeminiAsker):
    def __init__(self, reply):
        super().__init__(api_key="k")
        self.reply = reply
        self.seen = {}

    async def generate(self, contents, config, max_wait=0, on_wait=None):
        self.seen["contents"] = contents
        self.seen["prompt"] = config.system_instruction
        return self.reply


def test_asker_maps_sources_and_sends_history():
    reply = json.dumps({"answer": "В Лионе едят кнели.", "sources": [
        {"note": "n1", "time": "01:05"}, {"note": "n1", "time": "01:05"}, {"note": "n9", "time": ""}]})
    asker = FakeAsker(reply)
    result = asyncio.run(asker.ask("Что едят?", [LYON, REVO],
                                   [{"question": "Привет", "answer": "Здравствуйте"}]))
    assert result["answer"] == "В Лионе едят кнели."
    assert result["sources"] == [{"note_id": "job-1", "time": "01:05"}]  # без дублей и чужих id
    assert result["notes_used"] == 2 and result["full_texts"] == 2
    roles = [c.role for c in asker.seen["contents"]]
    assert roles == ["user", "model", "user"]
    assert "quenelles" in asker.seen["prompt"]


def test_asker_errors():
    with pytest.raises(AppError):
        asyncio.run(FakeAsker("{}").ask("?", [], []))
    with pytest.raises(AppError):
        asyncio.run(FakeAsker("not json").ask("?", [LYON], []))
    with pytest.raises(AppError):
        asyncio.run(FakeAsker('{"answer": "", "sources": []}').ask("?", [LYON], []))


def test_ask_endpoint():
    class Fake:
        async def ask(self, question, notes, history):
            return {"answer": f"{question}:{len(notes)}:{len(history)}", "sources": [],
                    "notes_used": len(notes), "notes_total": len(notes), "full_texts": 0}

    app.dependency_overrides[get_asker] = lambda: Fake()
    try:
        client = TestClient(app)
        res = client.post("/api/ask", json={"question": "Что?", "notes": [LYON, REVO],
                                            "history": [{"question": "a", "answer": "b"}]})
        assert res.status_code == 200 and res.json()["answer"] == "Что?:2:1"
        assert client.post("/api/ask", json={"question": "  ", "notes": [LYON]}).status_code == 400
    finally:
        app.dependency_overrides.clear()
