"""Извлечение звука из аудио/видео и нарезка на части с помощью ffmpeg."""

import asyncio
import shutil
import subprocess
from pathlib import Path

from app.errors import AppError

CHUNK_SECONDS = 600  # 10 минут: ~2.4 МБ в mp3 32 кбит/с, укладывается в лимиты Groq и Gemini


def ffmpeg_exe() -> str:
    """Системный ffmpeg, а если его нет — версия из пакета imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


async def split_audio(src: Path, out_dir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[Path]:
    """Делает из файла моно-mp3 16 кГц и режет на части по `chunk_seconds` секунд."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
        str(out_dir / "chunk_%04d.mp3"),
    ]
    # Через поток, а не asyncio-подпроцесс: так работает и на Windows с uvicorn --reload.
    result = await asyncio.to_thread(_run, cmd)
    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    if result.returncode != 0 or not chunks:
        raise AppError(
            "Не удалось извлечь звук из файла. Возможно, в нём нет звуковой дорожки "
            "или формат не поддерживается.",
            status_code=400,
        )
    return chunks
