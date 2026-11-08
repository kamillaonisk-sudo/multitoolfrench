import os
import tempfile
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from app.errors import AppError  # noqa: E402
from app.jobs import JobManager, transcript_text  # noqa: E402
from app.summary import GeminiSummarizer  # noqa: E402
from app.transcribe import make_transcriber  # noqa: E402
from app.translator import GeminiTranslator, detect_source  # noqa: E402

MAX_TEXT_LENGTH = 20_000
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2000"))

app = FastAPI(title="MultiTool French")

_translator = GeminiTranslator()
_jobs = JobManager(make_transcriber, _translator)
_summarizer = GeminiSummarizer()


def get_translator() -> GeminiTranslator:
    return _translator


def get_jobs() -> JobManager:
    return _jobs


def get_summarizer() -> GeminiSummarizer:
    return _summarizer


@app.exception_handler(AppError)
async def app_error_handler(_request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


# ---------- Переводчик ----------

class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    direction: Literal["auto", "fr-ru", "ru-fr"] = "auto"


class TranslateResponse(BaseModel):
    translation: str
    source: str
    target: str


@app.post("/api/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest, translator=Depends(get_translator)):
    text = req.text.strip()
    if not text:
        raise AppError("Введите текст для перевода.", status_code=400)

    if req.direction == "auto":
        source = detect_source(text)
    else:
        source = req.direction.split("-")[0]
    target = "ru" if source == "fr" else "fr"

    translation = await translator.translate(text, source, target)
    return TranslateResponse(translation=translation, source=source, target=target)


# ---------- Расшифровка ----------

@app.post("/api/transcribe")
async def transcribe(file: UploadFile, jobs: JobManager = Depends(get_jobs)):
    # Проверяем ключи до загрузки большого файла на диск.
    transcriber = jobs.transcriber_factory()
    for service in (transcriber, jobs.translator):
        if hasattr(service, "ensure_configured"):
            service.ensure_configured()

    suffix = Path(file.filename or "").suffix[:10]
    fd, tmp_name = tempfile.mkstemp(prefix="mtf_upload_", suffix=suffix)
    tmp = Path(tmp_name)
    size = 0
    limit = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise AppError(f"Файл больше {MAX_UPLOAD_MB} МБ.", status_code=413)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise AppError("Файл пустой.", status_code=400)

    job = jobs.start(tmp, file.filename or "запись", transcriber)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, jobs: JobManager = Depends(get_jobs)):
    job = jobs.get(job_id)
    if job is None:
        raise AppError(
            "Задача не найдена. Возможно, сервер перезапускался — загрузите файл снова.",
            status_code=404,
        )
    return job.to_dict()


# ---------- Конспект ----------

class SummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH * 2)


@app.post("/api/summary")
async def summary(req: SummaryRequest, summarizer=Depends(get_summarizer)):
    if not req.text.strip():
        raise AppError("Нет текста для конспекта.", status_code=400)
    return await summarizer.summarize(req.text.strip())


@app.post("/api/jobs/{job_id}/summary")
async def job_summary(
    job_id: str, jobs: JobManager = Depends(get_jobs), summarizer=Depends(get_summarizer)
):
    job = jobs.get(job_id)
    if job is None:
        raise AppError("Задача не найдена. Загрузите файл снова.", status_code=404)
    if job.status != "done":
        raise AppError("Конспект можно сделать, когда расшифровка закончится.", status_code=409)
    if job.summary is None:
        job.summary = await summarizer.summarize(transcript_text(job.paragraphs), max_wait=600)
    return job.summary


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)
