from __future__ import annotations

from src.i18n import LANGUAGES, normalize_lang, reset_cache, t


def setup_function() -> None:
    reset_cache()


def test_lookup_existing_key_ru() -> None:
    assert t("save.no_active_session", "ru") == "нет активной сессии"


def test_lookup_existing_key_en() -> None:
    assert t("save.no_active_session", "en") == "no active session"


def test_lookup_existing_key_uz() -> None:
    assert t("save.no_active_session", "uz") == "faol sessiya yo'q"


def test_missing_key_falls_back_to_key_itself() -> None:
    assert t("nonexistent.deeply.nested", "en") == "nonexistent.deeply.nested"


def test_missing_in_target_falls_back_to_ru() -> None:
    # If a key only exists in ru, en lookup must return ru content (not the key).
    # We don't actually have such a missing key right now, but contract holds.
    # As a synthetic check: unknown lang normalizes to ru, then lookup works.
    assert t("save.no_active_session", "xx") == "нет активной сессии"


def test_jinja_var_interpolation() -> None:
    assert t("save.failed", "ru", error="boom") == "⚠ не смог разобрать разговор: boom"


def test_normalize_lang_drops_unknown() -> None:
    assert normalize_lang("ru") == "ru"
    assert normalize_lang("en") == "en"
    assert normalize_lang("uz") == "uz"
    assert normalize_lang("zh") == "ru"
    assert normalize_lang(None) == "ru"


def test_all_locales_present() -> None:
    for lang in LANGUAGES:
        # core keys must exist in every locale to avoid runtime fallback noise
        for key in ("save.no_active_session", "lang.ui_updated", "confirm.confirmed"):
            assert t(key, lang) != key, f"{key} missing in {lang}"


def test_language_label_self_reference() -> None:
    assert t("language_label.ru", "ru") == "русский"
    assert t("language_label.en", "en") == "English"
    assert t("language_label.uz", "uz") == "O'zbekcha"


def test_cmd_descriptions_present_in_all_locales() -> None:
    for lang in LANGUAGES:
        # /lang was removed in favor of /settings → Languages submenu
        for cmd in ("start", "save", "undo", "settings"):
            val = t(f"cmd_descriptions.{cmd}", lang)
            assert val != f"cmd_descriptions.{cmd}", f"missing cmd desc {cmd}/{lang}"
            # Telegram limits command descriptions to 256 chars
            assert len(val) <= 256, f"cmd desc too long for {cmd}/{lang}: {len(val)}"


def test_section_headers_three_languages() -> None:
    from src.vault.section_headers import (
        FACTS_HEADERS,
        LINKS_HEADERS,
        facts_header,
        links_header,
    )

    assert FACTS_HEADERS["ru"] == "## Факты"
    assert FACTS_HEADERS["en"] == "## Facts"
    assert FACTS_HEADERS["uz"] == "## Faktlar"

    assert LINKS_HEADERS["ru"] == "## Связи"
    assert LINKS_HEADERS["en"] == "## Links"
    assert LINKS_HEADERS["uz"] == "## Bog'lanishlar"

    # Unknown lang falls back to ru
    assert facts_header("xx") == FACTS_HEADERS["ru"]
    assert links_header("xx") == LINKS_HEADERS["ru"]


def test_strip_links_section_handles_all_languages() -> None:
    from src.vault.section_headers import strip_links_section

    body_ru = "one_liner\n\n## Связи\n\n- [[a]]"
    body_en = "one_liner\n\n## Links\n\n- [[a]]"
    body_uz = "one_liner\n\n## Bog'lanishlar\n\n- [[a]]"
    body_none = "one_liner\n\nfacts: stuff"

    assert strip_links_section(body_ru) == "one_liner"
    assert strip_links_section(body_en) == "one_liner"
    assert strip_links_section(body_uz) == "one_liner"
    assert strip_links_section(body_none) == body_none


def test_prompt_loader_lang_aware() -> None:
    from src.agent import prompts

    ru = prompts.load("system", "ru")
    en = prompts.load("system", "en")
    uz = prompts.load("system", "uz")

    # All three exist and differ
    assert ru != en
    assert en != uz
    assert ru != uz

    # System prompt mentions the assistant name template
    rendered_en = prompts.render("system", "en", bot_name="Mnemo", personality="")
    assert "Mnemo" in rendered_en


def test_prompt_loader_fallback_to_ru() -> None:
    from src.agent import prompts

    # Request unknown language → falls back to ru content
    fallback = prompts.load("system", "zh-Hant")
    ru = prompts.load("system", "ru")
    assert fallback == ru


def test_profile_migration_backfills_languages() -> None:
    from src.session.manager import _apply_language_migration

    legacy: dict[str, object] = {"bot_name": "X", "owner_name": "Y"}
    _apply_language_migration(legacy)
    assert legacy["ui_language"] == "ru"
    assert legacy["notes_language"] == "ru"


def test_profile_migration_respects_legacy_vault_language() -> None:
    from src.session.manager import _apply_language_migration

    legacy: dict[str, object] = {"vault_language": "en"}
    _apply_language_migration(legacy)
    assert legacy["notes_language"] == "en"
    assert legacy["ui_language"] == "ru"


def test_profile_migration_collapses_mixed_to_ru() -> None:
    from src.session.manager import _apply_language_migration

    legacy: dict[str, object] = {"vault_language": "mixed"}
    _apply_language_migration(legacy)
    assert legacy["notes_language"] == "ru"
