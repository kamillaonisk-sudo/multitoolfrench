"""Перевод текста между французским и русским через Gemini API."""

import os
import re

from google import genai
from google.genai import errors, types

# SDK ходит в сеть через httpx2 (в старых версиях — через httpx).
_NETWORK_ERRORS: tuple[type[Exception], ...] = ()
for _mod in ("httpx2", "httpx"):
    try:
        _NETWORK_ERRORS += (__import__(_mod).HTTPError,)
    except ImportError:
        pass

LANG_NAMES = {"fr": "French", "ru": "Russian"}

SYSTEM_PROMPT = """You are a professional translator between {source} and {target}.
Translate the user's text from {source} into {target}.
- Keep the meaning, tone and register (formal/informal) of the original.
- Keep the paragraph breaks and list formatting.
- Translate idioms by meaning, not word for word.
- Leave names, brands and URLs as they are.
- Output only the translation, with no comments, notes or quotes around it."""


class TranslationError(Exception):
    """Ошибка с понятным пользователю сообщением на русском."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def detect_source(text: str) -> str:
    """Определяет язык по буквам: больше кириллицы — русский, иначе французский."""
    cyrillic = len(re.findall(r"[а-яёА-ЯЁ]", text))
    latin = len(re.findall(r"[a-zA-ZÀ-ÿœŒ]", text))
    return "ru" if cyrillic > latin else "fr"


class GeminiTranslator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest"
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if not self.api_key:
            raise TranslationError(
                "Не задан ключ Gemini. Добавьте GEMINI_API_KEY в файл .env "
                "(ключ можно получить бесплатно на aistudio.google.com/apikey).",
                status_code=500,
            )
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def translate(self, text: str, source: str, target: str) -> str:
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT.format(
                source=LANG_NAMES[source], target=LANG_NAMES[target]
            ),
            temperature=0.2,
        )
        try:
            response = await client.aio.models.generate_content(
                model=self.model, contents=text, config=config
            )
        except errors.APIError as e:
            raise TranslationError(_explain_api_error(e, self.model)) from e
        except _NETWORK_ERRORS as e:
            raise TranslationError(
                "Нет связи с Gemini. Проверьте интернет и попробуйте ещё раз."
            ) from e

        result = (response.text or "").strip()
        if not result:
            raise TranslationError(
                "Gemini вернул пустой ответ. Попробуйте ещё раз или измените текст."
            )
        return result


def _explain_api_error(e: errors.APIError, model: str) -> str:
    details = (e.message or "").lower()
    if e.code == 429:
        return (
            "Бесплатный лимит запросов Gemini исчерпан. "
            "Подождите минуту (или до завтра, если кончился дневной лимит)."
        )
    if e.code in (401, 403) or "api key" in details:
        return "Gemini не принял ключ. Проверьте GEMINI_API_KEY в файле .env."
    if e.code == 404:
        return f"Модель «{model}» не найдена. Укажите другую в GEMINI_MODEL в файле .env."
    return f"Ошибка Gemini ({e.code}): {e.message or 'неизвестная ошибка'}"
