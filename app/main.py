from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.translator import GeminiTranslator, TranslationError, detect_source

load_dotenv()

MAX_TEXT_LENGTH = 20_000

app = FastAPI(title="MultiTool French")

_translator = GeminiTranslator()


def get_translator() -> GeminiTranslator:
    return _translator


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    direction: Literal["auto", "fr-ru", "ru-fr"] = "auto"


class TranslateResponse(BaseModel):
    translation: str
    source: str
    target: str


@app.exception_handler(TranslationError)
async def translation_error_handler(_request, exc: TranslationError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.post("/api/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest, translator=Depends(get_translator)):
    text = req.text.strip()
    if not text:
        raise TranslationError("Введите текст для перевода.", status_code=400)

    if req.direction == "auto":
        source = detect_source(text)
    else:
        source = req.direction.split("-")[0]
    target = "ru" if source == "fr" else "fr"

    translation = await translator.translate(text, source, target)
    return TranslateResponse(translation=translation, source=source, target=target)


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)
