"""Расшифровка речи на любом языке: Groq (Whisper) или Gemini."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import groq
from google.genai import types

from app.errors import AppError
from app.gemini import GeminiClient
from app.retry import WaitCallback, with_retries


@dataclass
class Segment:
    start: float  # секунды от начала записи
    end: float
    text: str


# Языки, которые можно выбрать вручную. "auto" — определить автоматически.
LANGUAGES = {
    "auto": "Определить автоматически",
    "fr": "Французский",
    "en": "Английский",
    "es": "Испанский",
    "it": "Итальянский",
    "de": "Немецкий",
    "pt": "Португальский",
    "ru": "Русский",
    "uk": "Украинский",
}

LANGUAGE_NAMES_EN = {
    "fr": "French", "en": "English", "es": "Spanish", "it": "Italian",
    "de": "German", "pt": "Portuguese", "ru": "Russian", "uk": "Ukrainian",
}


class Transcriber(Protocol):
    name: str

    async def transcribe(
        self,
        path: Path,
        offset: float,
        on_wait: WaitCallback | None = None,
        language: str = "auto",
    ) -> list[Segment]: ...


# Whisper на тишине иногда «слышит» титры из обучающих данных.
_HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|sous titr|merci d'avoir regardé|abonnez-vous", re.IGNORECASE
)


def _looks_like_hallucination(seg: dict) -> bool:
    text = seg.get("text", "")
    if "amara.org" in text.lower():
        return True
    return bool(_HALLUCINATIONS.search(text)) and seg.get("no_speech_prob", 0) > 0.3


class GroqTranscriber:
    name = "Groq Whisper"

    def __init__(self, api_key: str, model: str | None = None):
        self.model = model or os.getenv("GROQ_MODEL") or "whisper-large-v3"
        # Повторы делаем сами (с ожиданием лимита), поэтому встроенные отключены.
        self.client = groq.AsyncGroq(api_key=api_key, max_retries=0)

    @staticmethod
    def _retry_delay(e: Exception) -> float | None:
        if isinstance(e, groq.RateLimitError):
            try:
                return float(e.response.headers.get("retry-after", "")) + 1
            except ValueError:
                return 30.0
        if isinstance(e, (groq.InternalServerError, groq.APIConnectionError)):
            return 5.0
        return None

    async def transcribe(self, path, offset, on_wait=None, language="auto"):
        audio = path.read_bytes()
        # Без параметра language Whisper определяет язык сам.
        extra = {} if language == "auto" else {"language": language}

        async def call():
            return await self.client.audio.transcriptions.create(
                file=(path.name, audio),
                model=self.model,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                temperature=0,
                **extra,
            )

        try:
            resp = await with_retries(call, self._retry_delay, 1800, on_wait)
        except groq.AuthenticationError as e:
            raise AppError("Groq не принял ключ. Проверьте GROQ_API_KEY в файле .env.") from e
        except groq.RateLimitError as e:
            raise AppError("Бесплатный лимит Groq исчерпан. Попробуйте позже.") from e
        except groq.APIConnectionError as e:
            raise AppError("Нет связи с Groq. Проверьте интернет.") from e
        except groq.APIStatusError as e:
            raise AppError(f"Ошибка Groq ({e.status_code}).") from e

        return parse_groq_segments(resp.to_dict(), offset)


def parse_groq_segments(data: dict, offset: float) -> list[Segment]:
    segments = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text or _looks_like_hallucination(seg):
            continue
        segments.append(Segment(offset + seg["start"], offset + seg["end"], text))
    return segments


GEMINI_TRANSCRIBE_PROMPT = """Transcribe this audio verbatim, in the language it is spoken in.
{language_hint}
Split it into short consecutive phrases (one sentence or less each).
For each phrase give its start time from the beginning of this audio as MM:SS.
Do not translate, summarize or skip anything. Skip music and silence."""

GEMINI_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "MM:SS"},
            "text": {"type": "string"},
        },
        "required": ["start", "text"],
    },
}


def _parse_timestamp(value: str) -> float:
    parts = [p for p in re.split(r"[:.]", value.strip()) if p.isdigit()]
    seconds = 0.0
    for p in parts[:3]:
        seconds = seconds * 60 + int(p)
    return seconds


def parse_gemini_segments(items: list[dict], offset: float, chunk_seconds: float) -> list[Segment]:
    rows = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if text:
            rows.append((min(_parse_timestamp(str(item.get("start", "0"))), chunk_seconds), text))
    segments = []
    for i, (start, text) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else min(start + 5, chunk_seconds)
        segments.append(Segment(offset + start, offset + max(end, start), text))
    return segments


class GeminiTranscriber(GeminiClient):
    name = "Gemini"

    def __init__(self, *args, chunk_seconds: float = 600, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunk_seconds = chunk_seconds

    async def transcribe(self, path, offset, on_wait=None, language="auto"):
        if language in LANGUAGE_NAMES_EN:
            hint = f"The speech is mostly in {LANGUAGE_NAMES_EN[language]}."
        else:
            hint = "If several languages are spoken, write each part in its own language."
        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_json_schema=GEMINI_SCHEMA,
        )
        contents = [
            types.Part.from_bytes(data=path.read_bytes(), mime_type="audio/mp3"),
            GEMINI_TRANSCRIBE_PROMPT.format(language_hint=hint),
        ]
        raw = await self.generate(contents, config, max_wait=1800, on_wait=on_wait)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AppError("Gemini вернул расшифровку в неожиданном формате.") from e
        if not isinstance(items, list):
            raise AppError("Gemini вернул расшифровку в неожиданном формате.")
        return parse_gemini_segments(items, offset, self.chunk_seconds)


def make_transcriber(chunk_seconds: float = 600) -> Transcriber:
    """Groq, если задан GROQ_API_KEY, иначе Gemini. TRANSCRIBE_ENGINE=groq|gemini — выбрать явно."""
    engine = os.getenv("TRANSCRIBE_ENGINE", "auto").lower()
    groq_key = os.getenv("GROQ_API_KEY", "")
    if engine == "groq" or (engine == "auto" and groq_key):
        if not groq_key:
            raise AppError("Не задан GROQ_API_KEY в файле .env.", status_code=500)
        return GroqTranscriber(groq_key)
    return GeminiTranscriber(chunk_seconds=chunk_seconds)
