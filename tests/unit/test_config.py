"""Budgets are the thing most likely to be quietly wrong, so they are pinned here.

The numbers are the revised, hardware-aware ones from plan section 14.4. If a change to
this test is ever proposed, the plan is what has to change first.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evergrove_agent.config import Settings


def test_default_budgets_match_the_plan(settings: Settings) -> None:
    # Raised from the plan's 2 on request (Day 3). 3 is also the ceiling
    # FocusPreparationReport.hops_used allows, so this is as high as it goes for free.
    assert settings.max_hops == 3
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


def test_each_role_selects_its_provider_independently() -> None:
    """One role moving to hosted must move that role and no other (Day 5 T3).

    `provider_for` is what `build_provider` routes on, so a shared or transposed field here
    would point a role at a model nobody configured for it — and the run would still succeed,
    which is what makes it worth pinning. `roles_using_hosted` is asserted alongside because
    `--fully-local` is the control that has to see the same answer.
    """
    settings = Settings(_env_file=None, appraiser_provider="hosted")

    assert settings.provider_for("supervisor") == "local"
    assert settings.provider_for("researcher") == "local"
    assert settings.provider_for("appraiser") == "hosted"
    assert settings.roles_using_hosted == ("appraiser",)


def test_an_unknown_provider_value_is_refused_rather_than_defaulted() -> None:
    """A typo in `*_PROVIDER` must fail loudly, not quietly run local.

    This is the normal configuration validation the project relies on: `ProviderName` is a
    `Literal`, so the run stops before a model is ever built. Without it a misspelled
    `APPRAISER_PROVIDER=hostd` would reach `build_provider`, which has to answer with
    *something* — and an operator who believed a role was hosted would get a local run that
    looks exactly like a correct one.
    """
    with pytest.raises(ValidationError, match="appraiser_provider"):
        Settings(_env_file=None, appraiser_provider="hostd")


def test_fully_local_accepts_an_all_local_configuration(settings: Settings) -> None:
    settings.force_fully_local()  # must not raise


def test_fully_local_refuses_when_a_role_is_hosted() -> None:
    settings = Settings(_env_file=None, researcher_provider="hosted")

    with pytest.raises(ValueError, match="researcher"):
        settings.force_fully_local()


def test_paths_are_absolute(settings: Settings) -> None:
    assert settings.db_path.is_absolute()
    assert settings.allowed_attachment_dir.is_absolute()
