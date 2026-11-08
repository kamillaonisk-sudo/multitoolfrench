"""Общий клиент Gemini: ключ, повторы при лимитах, понятные ошибки."""

import asyncio
import logging
import os
import re

from google import genai
from google.genai import errors, types

from app.errors import AppError
from app.retry import WaitCallback, with_retries

log = logging.getLogger("uvicorn.error")

# Если модель не задана (или заданной нет), берём из списка доступных моделей
# первую подходящую: Flash-Lite (больше бесплатный лимит), затем Flash.
_EXCLUDE = ("tts", "image", "live", "embedding", "audio", "computer", "robotics", "aqa", "learnlm")

# Выбранная модель для каждого ключа, чтобы не запрашивать список каждый раз.
_resolved_models: dict[str, str] = {}
_resolve_lock: asyncio.Lock | None = None


def _version(name: str) -> float:
    m = re.match(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def choose_model(names: list[str]) -> str | None:
    """Выбирает модель из списка доступных (имена без префикса models/)."""
    candidates = [
        n for n in names
        if n.startswith("gemini") and not any(word in n for word in _EXCLUDE)
    ]

    def rank(name: str):
        is_preview = "preview" in name or "exp" in name
        if "flash-lite" in name:
            family = 0
        elif "flash" in name:
            family = 1
        else:
            family = 2
        # Сначала стабильные, потом семейство, потом самая новая версия.
        return (is_preview, family, -_version(name), len(name))

    candidates.sort(key=rank)
    return candidates[0] if candidates else None

# SDK ходит в сеть через httpx2 (в старых версиях — через httpx).
NETWORK_ERRORS: tuple[type[Exception], ...] = ()
for _mod in ("httpx2", "httpx"):
    try:
        NETWORK_ERRORS += (__import__(_mod).HTTPError,)
    except ImportError:
        pass


def _retry_delay(e: Exception) -> float | None:
    if isinstance(e, errors.APIError):
        if e.code == 429:
            # Google обычно подсказывает, сколько ждать: "retryDelay": "37s"
            m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", str(e.details))
            return float(m.group(1)) + 1 if m else 30.0
        if e.code in (500, 503):
            return 10.0
        return None
    if isinstance(e, NETWORK_ERRORS):
        return 5.0
    return None


def explain_error(e: Exception, model: str) -> str:
    if isinstance(e, errors.APIError):
        details = (e.message or "").lower()
        if e.code == 429:
            return (
                "Бесплатный лимит запросов Gemini исчерпан. "
                "Подождите (или до завтра, если кончился дневной лимит)."
            )
        if e.code in (401, 403) or "api key" in details:
            return "Gemini не принял ключ. Проверьте GEMINI_API_KEY в файле .env."
        if e.code == 404:
            return (
                f"Модель «{model}» не найдена, и подобрать другую не получилось. "
                "Укажите модель в GEMINI_MODEL (список: aistudio.google.com)."
            )
        return f"Ошибка Gemini ({e.code}): {e.message or 'неизвестная ошибка'}"
    return "Нет связи с Gemini. Проверьте интернет и попробуйте ещё раз."


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        # Пусто — модель подбирается автоматически.
        self.model = model or os.getenv("GEMINI_MODEL", "").strip()
        self._client: genai.Client | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise AppError(
                "Не задан ключ Gemini. Добавьте GEMINI_API_KEY в файл .env "
                "(ключ можно получить бесплатно на aistudio.google.com/apikey).",
                status_code=500,
            )

    def _get_client(self) -> genai.Client:
        self.ensure_configured()
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def _auto_model(self, client: genai.Client) -> str:
        """Подбирает модель по списку доступных для этого ключа."""
        global _resolve_lock
        if self.api_key in _resolved_models:
            return _resolved_models[self.api_key]
        if _resolve_lock is None:
            _resolve_lock = asyncio.Lock()
        async with _resolve_lock:
            if self.api_key not in _resolved_models:
                names = []
                async for m in await client.aio.models.list():
                    if "generateContent" in (m.supported_actions or []):
                        names.append((m.name or "").removeprefix("models/"))
                chosen = choose_model(names)
                if not chosen:
                    raise AppError("Не нашлось ни одной подходящей модели Gemini для этого ключа.")
                log.info("Gemini: выбрана модель %s", chosen)
                _resolved_models[self.api_key] = chosen
        return _resolved_models[self.api_key]

    async def generate(
        self,
        contents,
        config: types.GenerateContentConfig,
        max_wait: float = 0,
        on_wait: WaitCallback | None = None,
    ) -> str:
        """Запрос к Gemini; возвращает текст ответа. При лимитах ждёт до `max_wait` секунд."""
        client = self._get_client()
        model = self.model

        async def call():
            return await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )

        try:
            if not model:
                model = await self._auto_model(client)
            try:
                response = await with_retries(call, _retry_delay, max_wait, on_wait)
            except errors.APIError as e:
                if e.code != 404 or not self.model:
                    raise
                # Заданной модели нет — переходим на автоматический выбор.
                log.warning("Gemini: модель %s не найдена, подбираю другую", self.model)
                self.model = ""
                model = await self._auto_model(client)
                response = await with_retries(call, _retry_delay, max_wait, on_wait)
        except (errors.APIError, *NETWORK_ERRORS) as e:
            raise AppError(explain_error(e, model or "авто")) from e

        text = (response.text or "").strip()
        if not text:
            raise AppError("Gemini вернул пустой ответ. Попробуйте ещё раз.")
        return text
