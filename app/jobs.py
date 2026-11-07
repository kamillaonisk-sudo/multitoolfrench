"""Фоновые задачи расшифровки: нарезка → расшифровка → абзацы → перевод."""

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.audio import CHUNK_SECONDS, split_audio
from app.errors import AppError
from app.transcribe import Segment, Transcriber

log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 24 * 3600
TRANSLATE_BATCH_CHARS = 6000


@dataclass
class Paragraph:
    start: float
    end: float
    fr: str
    ru: str = ""


@dataclass
class Job:
    id: str
    filename: str
    engine: str
    status: str = "queued"  # queued | running | done | error
    stage: str = "В очереди…"
    progress: float = 0.0
    paragraphs: list[Paragraph] = field(default_factory=list)
    error: str | None = None
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def group_paragraphs(segments: list[Segment], min_s: float = 20, max_s: float = 60) -> list[Paragraph]:
    """Склеивает короткие фразы в абзацы по 20–60 секунд, стараясь резать по концу предложения."""
    paragraphs: list[Paragraph] = []
    current: list[Segment] = []
    for seg in segments:
        current.append(seg)
        duration = seg.end - current[0].start
        ends_sentence = seg.text.rstrip().endswith((".", "!", "?", "…"))
        if duration >= max_s or (duration >= min_s and ends_sentence):
            paragraphs.append(_merge(current))
            current = []
    if current:
        paragraphs.append(_merge(current))
    return paragraphs


def _merge(segments: list[Segment]) -> Paragraph:
    return Paragraph(
        start=segments[0].start,
        end=segments[-1].end,
        fr=" ".join(s.text for s in segments),
    )


def batch_by_chars(paragraphs: list[Paragraph], limit: int = TRANSLATE_BATCH_CHARS) -> list[list[Paragraph]]:
    batches: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p.fr) > limit:
            batches.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p.fr)
    if current:
        batches.append(current)
    return batches


class JobManager:
    def __init__(
        self,
        transcriber_factory: Callable[[], Transcriber],
        translator,
        chunk_seconds: int = CHUNK_SECONDS,
    ):
        self.transcriber_factory = transcriber_factory
        self.translator = translator
        self.chunk_seconds = chunk_seconds
        self.jobs: dict[str, Job] = {}
        # Бесплатные лимиты маленькие, поэтому файлы обрабатываются по одному.
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def start(self, src: Path, filename: str, transcriber: Transcriber) -> Job:
        self._purge_old()
        job = Job(id=uuid.uuid4().hex, filename=filename, engine=transcriber.name)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, src, transcriber))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def _purge_old(self) -> None:
        now = time.time()
        for job_id in [j.id for j in self.jobs.values() if now - j.created > JOB_TTL_SECONDS]:
            del self.jobs[job_id]

    async def _run(self, job: Job, src: Path, transcriber: Transcriber) -> None:
        workdir = Path(tempfile.mkdtemp(prefix="mtf_"))
        try:
            async with self._lock:
                job.status = "running"
                await self._process(job, src, workdir, transcriber)
                job.status = "done"
                job.stage = "Готово"
                job.progress = 1.0
        except AppError as e:
            job.status, job.error = "error", e.message
        except Exception:
            log.exception("Job %s failed", job.id)
            job.status, job.error = "error", "Непредвиденная ошибка при обработке файла."
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            src.unlink(missing_ok=True)

    async def _process(self, job: Job, src: Path, workdir: Path, transcriber: Transcriber) -> None:
        job.stage = "Извлекаю звук…"
        job.progress = 0.02
        chunks = await split_audio(src, workdir / "chunks", self.chunk_seconds)

        segments: list[Segment] = []
        for i, chunk in enumerate(chunks):
            stage = f"Расшифровка: часть {i + 1} из {len(chunks)}"
            job.stage = stage
            segments += await transcriber.transcribe(
                chunk, offset=i * self.chunk_seconds, on_wait=self._waiter(job, stage)
            )
            job.progress = 0.05 + 0.6 * (i + 1) / len(chunks)
            job.paragraphs = group_paragraphs(segments)  # показываем текст по мере готовности

        paragraphs = group_paragraphs(segments)
        if not paragraphs:
            raise AppError("В записи не найдено речи.", status_code=400)
        job.paragraphs = paragraphs

        batches = batch_by_chars(paragraphs)
        for i, batch in enumerate(batches):
            stage = f"Перевод: часть {i + 1} из {len(batches)}"
            job.stage = stage
            translations = await self.translator.translate_batch(
                [p.fr for p in batch], "fr", "ru", on_wait=self._waiter(job, stage)
            )
            for p, ru in zip(batch, translations):
                p.ru = ru
            job.progress = 0.65 + 0.35 * (i + 1) / len(batches)

    @staticmethod
    def _waiter(job: Job, stage: str):
        def on_wait(seconds: float) -> None:
            job.stage = f"{stage} — жду бесплатный лимит, ~{int(seconds)} с"

        return on_wait
