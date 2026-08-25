"""The configuration loader, and the shipped configuration itself."""

from __future__ import annotations

import pytest
import yaml

from radar.config import ConfigError, load_config


def _write(directory, settings=None, sources=None, profile=None, base=None):
    """Write a config directory, starting from the shipped one."""
    for name, override in (
        ("settings.yaml", settings), ("sources.yaml", sources), ("profile.yaml", profile),
    ):
        data = yaml.safe_load((base / name).read_text())
        if override:
            override(data)
        (directory / name).write_text(yaml.safe_dump(data), encoding="utf-8")
    return directory


def test_the_shipped_config_loads(config):
    assert config.enabled_sources
    assert config.profile.queries
    assert config.saved_searches


def test_every_shipped_source_names_a_known_kind(config):
    from radar.config import KNOWN_KINDS

    assert all(source.kind in KNOWN_KINDS for source in config.sources)


def test_keyed_sources_ship_switched_off(config):
    """They cannot work without a key, and a source that fails every morning
    trains its reader to ignore the footer."""
    for source in config.sources:
        if source.kind in {"careerjet", "jooble", "adzuna"}:
            assert not source.enabled, f"{source.id} needs a key but ships enabled"


def test_an_unknown_kind_is_rejected(tmp_path, config):
    def break_kind(data):
        data["sources"][0]["kind"] = "monster"

    directory = _write(tmp_path, sources=break_kind, base=config.config_dir)
    with pytest.raises(ConfigError, match="unknown kind"):
        load_config(directory)


def test_duplicate_source_ids_are_rejected(tmp_path, config):
    def duplicate(data):
        data["sources"].append(dict(data["sources"][0]))

    directory = _write(tmp_path, sources=duplicate, base=config.config_dir)
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_config(directory)


def test_all_sources_disabled_is_rejected(tmp_path, config):
    def disable(data):
        for source in data["sources"]:
            source["enabled"] = False

    directory = _write(tmp_path, sources=disable, base=config.config_dir)
    with pytest.raises(ConfigError, match="every source is disabled"):
        load_config(directory)


def test_out_of_order_tiers_are_rejected(tmp_path, config):
    def invert(data):
        data["scoring"]["tiers"] = {"strong": 40, "possible": 55, "stretch": 70}

    directory = _write(tmp_path, profile=invert, base=config.config_dir)
    with pytest.raises(ConfigError, match="descend"):
        load_config(directory)


def test_an_empty_function_gate_is_rejected(tmp_path, config):
    """An empty gate would silently admit every senior role on earth."""
    def empty(data):
        data["dimensions"]["function"]["strong"] = []

    directory = _write(tmp_path, profile=empty, base=config.config_dir)
    with pytest.raises(ConfigError, match="function gate"):
        load_config(directory)


def test_a_bad_proof_of_life_day_is_rejected(tmp_path, config):
    def typo(data):
        data["radar"]["proof_of_life_weekday"] = "moonday"

    directory = _write(tmp_path, settings=typo, base=config.config_dir)
    with pytest.raises(ConfigError, match="weekday"):
        load_config(directory)
