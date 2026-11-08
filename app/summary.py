"""Конспект текста или расшифровки на русском через Gemini."""

import json

from google.genai import types

from app.errors import AppError
from app.gemini import GeminiClient

SUMMARY_PROMPT = """You write study notes (a "конспект") in Russian.
The user sends a text in any language. It may be a transcript of speech
where lines start with a timestamp like [12:34].

Write the notes in Russian, whatever the language of the text:
- title: a short title for the whole text, 3-8 words.
- topic: one broad subject area, 1-3 words, capitalized (for example "История", "Кулинария",
  "Французский язык", "Политика"). {topics_hint}
- tags: 2-5 short keywords (lowercase, 1-3 words each).
- overview: 2-4 sentences with the main idea of the whole text.
- sections: the text split into logical parts, in order (2-10 parts, fewer for short texts).
  - title: a short heading for the part.
  - start: the timestamp where the part begins, copied from the input (for example "12:34");
    an empty string if the input has no timestamps.
  - points: 2-6 concise bullet points with the key facts, arguments, examples, names and numbers.
- takeaways: 2-5 main conclusions or things worth remembering.

Be faithful to the text: do not add facts that are not in it.
Keep foreign names and terms in their original form where a translation would be unclear."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "topic": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "overview": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "start", "points"],
            },
        },
        "takeaways": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "topic", "tags", "overview", "sections", "takeaways"],
}


def _clean(data) -> dict:
    """Приводит ответ модели к ожидаемой форме, отбрасывая лишнее."""
    if not isinstance(data, dict):
        raise AppError("Gemini вернул конспект в неожиданном формате.")

    def strings(value) -> list[str]:
        return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []

    sections = []
    for s in data.get("sections") or []:
        if isinstance(s, dict) and (s.get("title") or s.get("points")):
            sections.append({
                "title": str(s.get("title", "")).strip(),
                "start": str(s.get("start", "")).strip(),
                "points": strings(s.get("points")),
            })
    return {
        "title": str(data.get("title", "")).strip(),
        "topic": str(data.get("topic", "")).strip() or "Без темы",
        "tags": strings(data.get("tags"))[:5],
        "overview": str(data.get("overview", "")).strip(),
        "sections": sections,
        "takeaways": strings(data.get("takeaways")),
    }


class GeminiSummarizer(GeminiClient):
    async def summarize(
        self, text: str, max_wait: float = 120, topics: list[str] | None = None
    ) -> dict:
        topics = [t.strip() for t in (topics or []) if t.strip()][:50]
        if topics:
            hint = ("The user already has these topics; if one of them fits, use it exactly: "
                    + "; ".join(topics) + ".")
        else:
            hint = ""
        config = types.GenerateContentConfig(
            system_instruction=SUMMARY_PROMPT.format(topics_hint=hint),
            temperature=0.3,
            response_mime_type="application/json",
            response_json_schema=SUMMARY_SCHEMA,
        )
        raw = await self.generate(text, config, max_wait=max_wait)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AppError("Gemini вернул конспект в неожиданном формате.") from e
        result = _clean(data)
        if not result["overview"] and not result["sections"]:
            raise AppError("Не получилось составить конспект. Попробуйте ещё раз.")
        return result
