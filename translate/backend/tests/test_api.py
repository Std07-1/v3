"""Тести API: читаються як специфікація поведінки сервісу."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_engine_and_cache(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["engine"] == "mock"
    assert body["cache"] == "memory"


def test_languages_includes_target_pairs(client: TestClient) -> None:
    resp = client.get("/api/languages")
    assert resp.status_code == 200
    codes = {lang["code"] for lang in resp.json()["languages"]}
    assert {"uk", "cs", "hu", "en", "ru"} <= codes


def test_translate_routes_through_engine(client: TestClient) -> None:
    resp = client.post(
        "/api/translate",
        json={"text": "Привіт", "source": "uk", "target": "cs"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "uk"
    assert body["target"] == "cs"
    assert body["engine"] == "mock"
    assert body["cached"] is False
    assert "Привіт" in body["text"]


def test_translate_second_call_is_cached(client: TestClient) -> None:
    payload = {"text": "Дякую", "source": "uk", "target": "hu"}
    first = client.post("/api/translate", json=payload)
    second = client.post("/api/translate", json=payload)
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert first.json()["text"] == second.json()["text"]


def test_translate_auto_detects_ukrainian(client: TestClient) -> None:
    resp = client.post(
        "/api/translate",
        json={"text": "Ти відпочиваєш, сестричко?", "source": "auto", "target": "en"},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "uk"


def test_translate_same_language_is_passthrough(client: TestClient) -> None:
    resp = client.post(
        "/api/translate",
        json={"text": "ahoj", "source": "cs", "target": "cs"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "ahoj"


def test_translate_rejects_unknown_target(client: TestClient) -> None:
    resp = client.post(
        "/api/translate",
        json={"text": "test", "source": "en", "target": "xx"},
    )
    assert resp.status_code == 400


def test_translate_rejects_empty_text(client: TestClient) -> None:
    resp = client.post(
        "/api/translate",
        json={"text": "   ", "source": "en", "target": "uk"},
    )
    assert resp.status_code == 400
