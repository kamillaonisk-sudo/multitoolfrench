"""Вопросы по сохранённым конспектам и расшифровкам через Gemini."""

import json
import re

from google.genai import types

from app.errors import AppError
from app.gemini import GeminiClient
from app.pdf import format_time

# Сколько текста отправляем в Gemini за один вопрос. ~4 символа на токен,
# так что это около 75 тысяч токенов — с запасом для бесплатных лимитов.
MAX_CONTEXT_CHARS = 300_000

ASK_PROMPT = """You answer the user's questions about their saved materials: summaries ("конспекты")
of recordings and texts, and transcripts of recordings (often in French) with timestamps.

The materials are below, each inside <note id="..."> tags. Some notes contain only the summary,
others also the full transcript or text.

Rules:
- Answer in Russian, clearly and to the point. Plain text, no Markdown; use short paragraphs
  and lines starting with "• " for lists.
- Use only the materials. If they do not contain the answer, say so honestly.
- When you quote French (or another language), give the original and the Russian translation.
- In "sources" list the notes you used, with the timestamp (like "12:34") of the relevant place
  in the transcript when there is one; otherwise an empty string.

MATERIALS:
{materials}"""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "note id, for example n2"},
                    "time": {"type": "string"},
                },
                "required": ["note", "time"],
            },
        },
    },
    "required": ["answer", "sources"],
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{4,}", text.lower())}


def summary_text(summary: dict) -> str:
    lines = [summary.get("overview", "")]
    for sec in summary.get("sections") or []:
        start = f"[{sec.get('start')}] " if sec.get("start") else ""
        lines.append(f"{start}{sec.get('title', '')}")
        lines += [f"  • {p}" for p in sec.get("points") or []]
    if summary.get("takeaways"):
        lines.append("Главные выводы:")
        lines += [f"  • {t}" for t in summary["takeaways"]]
    return "\n".join(line for line in lines if line)


def full_text(note: dict) -> str:
    if note.get("paragraphs"):
        return "\n".join(
            f"[{format_time(p.get('start', 0))}] {p.get('original', '')}" for p in note["paragraphs"]
        )
    return note.get("text") or ""


def build_materials(notes: list[dict], question: str, limit: int = MAX_CONTEXT_CHARS):
    """Собирает материалы в пределах лимита. Конспекты — всегда (пока влезают),
    полные тексты — сначала для записей, больше всего похожих на вопрос.
    Возвращает (текст, {короткий id: настоящий id}, сколько записей с полным текстом)."""
    ids = {f"n{i + 1}": note["id"] for i, note in enumerate(notes)}
    headers, summaries, fulls = {}, {}, {}
    for short, note in zip(ids, notes):
        attrs = {
            "id": short,
            "title": note.get("title", ""),
            "topic": note.get("topic", ""),
            "date": (note.get("created") or "")[:10],
            "file": note.get("filename") or "",
        }
        headers[short] = "<note " + " ".join(
            f'{k}="{str(v).replace(chr(34), chr(39))}"' for k, v in attrs.items() if v
        ) + ">"
        summaries[short] = "Конспект:\n" + summary_text(note.get("summary") or {})
        fulls[short] = full_text(note)

    # Порядок: самые похожие на вопрос — первыми.
    q = _words(question)
    order = sorted(
        ids,
        key=lambda s: -len(q & _words(summaries[s] + " " + headers[s] + " " + fulls[s][:20_000])),
    )

    used = 0
    included, with_full = [], set()
    for short in order:
        size = len(headers[short]) + len(summaries[short]) + 20
        if used + size > limit:
            continue
        included.append(short)
        used += size
    for short in order:
        if short in included and fulls[short] and used + len(fulls[short]) + 30 <= limit:
            with_full.add(short)
            used += len(fulls[short]) + 30

    parts = []
    for short in ids:  # в исходном порядке
        if short not in included:
            continue
        body = summaries[short]
        if short in with_full:
            label = "Расшифровка (оригинал)" if notes[int(short[1:]) - 1].get("paragraphs") else "Текст"
            body += f"\n\n{label}:\n{fulls[short]}"
        parts.append(f"{headers[short]}\n{body}\n</note>")
    return "\n\n".join(parts), {s: ids[s] for s in included}, len(with_full)


class GeminiAsker(GeminiClient):
    async def ask(self, question: str, notes: list[dict], history: list[dict]) -> dict:
        if not notes:
            raise AppError("Нет записей, по которым можно задать вопрос.", status_code=400)
        materials, ids, full_count = build_materials(notes, question)
        if not ids:
            raise AppError("Записи слишком большие, чтобы задать по ним вопрос.", status_code=400)

        contents = []
        for turn in history[-6:]:
            contents.append(types.Content(role="user", parts=[types.Part(text=turn["question"])]))
            contents.append(types.Content(role="model", parts=[types.Part(text=turn["answer"])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

        config = types.GenerateContentConfig(
            system_instruction=ASK_PROMPT.format(materials=materials),
            temperature=0.2,
            response_mime_type="application/json",
            response_json_schema=ANSWER_SCHEMA,
        )
        raw = await self.generate(contents, config, max_wait=60)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AppError("Gemini ответил в неожиданном формате. Попробуйте ещё раз.") from e

        answer = str(data.get("answer", "")).strip() if isinstance(data, dict) else ""
        if not answer:
            raise AppError("Gemini не дал ответа. Попробуйте переформулировать вопрос.")

        sources, seen = [], set()
        for s in data.get("sources") or []:
            if not isinstance(s, dict):
                continue
            real = ids.get(str(s.get("note", "")).strip())
            time = str(s.get("time", "")).strip()
            if real and (real, time) not in seen:
                seen.add((real, time))
                sources.append({"note_id": real, "time": time})

        return {
            "answer": answer,
            "sources": sources,
            "notes_used": len(ids),
            "notes_total": len(notes),
            "full_texts": full_count,
        }
