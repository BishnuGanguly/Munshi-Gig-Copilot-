"""Choosing providers by configuration, and chaining them."""
import pytest

from app.errors import LlmUnavailableError
from app.llm import FallbackLlm, KNOWN, PRESETS, build_provider, get_provider
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.base import ToolCall
from tests.fakes import FakeProvider


# ---------------------------------------------------------------- configuration
def second_provider(monkeypatch, model="their-model"):
    """A second provider for chain tests: any OpenAI-style server, configured purely by environment."""
    monkeypatch.setenv("BRAIN_BASE_URL", "https://other.example.com/v1")
    monkeypatch.setenv("BRAIN_API_KEY_ENV", "OTHER_KEY")
    monkeypatch.setenv("OTHER_KEY", "other-secret")
    monkeypatch.setenv("BRAIN_MODEL_OPENAI_COMPAT", model)


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
    second_provider(monkeypatch)
    monkeypatch.setenv("BRAIN", "sarvam, openai_compat")
    p = get_provider()
    assert isinstance(p, FallbackLlm) and [x.name for x in p.providers] == ["sarvam", "openai_compat"]
    assert isinstance(p.providers[1], OpenAICompatProvider) and p.providers[1].url.startswith("https://other.example.com")


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
    with pytest.raises(ValueError, match="Unknown BRAIN provider 'skynet'.*sarvam"):
        get_provider()


# ---------------------------------------------------------------- models and URLs
def test_model_override_precedence(monkeypatch):
    second_provider(monkeypatch, model="second-model")
    monkeypatch.setenv("BRAIN", "sarvam,openai_compat")
    monkeypatch.setenv("BRAIN_MODEL", "first-model")
    a, b = get_provider().providers
    assert a.model == "first-model"                        # BRAIN_MODEL is shorthand for the FIRST provider
    assert b.model == "second-model"                       # the second does not inherit it
    monkeypatch.setenv("BRAIN_MODEL_SARVAM", "sarvam-special")
    assert get_provider().providers[0].model == "sarvam-special"   # the specific one wins


def test_a_shared_model_name_is_not_forced_on_the_fallback(monkeypatch):
    second_provider(monkeypatch, model="their-model")
    monkeypatch.setenv("BRAIN", "sarvam,openai_compat")
    monkeypatch.setenv("BRAIN_MODEL", "sarvam-105b")
    assert get_provider().providers[1].model == "their-model"


def test_base_url_can_be_overridden_per_provider(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.setenv("BRAIN_BASE_URL_SARVAM", "https://api.sarvam.ai/v2")
    assert get_provider().url == "https://api.sarvam.ai/v2/chat/completions"


# ---------------------------------------------------------------- any other OpenAI-style provider
def test_the_sarvam_preset_builds_without_a_key():
    """Building is cheap and contacts nothing: a missing key is only an error when a call is made."""
    assert build_provider("sarvam").name == "sarvam" and set(PRESETS) == {"sarvam"}
    assert KNOWN == ["openai_compat", "sarvam"]


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


def test_a_local_server_needs_no_key(monkeypatch):
    """Any OpenAI-style server, with no auth, covers a model running on the same machine."""
    monkeypatch.setenv("BRAIN_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("BRAIN_AUTH", "none")
    monkeypatch.setenv("BRAIN_MODEL_OPENAI_COMPAT", "llama3.1")
    p = build_provider("openai_compat")
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
    chain = FallbackLlm([down("sarvam is down"), down("the second is down")])
    with pytest.raises(LlmUnavailableError, match="sarvam is down.*the second is down"):
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
        {"type": "income", "amount": 1400, "platform": "Swiggy"}, {"type": "expense", "amount": 250, "category": "fuel"}]})],
        [("analyze", {"group_by": "weekday"})]],
        texts=[json.dumps({"language": "hi-Latn", "reply": "Swiggy se ₹1,400 income, petrol ₹250 kharcha. Sahi hai?"}),
               json.dumps({"language": "hi-Latn", "reply": "Save ho gaya. Kharche ke baad ₹1,390 bache, target poora!",
                           "used": ["today_after_costs"]}),
               json.dumps({"language": "hi-Latn", "reply": "Shaniwar sabse accha, average ₹1,900. Somvar sabse kam, ₹800.",
                           "used": ["highest", "highest_value", "lowest", "lowest_value"]})])
    assert check_llm.check(good) is True
    out = capsys.readouterr().out
    assert "[ ok ] picked log_entries" in out and "[ ok ] chose an insight from the facts" in out
    assert "facts used: ['today_after_costs']" in out
    assert "[ ok ] picked analyze by weekday" in out and "[ ok ] explained the analysis" in out

    inventor = FakeProvider(turns=good.turns, texts=[good.texts[0], json.dumps(
        {"language": "hi-Latn", "reply": "Save ho gaya. Aaj ₹1,800 bache!"})])
    assert check_llm.check(inventor) is False
    assert "the model's insight was not used" in capsys.readouterr().out

    no_analyze = FakeProvider(turns=[good.turns[0], [("query_summary", {"kind": "earnings"})]], texts=good.texts)
    assert check_llm.check(no_analyze) is False
    assert "[FAIL] expected analyze with group_by weekday" in capsys.readouterr().out

    wrong = FakeProvider(turns=[[("help", {})]], text="not json")
    wrong.last_response = {"choices": [{"message": {"content": "not json"}}]}
    assert check_llm.check(wrong) is False
    out = capsys.readouterr().out
    assert "[FAIL] expected one log_entries call" in out and "render fell back to English" in out
    assert "why  : no JSON object" in out and "model said: 'not json'" in out      # the next run shows the cause
    assert "the provider's whole reply" in out and '"content": "not json"' in out

    assert check_llm.check(FakeProvider(raises=LlmUnavailableError("401"))) is False


# ---------------------------------------------------------------- reasoning configuration
def test_sarvam_thinks_a_little_to_choose_and_not_at_all_to_reword(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    assert get_provider().reasoning == {"choose": "low", "complete": "off"}


def test_reasoning_can_be_overridden_for_both_calls(monkeypatch):
    monkeypatch.setenv("BRAIN", "sarvam")
    monkeypatch.setenv("BRAIN_REASONING_SARVAM", "medium")
    assert get_provider().reasoning == {"choose": "medium", "complete": "medium"}
    monkeypatch.setenv("BRAIN_REASONING_SARVAM", "omit")
    assert get_provider().reasoning == {"choose": "omit", "complete": "omit"}


def test_a_generic_provider_sends_no_reasoning_field_by_default(monkeypatch):
    second_provider(monkeypatch)
    assert build_provider("openai_compat").reasoning == {}


def test_the_interface_defaults_leave_room_to_think():
    import inspect
    from app.llm.base import LlmProvider
    assert inspect.signature(LlmProvider.choose_actions).parameters["max_tokens"].default == 4096
    assert inspect.signature(LlmProvider.complete).parameters["max_tokens"].default == 2048


def test_check_llm_does_not_pass_english_that_was_only_relabelled(capsys):
    """Regression: the live check passed a reply that was our English sentence verbatim, labelled hi-Latn, because
    it checked only the language label. A rider would have read English."""
    import importlib.util, json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("check_llm2", Path(__file__).parent.parent / "scripts" / "check_llm.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    english = "Please confirm: income ₹1,400 from Swiggy; expense ₹250 on fuel. Is that correct?"
    lazy = FakeProvider(turns=[[("log_entries", {"entries": [
        {"type": "income", "amount": 1400, "platform": "Swiggy"}, {"type": "expense", "amount": 250, "category": "fuel"}]})]],
        text=json.dumps({"language": "hi-Latn", "reply": english}))
    assert mod.check(lazy) is False
    assert "word for word in English" in capsys.readouterr().out
