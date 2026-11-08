import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "0000-test")
    return TestClient(app, base_url="https://testserver")


def test_no_password_means_open(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert TestClient(app).get("/").status_code == 200


def test_page_redirects_to_login(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login.html"


def test_login_page_and_styles_are_open(client):
    assert client.get("/login.html").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_api_requires_login(client):
    res = client.post("/api/translate", json={"text": "Bonjour"})
    assert res.status_code == 401
    assert "пароль" in res.json()["detail"]


def test_wrong_password(client):
    res = client.post("/api/login", json={"password": "nope"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Неверный пароль."
    assert client.get("/", follow_redirects=False).status_code == 303


def test_right_password_sets_cookie(client):
    res = client.post("/api/login", json={"password": "0000-test"})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "0000-test" not in cookie
    assert client.get("/").status_code == 200
    assert client.get("/api/jobs/nope").status_code == 404  # уже не 401


def test_non_ascii_password(client):
    assert client.post("/api/login", json={"password": "пароль"}).status_code == 401
