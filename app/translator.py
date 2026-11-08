"""Перевод текста между французским и русским через Gemini API."""

import json
import re

from google.genai import types

from app.errors import AppError
from app.gemini import GeminiClient
from app.retry import WaitCallback

# Старое имя оставлено для совместимости.
TranslationError = AppError

LANG_NAMES = {
    "fr": "French", "ru": "Russian", "en": "English", "es": "Spanish", "it": "Italian",
    "de": "German", "pt": "Portuguese", "uk": "Ukrainian",
    "auto": "the original language (detect it)",
}

SYSTEM_PROMPT = """You are a professional translator between {source} and {target}.
Translate the user's text from {source} into {target}.
- Keep the meaning, tone and register (formal/informal) of the original.
- Keep the paragraph breaks and list formatting.
- Translate idioms by meaning, not word for word.
- Leave names, brands and URLs as they are.
- Output only the translation, with no comments, notes or quotes around it."""

BATCH_PROMPT = """You are a professional translator into {target}.
The user sends a JSON array of consecutive paragraphs from a transcript of speech in {source}.
Translate every paragraph into natural, fluent {target}.
- If a paragraph is already in {target}, return it unchanged.
- Return a JSON array of strings with exactly the same number of items, in the same order.
- Item N of your answer must be the translation of item N of the input.
- Use the neighbouring paragraphs for context, but do not move text between items.
- Speech transcripts may have small recognition errors: translate the intended meaning.
- Leave names, brands and URLs as they are."""


def detect_source(text: str) -> str:
    """Определяет язык по буквам: больше кириллицы — русский, иначе французский."""
    cyrillic = len(re.findall(r"[а-яёА-ЯЁ]", text))
    latin = len(re.findall(r"[a-zA-ZÀ-ÿœŒ]", text))
    return "ru" if cyrillic > latin else "fr"


class GeminiTranslator(GeminiClient):
    async def translate(self, text: str, source: str, target: str, max_wait: float = 15) -> str:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT.format(
                source=LANG_NAMES[source], target=LANG_NAMES[target]
            ),
            temperature=0.2,
        )
        return await self.generate(text, config, max_wait=max_wait)

    async def translate_batch(
        self,
        texts: list[str],
        source: str | None,
        target: str,
        max_wait: float = 1800,
        on_wait: WaitCallback | None = None,
    ) -> list[str]:
        """Переводит список абзацев за один запрос, сохраняя порядок и количество.
        source=None — язык оригинала определяет модель."""
        source_name = LANG_NAMES.get(source or "", "any language (detect it)")
        config = types.GenerateContentConfig(
            system_instruction=BATCH_PROMPT.format(source=source_name, target=LANG_NAMES[target]),
            temperature=0.2,
            response_mime_type="application/json",
            response_json_schema={"type": "array", "items": {"type": "string"}},
        )
        raw = await self.generate(
            json.dumps(texts, ensure_ascii=False), config, max_wait=max_wait, on_wait=on_wait
        )
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = None
        if isinstance(result, list) and len(result) == len(texts):
            return [str(r).strip() for r in result]

        # Модель сбилась с количеством абзацев — переводим по одному.
        return [
            await self.translate(t, source or "auto", target, max_wait=max_wait) for t in texts
        ]
