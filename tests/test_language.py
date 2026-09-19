"""Component 10. Pure logic, no network, no keys."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from voice_agent.flow import Script
from voice_agent.language import (
    LANGUAGE_ENV_VAR,
    Language,
    Normalizer,
    available,
    default_locale,
    load,
)


def test_loads_urdu_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LANGUAGE_ENV_VAR, raising=False)

    language = load()

    assert language.code == "ur"
    assert language.locale == "ur-PK"


def test_the_env_var_selects_the_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the component. Changing .env is enough to switch."""
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "ur-PK")

    assert load().code == "ur"


def test_bare_code_and_full_locale_both_work() -> None:
    assert load("ur").locale == load("ur-PK").locale


def test_carries_the_voice_and_stt_code() -> None:
    language = load("ur-PK")

    assert language.tts_voice == "en-IN-Neerja:DragonHDLatestNeural"
    assert language.stt_language == "ur"


def test_carries_the_keyterms() -> None:
    """stt.py needs these to bias recognition toward the replies we expect."""
    keyterms = load("ur-PK").keyterms

    assert "جی ہاں" in keyterms
    assert "theek hai" in keyterms


def test_carries_a_working_normalizer() -> None:
    normalizer = load("ur-PK").normalizer

    assert isinstance(normalizer, Normalizer)
    assert normalizer.from_speech("جی ہاں، ٹھیک ہے۔") == "جی ہاں ٹھیک ہے"


def test_carries_the_parsed_script() -> None:
    script = load("ur-PK").script

    assert isinstance(script, Script)
    assert script.first == "confirm_appointment"


def test_keyterms_are_copied_not_shared() -> None:
    """A caller mutating its list must not corrupt the module-level manifest
    for every later call in the process."""
    first = load("ur-PK")
    first.keyterms.append("polluted")

    assert "polluted" not in load("ur-PK").keyterms


def test_an_unknown_language_says_what_to_add() -> None:
    with pytest.raises(ValueError, match="no language pack"):
        load("fr-FR")


def test_language_is_immutable() -> None:
    """Nothing may swap the voice mid-call."""
    language = load("ur-PK")

    with pytest.raises(FrozenInstanceError):
        language.tts_voice = "en-US-JennyNeural"  # type: ignore[misc]


def test_repr_stays_readable() -> None:
    """The script is large. A default repr would flood every log line."""
    text = repr(load("ur-PK"))

    assert "ur-PK" in text
    assert len(text) < 200


def test_language_is_the_only_thing_components_need() -> None:
    """If this drifts, components will start importing lang/ur directly and
    adding English stops being a folder."""
    language = load("ur-PK")

    for field in ("stt_language", "tts_voice", "keyterms", "normalizer", "script"):
        assert getattr(language, field) is not None, f"{field} missing from Language"


def test_language_type_is_exported() -> None:
    assert isinstance(load("ur-PK"), Language)


def test_every_pack_on_disk_is_listed_with_its_name() -> None:
    """This is what the language toggle shows. A new folder is a new button."""
    assert list(available().items()) == [
        ("de-DE", "Deutsch"),
        ("en-US", "English"),
        ("ur-PK", "اردو"),
        ("sd-PK", "سنڌي"),
    ], "each pack's ORDER decides where it sits on the toggle"


def test_the_default_locale_follows_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "en-US")
    assert default_locale() == "en-US"
    monkeypatch.delenv(LANGUAGE_ENV_VAR)
    assert default_locale() == "ur-PK"


def test_each_language_knows_its_own_name() -> None:
    """The toggle label, and what a composed persona tells the model to speak."""
    assert load("ur-PK").name == "اردو"
    assert load("en-US").name == "English"


def test_each_language_has_a_time_up_line() -> None:
    """Said before the demo's limit cuts the call, so the line never just dies."""
    assert load("ur-PK").time_up.endswith("خدا حافظ۔")
    assert load("en-US").time_up.endswith("Goodbye.")
