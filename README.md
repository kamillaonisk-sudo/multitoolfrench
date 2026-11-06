# MultiTool French

Веб-приложение для работы с французским:

- **Переводчик** RU ↔ FR — готово.
- **Расшифровка** аудио и видео на французском с переводом на русский — в работе.

Перевод делает Gemini API (бесплатный уровень от Google).

## Как запустить на своём компьютере

Нужен Python 3.10 или новее.

1. Получите бесплатный ключ Gemini: https://aistudio.google.com/apikey
2. Скопируйте `.env.example` в `.env` и вставьте ключ в `GEMINI_API_KEY`.
3. Установите зависимости и запустите:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

4. Откройте в браузере http://localhost:8000

## Настройки (файл `.env`)

| Переменная | Что это |
|---|---|
| `GEMINI_API_KEY` | Ключ Gemini API |
| `GEMINI_MODEL` | Модель Gemini, по умолчанию `gemini-flash-lite-latest` |

Свои бесплатные лимиты можно посмотреть на https://aistudio.google.com/rate-limit

⚠️ На бесплатном уровне Google может использовать отправленные тексты для улучшения своих продуктов. Не отправляйте конфиденциальные данные.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```
