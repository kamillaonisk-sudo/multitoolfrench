"""Общий клиент Gemini: ключ, повторы при лимитах, понятные ошибки."""

import os
import re

from google import genai
from google.genai import errors, types

from app.errors import AppError
from app.retry import WaitCallback, with_retries

DEFAULT_MODEL = "gemini-flash-lite-latest"

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
            return f"Модель «{model}» не найдена. Укажите другую в GEMINI_MODEL в файле .env."
        return f"Ошибка Gemini ({e.code}): {e.message or 'неизвестная ошибка'}"
    return "Нет связи с Gemini. Проверьте интернет и попробуйте ещё раз."


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
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

    async def generate(
        self,
        contents,
        config: types.GenerateContentConfig,
        max_wait: float = 0,
        on_wait: WaitCallback | None = None,
    ) -> str:
        """Запрос к Gemini; возвращает текст ответа. При лимитах ждёт до `max_wait` секунд."""
        client = self._get_client()

        async def call():
            return await client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            )

        try:
            response = await with_retries(call, _retry_delay, max_wait, on_wait)
        except (errors.APIError, *NETWORK_ERRORS) as e:
            raise AppError(explain_error(e, self.model)) from e

        text = (response.text or "").strip()
        if not text:
            raise AppError("Gemini вернул пустой ответ. Попробуйте ещё раз.")
        return text
