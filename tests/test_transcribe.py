import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audio import ffmpeg_exe, split_audio
from app.errors import AppError
from app.jobs import JobManager, Paragraph, batch_by_chars, group_paragraphs
from app.main import app, get_jobs
from app.retry import with_retries
from app.transcribe import Segment, parse_gemini_segments, parse_groq_segments
from app.translator import GeminiTranslator


def make_audio(path: Path, seconds: float, video: bool = False) -> Path:
    cmd = [ffmpeg_exe(), "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


# ---------- нарезка ----------

def test_split_audio_into_chunks(tmp_path):
    src = make_audio(tmp_path / "a.wav", 25)
    chunks = asyncio.run(split_audio(src, tmp_path / "out", chunk_seconds=10))
    assert len(chunks) == 3
    assert all(c.suffix == ".mp3" and c.stat().st_size > 0 for c in chunks)


def test_split_video_extracts_audio(tmp_path):
    src = make_audio(tmp_path / "v.mp4", 5, video=True)
    chunks = asyncio.run(split_audio(src, tmp_path / "out", chunk_seconds=10))
    assert len(chunks) == 1


def test_split_rejects_non_media(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("это не аудио")
    with pytest.raises(AppError):
        asyncio.run(split_audio(src, tmp_path / "out"))


# ---------- разбор ответов ----------

def test_parse_groq_segments_adds_offset_and_drops_hallucinations():
    data = {"segments": [
        {"start": 0.0, "end": 3.0, "text": " Bonjour à tous.", "no_speech_prob": 0.01},
        {"start": 3.0, "end": 5.0, "text": "  ", "no_speech_prob": 0.01},
        {"start": 5.0, "end": 9.0, "text": "Sous-titres réalisés par la communauté d'Amara.org",
         "no_speech_prob": 0.9},
    ]}
    segs = parse_groq_segments(data, offset=600)
    assert segs == [Segment(600.0, 603.0, "Bonjour à tous.")]


def test_parse_gemini_segments():
    items = [{"start": "00:00", "text": "Bonjour."}, {"start": "01:05", "text": "Ça va ?"}]
    segs = parse_gemini_segments(items, offset=600, chunk_seconds=600)
    assert segs[0] == Segment(600, 665, "Bonjour.")
    assert segs[1].start == 665


# ---------- абзацы ----------

def test_group_paragraphs_breaks_on_sentence_end():
    segs = [Segment(i * 5, i * 5 + 5, f"Phrase {i}" + ("." if i % 5 == 4 else ""))
            for i in range(10)]
    paras = group_paragraphs(segs)
    assert len(paras) == 2
    assert paras[0].start == 0 and paras[0].end == 25
    assert paras[1].fr.startswith("Phrase 5")


def test_group_paragraphs_forces_break_on_long_speech():
    segs = [Segment(i * 10, i * 10 + 10, "sans point") for i in range(13)]
    paras = group_paragraphs(segs)
    assert all(p.end - p.start <= 60 for p in paras)


def test_batch_by_chars():
    paras = [Paragraph(0, 1, "x" * 4000) for _ in range(3)]
    assert [len(b) for b in batch_by_chars(paras, limit=6000)] == [1, 1, 1]
    assert [len(b) for b in batch_by_chars(paras, limit=9000)] == [2, 1]


# ---------- повторы ----------

def test_retry_waits_then_succeeds(monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls, waits = [], []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("limit")
        return "ok"

    result = asyncio.run(with_retries(flaky, lambda e: 10, 100, waits.append))
    assert result == "ok" and waits == [10, 10]


def test_retry_gives_up_after_max_wait(monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def always_fails():
        raise RuntimeError("limit")

    with pytest.raises(RuntimeError):
        asyncio.run(with_retries(always_fails, lambda e: 60, 100))


def test_translate_batch_falls_back_when_count_mismatch():
    class Fake(GeminiTranslator):
        async def generate(self, contents, config, max_wait=0, on_wait=None):
            if config.response_mime_type == "application/json":
                return '["только один"]'
            return f"RU({contents})"

    result = asyncio.run(Fake(api_key="k").translate_batch(["a", "b"], "fr", "ru"))
    assert result == ["RU(a)", "RU(b)"]


# ---------- весь путь через API ----------

class FakeTranscriber:
    name = "Fake"

    async def transcribe(self, path, offset, on_wait=None):
        return [Segment(offset, offset + 4, f"Phrase à {int(offset)} secondes.")]


class FakeTranslator:
    async def translate_batch(self, texts, source, target, on_wait=None, max_wait=0):
        return [f"Перевод: {t}" for t in texts]


def wait_for_job(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise AssertionError("задача не завершилась")


def test_transcribe_end_to_end(tmp_path):
    manager = JobManager(FakeTranscriber, FakeTranslator(), chunk_seconds=10)
    app.dependency_overrides[get_jobs] = lambda: manager
    try:
        src = make_audio(tmp_path / "talk.mp3", 25)
        with TestClient(app) as client:
            res = client.post("/api/transcribe",
                              files={"file": ("talk.mp3", src.read_bytes(), "audio/mpeg")})
            assert res.status_code == 200, res.text
            job = wait_for_job(client, res.json()["job_id"])
    finally:
        app.dependency_overrides.clear()

    assert job["status"] == "done", job
    assert job["engine"] == "Fake"
    texts = [p["fr"] for p in job["paragraphs"]]
    assert "Phrase à 0 secondes." in texts[0]
    assert "Phrase à 20 secondes." in " ".join(texts)
    assert all(p["ru"].startswith("Перевод: ") for p in job["paragraphs"])


def test_transcribe_bad_file_reports_error():
    manager = JobManager(FakeTranscriber, FakeTranslator())
    app.dependency_overrides[get_jobs] = lambda: manager
    try:
        with TestClient(app) as client:
            res = client.post("/api/transcribe",
                              files={"file": ("x.mp3", b"not audio at all", "audio/mpeg")})
            job = wait_for_job(client, res.json()["job_id"])
    finally:
        app.dependency_overrides.clear()
    assert job["status"] == "error"
    assert "звук" in job["error"]


def test_transcribe_requires_gemini_key(monkeypatch):
    manager = JobManager(FakeTranscriber, GeminiTranslator(api_key=""))
    app.dependency_overrides[get_jobs] = lambda: manager
    try:
        res = TestClient(app).post("/api/transcribe",
                                   files={"file": ("a.mp3", b"123", "audio/mpeg")})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 500
    assert "GEMINI_API_KEY" in res.json()["detail"]


def test_unknown_job_404():
    res = TestClient(app).get("/api/jobs/nope")
    assert res.status_code == 404


def test_retry_gives_up_after_max_attempts(monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = []

    async def always_fails():
        calls.append(1)
        raise RuntimeError("network")

    with pytest.raises(RuntimeError):
        asyncio.run(with_retries(always_fails, lambda e: 5, 10_000, max_attempts=4))
    assert len(calls) == 4
