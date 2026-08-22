"""Configuration loading, and the errors it gives when the YAML is wrong."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bot.config import ConfigError, load_config
from tests.conftest import ROOT

SETTINGS = "digest:\n  timezone: Asia/Kolkata\n"
TOPICS = textwrap.dedent(
    """
    scoring:
      strong_weight: 3.0
    sections:
      - id: life_insurance
        title: Life Insurance
        strong: [life insurer]
        supporting: [premium]
    """
)
SOURCES = textwrap.dedent(
    """
    sources:
      - id: one
        name: One
        url: https://one.test/rss
    """
)


def write_config(directory: Path, settings=SETTINGS, topics=TOPICS, sources=SOURCES) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "settings.yaml").write_text(settings, encoding="utf-8")
    (directory / "topics.yaml").write_text(topics, encoding="utf-8")
    (directory / "sources.yaml").write_text(sources, encoding="utf-8")
    return directory


# ------------------------------------------------------------- shipped config

def test_the_shipped_configuration_loads():
    config = load_config(ROOT / "config")
    assert config.enabled_sources
    assert config.section("life_insurance") is not None
    assert config.digest.timezone == "Asia/Kolkata"


def test_every_source_hint_names_a_real_section():
    config = load_config(ROOT / "config")
    known = {section.id for section in config.sections}
    assert all(s.section_hint in known for s in config.sources if s.section_hint)


def test_the_shipped_thresholds_are_self_consistent():
    """A single strong keyword in a headline must be able to clear the bar,
    or the digest would only ever carry keyword-dense stories."""
    config = load_config(ROOT / "config")
    best_single_hit = config.scoring.strong_weight * config.scoring.title_multiplier
    assert best_single_hit >= config.digest.min_score


# ------------------------------------------------------------------- defaults

def test_missing_optional_settings_fall_back_to_defaults(tmp_path):
    config = load_config(write_config(tmp_path / "c"))
    assert config.digest.lookback_hours == 26
    assert config.telegram.enabled and config.email.enabled
    assert config.state.path.name == "seen.json"


def test_a_relative_state_path_is_anchored_to_the_project(tmp_path):
    settings = SETTINGS + "state:\n  path: var/seen.json\n"
    config = load_config(write_config(tmp_path / "c", settings=settings))
    assert config.state.path.is_absolute()
    assert config.state.path.parts[-2:] == ("var", "seen.json")


# --------------------------------------------------------------------- errors

def test_a_missing_config_directory_is_reported_clearly(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nowhere")


@pytest.mark.parametrize(
    "sources,message",
    [
        ("sources: []", "non-empty list"),
        ("sources:\n  - name: No id\n    url: https://x.test/1", "missing required key 'id'"),
        ("sources:\n  - id: a\n    url: ftp://x.test/1", "must be http"),
        (
            "sources:\n  - id: a\n    url: https://x.test/1\n  - id: a\n    url: https://y.test/2",
            "duplicate source id",
        ),
        (
            "sources:\n  - id: a\n    url: https://x.test/1\n    enabled: false",
            "every source is disabled",
        ),
        (
            "sources:\n  - id: a\n    url: https://x.test/1\n    section_hint: nope",
            "unknown section",
        ),
    ],
)
def test_bad_sources_are_rejected_with_a_useful_message(tmp_path, sources, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path / "c", sources=sources))


def test_a_section_without_keywords_is_rejected(tmp_path):
    topics = "sections:\n  - id: empty\n    title: Empty\n"
    with pytest.raises(ConfigError, match="no keywords"):
        load_config(write_config(tmp_path / "c", topics=topics))


@pytest.mark.parametrize(
    "settings,message",
    [
        ("digest:\n  lookback_hours: many\n", "whole number"),
        ("digest:\n  lookback_hours: 0\n", "must be positive"),
        ("digest:\n  undated_items: maybe\n", "undated_items"),
        ("digest:\n  section_order: random\n", "section_order"),
        ("digest:\n  send_when_empty: yes please\n", "true or false"),
        ("digest:\n  min_score: high\n", "must be a number"),
    ],
)
def test_bad_settings_are_rejected_with_a_useful_message(tmp_path, settings, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path / "c", settings=settings))


def test_the_telegram_message_cap_is_clamped_below_the_api_limit(tmp_path):
    settings = SETTINGS + "delivery:\n  telegram:\n    max_message_chars: 999999\n"
    config = load_config(write_config(tmp_path / "c", settings=settings))
    assert config.telegram.max_message_chars <= 4000
