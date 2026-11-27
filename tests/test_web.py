"""The main screen, the key banner, and the Docker setup the organisers start the app from."""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def client():
    return TestClient(create_app(":memory:"))


# ---------------------------------------------------------------- the main screen
def test_root_is_the_app_not_a_default_server_page():
    r = client().get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "<title>Munshi" in html and 'id="mic"' in html and 'id="text"' in html and 'id="status"' in html
    assert "Swagger" not in html and "detail" not in html[:200]


def test_the_page_is_self_contained():
    """No CDN, no external script or stylesheet: it must work on a judge's machine as shipped."""
    html = client().get("/").text
    assert not re.search(r'<(script|link)[^>]+(src|href)="https?://', html)


def test_rider_text_is_never_injected_as_html():
    html = client().get("/").text
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html


def test_the_page_drives_the_real_endpoints():
    html = client().get("/").text
    assert 'fetch("/process"' in html and 'fetch("/status")' in html


# ---------------------------------------------------------------- the key banner
def test_status_with_no_keys_says_key_not_set():
    s = client().get("/status").json()
    assert set(s) == {"model", "speech_to_text", "voice"}
    assert s["model"] == {"provider": "none", "ready": False,
                          "message": "Model not configured (BRAIN=none): messages cannot be understood."}
    assert s["speech_to_text"]["ready"] is False and "key not set" in s["speech_to_text"]["message"]
    assert s["voice"]["ready"] is False and "key not set" in s["voice"]["message"]


def test_status_names_a_provider_whose_key_is_missing(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.setenv("STT_PROVIDER", "sarvam")
    s = client().get("/status").json()
    assert s["model"]["ready"] is False and "key not set for sarvam" in s["model"]["message"]
    assert s["speech_to_text"]["message"] == "Speech-to-text: Sarvam key not set."


def test_status_when_configured_and_never_leaks_a_key(monkeypatch):
    secret = "sk_live_DO_NOT_LEAK_1234567890"
    for k, v in {"BRAIN": "sarvam", "SARVAM_API_KEY": secret, "STT_PROVIDER": "sarvam", "TTS_PROVIDER": "sarvam"}.items():
        monkeypatch.setenv(k, v)
    r = client().get("/status")
    assert r.json()["model"] == {"provider": "sarvam", "ready": True, "message": "Model: sarvam."}
    assert r.json()["speech_to_text"]["ready"] and r.json()["voice"]["ready"]
    assert secret not in r.text and "sk_" not in r.text


def test_a_keyless_local_server_is_ready(monkeypatch):
    monkeypatch.setenv("BRAIN", "openai_compat")
    monkeypatch.setenv("BRAIN_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("BRAIN_AUTH", "none")
    monkeypatch.setenv("BRAIN_MODEL_OPENAI_COMPAT", "llama3.1")
    assert client().get("/status").json()["model"]["ready"] is True


def test_the_api_still_works_beside_the_page():
    c = client()
    assert c.get("/health").json() == {"status": "ok"}
    r = c.post("/process", data={"user_id": "w1", "text": "hello"}).json()
    assert "understanding_unavailable" in r["warnings"]          # keyless: an honest answer, not a crash


# ---------------------------------------------------------------- the Docker setup
def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_compose_port_matches_the_image():
    svc = compose()["services"]["munshi"]
    host, container = svc["ports"][0].split(":")
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert host == container == "3000"
    assert "EXPOSE 3000" in dockerfile and '"--port", "3000"' in dockerfile and '"0.0.0.0"' in dockerfile


def test_compose_starts_without_a_dotenv_or_any_key():
    """Every value has a default, and there is no env_file: a missing .env can never stop the start."""
    svc = compose()["services"]["munshi"]
    assert "env_file" not in svc
    for name, value in svc["environment"].items():
        if isinstance(value, str) and "${" in value:
            assert ":-" in value, f"{name} has no default"
    env = svc["environment"]
    assert env["BRAIN"] == "${BRAIN:-none}" and env["SARVAM_API_KEY"] == "${SARVAM_API_KEY:-}"


def test_secrets_are_kept_out_of_the_image():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore and ".git" in ignore and "data" in ignore


def test_the_image_pins_its_tools():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.11-slim" in dockerfile and "uv==0.11.32" in dockerfile and "--frozen" in dockerfile
    assert "USER munshi" in dockerfile                                 # not root


def test_every_documented_variable_is_passed_through():
    env = compose()["services"]["munshi"]["environment"]
    for name in ("BRAIN", "SARVAM_API_KEY", "STT_PROVIDER", "TTS_PROVIDER"):
        assert name in env
