import asyncio

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.jobs import Job, JobManager, Paragraph, transcript_text
from app.main import app, get_jobs, get_summarizer
from app.summary import GeminiSummarizer, _clean

SUMMARY = {
    "overview": "Рассказ о лионской кухне.",
    "sections": [{"title": "Бушоны", "start": "00:30", "points": ["Маленькие ресторанчики"]}],
    "takeaways": ["Лион — гастрономическая столица"],
}


class FakeSummarizer:
    def __init__(self):
        self.calls = []

    async def summarize(self, text, max_wait=0):
        self.calls.append(text)
        return SUMMARY


@pytest.fixture
def fake():
    s = FakeSummarizer()
    app.dependency_overrides[get_summarizer] = lambda: s
    yield s
    app.dependency_overrides.clear()


def test_summary_of_text(fake):
    res = TestClient(app).post("/api/summary", json={"text": "Bonjour à tous."})
    assert res.status_code == 200
    assert res.json() == SUMMARY
    assert fake.calls == ["Bonjour à tous."]


def test_summary_rejects_blank(fake):
    assert TestClient(app).post("/api/summary", json={"text": "  "}).status_code == 400


def make_manager(status="done"):
    manager = JobManager(lambda: None, None)
    job = Job(id="j1", filename="a.mp3", engine="Fake", status=status,
              paragraphs=[Paragraph(0, 20, "Bonjour.", "Привет."),
                          Paragraph(3725, 3750, "Au revoir.", "До свидания.")])
    manager.jobs[job.id] = job
    app.dependency_overrides[get_jobs] = lambda: manager
    return manager, job


def test_job_summary_uses_french_with_timestamps_and_is_cached(fake):
    _, job = make_manager()
    client = TestClient(app)
    assert client.post("/api/jobs/j1/summary").json() == SUMMARY
    assert client.post("/api/jobs/j1/summary").json() == SUMMARY
    assert fake.calls == ["[00:00] Bonjour.\n[1:02:05] Au revoir."]  # второй раз — из кэша
    assert client.get("/api/jobs/j1").json()["summary"] == SUMMARY


def test_job_summary_waits_for_finish(fake):
    make_manager(status="running")
    assert TestClient(app).post("/api/jobs/j1/summary").status_code == 409


def test_job_summary_unknown_job(fake):
    make_manager()
    assert TestClient(app).post("/api/jobs/nope/summary").status_code == 404


def test_transcript_text():
    assert transcript_text([Paragraph(65, 70, "Salut")]) == "[01:05] Salut"


def test_clean_drops_junk():
    data = {"overview": " Суть ", "sections": [
        {"title": "A", "start": "", "points": ["x", "", 3]}, "мусор", {"title": "", "points": []}],
        "takeaways": "не список"}
    assert _clean(data) == {
        "overview": "Суть",
        "sections": [{"title": "A", "start": "", "points": ["x", "3"]}],
        "takeaways": [],
    }


def test_summarizer_parses_model_json():
    class Fake(GeminiSummarizer):
        async def generate(self, contents, config, max_wait=0, on_wait=None):
            return '{"overview": "Суть", "sections": [], "takeaways": ["вывод"]}'

    result = asyncio.run(Fake(api_key="k").summarize("texte"))
    assert result["overview"] == "Суть" and result["takeaways"] == ["вывод"]


def test_summarizer_bad_json():
    class Fake(GeminiSummarizer):
        async def generate(self, contents, config, max_wait=0, on_wait=None):
            return "not json"

    with pytest.raises(AppError):
        asyncio.run(Fake(api_key="k").summarize("texte"))
