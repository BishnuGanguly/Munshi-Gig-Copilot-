"""Choosing providers by configuration, and chaining them."""
import pytest

from app.errors import LlmUnavailableError
from app.llm import FallbackLlm, KNOWN, PRESETS, build_provider, get_provider
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.base import ToolCall
from tests.fakes import FakeProvider


# ---------------------------------------------------------------- configuration
def test_off_by_default():
    assert get_provider() is None


@pytest.mark.parametrize("value", ["none", "NONE", "", "  "])
def test_off_spellings(monkeypatch, value):
    monkeypatch.setenv("BRAIN", value)
    assert get_provider() is None


def test_one_provider(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    p = get_provider()
    assert isinstance(p, OpenAICompatProvider) and p.name == "sarvam"
    assert p.url == "https://api.sarvam.ai/v1/chat/completions" and p.model == "sarvam-105b"
    assert p.auth == "header:api-subscription-key"


def test_a_chain_preserves_order(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam, anthropic")
    p = get_provider()
    assert isinstance(p, FallbackLlm) and [x.name for x in p.providers] == ["sarvam", "anthropic"]
    assert isinstance(p.providers[1], AnthropicProvider)


def test_the_sarvam_key_is_the_one_used_for_speech(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "shared-key")
    assert get_provider().api_key == "shared-key"


def test_providers_are_cached_while_config_is_unchanged(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    assert get_provider() is get_provider()               # one connection pool, reused across messages
    monkeypatch.setenv("BRAIN_MODEL", "other")
    assert get_provider().model == "other"                # a config change is noticed


def test_an_unknown_provider_fails_loudly_and_names_the_choices(monkeypatch):
    monkeypatch.setenv("BRAIN", "skynet")
    with pytest.raises(ValueError, match="Unknown BRAIN provider 'skynet'.*anthropic"):
        get_provider()


# ---------------------------------------------------------------- models and URLs
def test_model_override_precedence(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam,anthropic")
    monkeypatch.setenv("BRAIN_MODEL", "first-model")
    monkeypatch.setenv("BRAIN_MODEL_ANTHROPIC", "claude-sonnet-5-5")
    a, b = get_provider().providers
    assert a.model == "first-model"                        # BRAIN_MODEL is shorthand for the FIRST provider
    assert b.model == "claude-sonnet-5-5"                  # the second does not inherit it
    monkeypatch.setenv("BRAIN_MODEL_SARVAM", "sarvam-special")
    assert get_provider().providers[0].model == "sarvam-special"   # the specific one wins


def test_a_shared_model_name_is_not_forced_on_the_fallback(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam,anthropic")
    monkeypatch.setenv("BRAIN_MODEL", "sarvam-105b")
    assert get_provider().providers[1].model == "claude-haiku-5-5"


def test_base_url_can_be_overridden_per_provider(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.setenv("BRAIN_BASE_URL_SARVAM", "https://api.sarvam.ai/v2")
    assert get_provider().url == "https://api.sarvam.ai/v2/chat/completions"


# ---------------------------------------------------------------- any other OpenAI-style provider
def test_every_preset_builds_without_a_key():
    """Building is cheap and contacts nothing: a missing key is only an error when a call is made."""
    for name in PRESETS:
        assert build_provider(name).name == name
    assert set(PRESETS) <= set(KNOWN) and "anthropic" in KNOWN and "openai_compat" in KNOWN


def test_generic_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("BRAIN", "openai_compat")
    monkeypatch.setenv("BRAIN_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("BRAIN_API_KEY_ENV", "MY_PROVIDER_KEY")
    monkeypatch.setenv("MY_PROVIDER_KEY", "secret")
    monkeypatch.setenv("BRAIN_AUTH", "header:X-Token")
    monkeypatch.setenv("BRAIN_MODEL_OPENAI_COMPAT", "their-model")
    p = get_provider()
    assert (p.url, p.model, p.api_key, p.auth) == ("https://llm.example.com/v1/chat/completions", "their-model",
                                                  "secret", "header:X-Token")


@pytest.mark.parametrize("missing", ["BRAIN_BASE_URL", "BRAIN_MODEL_OPENAI_COMPAT"])
def test_generic_provider_says_what_is_missing(monkeypatch, missing):
    env = {"BRAIN_BASE_URL": "https://x/v1", "BRAIN_MODEL_OPENAI_COMPAT": "m"}
    env.pop(missing)
    monkeypatch.delenv("BRAIN_BASE_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="needs"):
        build_provider("openai_compat")


def test_local_ollama_needs_no_key():
    p = build_provider("ollama")
    assert p.auth == "none" and p.url == "http://localhost:11434/v1/chat/completions"


# ---------------------------------------------------------------- the chain
def down(text="down"):
    return FakeProvider(raises=LlmUnavailableError(text))


def test_the_chain_skips_a_provider_that_is_down():
    good = FakeProvider(turns=[[("help", {})]], text="hello")
    chain = FallbackLlm([down(), good])
    assert [c.name for c in chain.choose_actions("s", [], "u")] == ["help"]
    assert chain.complete("s", "u") == "hello"


def test_the_primary_answers_when_it_is_up():
    first, second = FakeProvider(turns=[[("help", {})]]), FakeProvider(turns=[[("delete_entry", {})]])
    assert FallbackLlm([first, second]).choose_actions("s", [], "u")[0].name == "help"
    assert second.requests == []                            # the fallback is never even contacted


def test_no_action_is_not_a_failure_so_the_fallback_is_not_asked():
    """A model that chose nothing exercised its judgement. Asking a second vendor would double the cost and
    hide a quality problem; only an outage moves on."""
    first, second = FakeProvider(turns=[[]]), FakeProvider(turns=[[("help", {})]])
    assert FallbackLlm([first, second]).choose_actions("s", [], "u") == []
    assert second.requests == []


def test_when_every_provider_is_down_the_errors_are_all_reported():
    chain = FallbackLlm([down("sarvam is down"), down("claude is down")])
    with pytest.raises(LlmUnavailableError, match="sarvam is down.*claude is down"):
        chain.complete("s", "u")


def test_a_non_availability_error_is_not_swallowed():
    """A bug is not an outage: it must surface, not silently roll over to the next vendor."""
    with pytest.raises(KeyError):
        FallbackLlm([FakeProvider(raises=KeyError("bug")), FakeProvider()]).complete("s", "u")


def test_an_empty_chain_is_rejected():
    with pytest.raises(ValueError):
        FallbackLlm([])


def test_toolcall_defaults():
    assert ToolCall("help").args == {}


# ---------------------------------------------------------------- startup
def test_a_misconfigured_provider_stops_the_app_at_startup_not_on_the_first_message(monkeypatch):
    from app.main import create_app
    monkeypatch.setenv("BRAIN", "skynet")
    with pytest.raises(ValueError, match="Unknown BRAIN provider"):
        create_app(":memory:")


def test_the_app_starts_with_a_provider_that_has_no_key_yet(monkeypatch):
    """Building a provider contacts nothing, so a missing key is an error only when a call is made."""
    from app.main import create_app
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    assert create_app(":memory:") is not None


# ---------------------------------------------------------------- the smoke-test script
def test_check_llm_passes_a_well_behaved_provider_and_fails_a_poor_one(monkeypatch, capsys):
    import importlib.util, json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("check_llm", Path(__file__).parent.parent / "scripts" / "check_llm.py")
    check_llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_llm)

    good = FakeProvider(turns=[[("log_entries", {"entries": [
        {"type": "income", "amount": 1400, "platform": "Swiggy"}, {"type": "expense", "amount": 250, "category": "fuel"}]})]],
        text=json.dumps({"language": "hi-Latn", "reply": "Swiggy se ₹1,400 income, petrol ₹250 kharcha. Sahi hai?"}))
    assert check_llm.check(good) is True
    assert "[ ok ] picked log_entries" in capsys.readouterr().out

    wrong = FakeProvider(turns=[[("help", {})]], text="not json")
    assert check_llm.check(wrong) is False
    out = capsys.readouterr().out
    assert "[FAIL] expected one log_entries call" in out and "render fell back to English" in out

    assert check_llm.check(FakeProvider(raises=LlmUnavailableError("401"))) is False
