"""Простой вход по паролю. Включается, если задана переменная APP_PASSWORD."""

import hashlib
import hmac
import os

COOKIE_NAME = "mtf_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 дней

# Страницы и адреса, доступные без пароля.
OPEN_PATHS = {"/login.html", "/api/login", "/style.css"}


def app_password() -> str:
    return os.getenv("APP_PASSWORD", "")


def cookie_value(password: str) -> str:
    return hmac.new(password.encode(), b"multitoolfrench-login", hashlib.sha256).hexdigest()


def password_matches(given: str, password: str) -> bool:
    return hmac.compare_digest(given.encode(), password.encode())


def is_logged_in(cookie: str | None) -> bool:
    password = app_password()
    if not password:
        return True
    return hmac.compare_digest((cookie or "").encode(), cookie_value(password).encode())
