"""Повтор запросов, когда упираемся в бесплатный лимит или сервис перегружен."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# Колбэк получает число секунд, которое придётся подождать.
WaitCallback = Callable[[float], None]


async def with_retries(
    call: Callable[[], Awaitable[T]],
    retry_delay_for: Callable[[Exception], float | None],
    max_total_wait: float,
    on_wait: WaitCallback | None = None,
    max_attempts: int = 10,
) -> T:
    """Вызывает `call`; если `retry_delay_for` говорит, что ошибку можно переждать,
    ждёт и пробует снова, пока суммарное ожидание не превысит `max_total_wait`
    и число попыток не превысит `max_attempts`."""
    waited = 0.0
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
        except Exception as e:
            delay = retry_delay_for(e)
            if delay is None or waited + delay > max_total_wait or attempt >= max_attempts:
                raise
            if on_wait:
                on_wait(delay)
            await asyncio.sleep(delay)
            waited += delay
