"""Budgets are the thing most likely to be quietly wrong, so they are pinned here.

The numbers are the revised, hardware-aware ones from plan section 14.4. If a change to
this test is ever proposed, the plan is what has to change first.
"""

from __future__ import annotations

import pytest

from evergrove_agent.config import Settings


def test_default_budgets_match_the_plan(settings: Settings) -> None:
    assert settings.max_hops == 2
    assert settings.max_search_calls == 3
    assert settings.max_fetch_calls == 4
    assert settings.max_sources_kept == 3
    assert settings.max_model_calls == 10
    assert settings.source_excerpt_chars == 3000
    assert settings.num_ctx == 4096
    assert settings.total_run_timeout_s == 900
    assert settings.monthly_search_budget == 200


def test_defaults_keep_the_run_free_and_offline(settings: Settings) -> None:
    """`local` + `fixture` is what makes the committed default cost nothing."""
    assert settings.local_model == "qwen3:4b"
    assert settings.search_backend == "fixture"
    assert settings.roles_using_hosted == ()
    assert settings.temperature == 0.0
    assert settings.local_keep_alive == "60m"


def test_environment_overrides_a_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_SEARCH_CALLS", "1")

    assert Settings(_env_file=None).max_search_calls == 1


def test_fully_local_accepts_an_all_local_configuration(settings: Settings) -> None:
    settings.force_fully_local()  # must not raise


def test_fully_local_refuses_when_a_role_is_hosted() -> None:
    settings = Settings(_env_file=None, researcher_provider="hosted")

    with pytest.raises(ValueError, match="researcher"):
        settings.force_fully_local()


def test_paths_are_absolute(settings: Settings) -> None:
    assert settings.db_path.is_absolute()
    assert settings.allowed_attachment_dir.is_absolute()
