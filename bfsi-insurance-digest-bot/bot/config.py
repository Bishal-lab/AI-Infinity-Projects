"""Loading and validating the YAML configuration.

The whole bot is driven by three files in ``config/`` — settings, sources and
topics. They are read once at start-up into frozen dataclasses so that nothing
downstream has to reason about missing keys or stringly-typed numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(ValueError):
    """Raised when the YAML is missing something the bot cannot invent."""


# --------------------------------------------------------------------------- #
# Small typed helpers. They exist so every error message names the file, the
# key and what was expected, rather than surfacing a bare KeyError at 08:00.
# --------------------------------------------------------------------------- #

def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _as_float(value: Any, key: str, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: '{key}' must be a number, got {value!r}") from None


def _as_int(value: Any, key: str, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: '{key}' must be a whole number, got {value!r}") from None


def _as_bool(value: Any, key: str, where: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{where}: '{key}' must be true or false, got {value!r}")


def _as_str_map(value: Any, key: str, where: str) -> dict[str, str]:
    """A mapping of plain strings; keys are lowercased for case-free lookup."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: '{key}' must be a mapping of name to name")
    out: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ConfigError(f"{where}: '{key}' entries must all be strings")
        out[raw_key.strip().lower()] = raw_value.strip()
    return out


def _as_str_list(value: Any, key: str, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of strings")
    return tuple(v.strip() for v in value if v and v.strip())


# --------------------------------------------------------------------------- #
# Configuration objects
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    weight: float = 0.0
    section_hint: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    emoji: str
    multiplier: float
    strong: tuple[str, ...]
    supporting: tuple[str, ...]
    #: Whether this section's strong keywords are, on their own, proof that a
    #: story belongs to the BFSI world. True for sections whose vocabulary names
    #: the industry ("HDFC Life", "IRDAI"); false for sections whose vocabulary
    #: names a kind of event that happens in every industry ("IPO", "appoints").
    certifies_domain: bool = True


@dataclass(frozen=True)
class Scoring:
    strong_weight: float = 3.0
    supporting_weight: float = 1.0
    title_multiplier: float = 1.5
    hint_bonus: float = 2.0
    gate_bypass_weight: float = 2.0
    max_keywords_counted: int = 8


@dataclass(frozen=True)
class DigestSettings:
    timezone: str = "Asia/Kolkata"
    lookback_hours: int = 26
    undated_items: str = "window"
    max_items_per_source: int = 4
    max_items_per_section: int = 6
    max_items_total: int = 24
    min_score: float = 3.0
    send_when_empty: bool = True
    section_order: str = "fixed"
    #: hostname -> masthead, for feeds that report an outlet by domain.
    publisher_aliases: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchSettings:
    timeout_seconds: float = 20.0
    max_workers: int = 8
    retries: int = 2
    backoff_seconds: float = 2.0
    user_agent: str = "bfsi-insurance-digest-bot/1.0"
    degraded_failure_ratio: float = 0.34


@dataclass(frozen=True)
class StateSettings:
    path: Path = PROJECT_ROOT / "state" / "seen.json"
    retention_days: int = 14


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = True
    disable_web_page_preview: bool = True
    max_message_chars: int = 3500
    include_summaries: bool = True
    summary_chars: int = 170


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool = True
    subject_template: str = "BFSI & Life Insurance Daily — {date}"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    use_ssl: bool = True
    summary_chars: int = 300


@dataclass(frozen=True)
class Config:
    digest: DigestSettings
    fetch: FetchSettings
    state: StateSettings
    telegram: TelegramSettings
    email: EmailSettings
    scoring: Scoring
    sections: tuple[Section, ...]
    sources: tuple[Source, ...]
    domain_gate: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    config_dir: Path = DEFAULT_CONFIG_DIR

    @property
    def enabled_sources(self) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if s.enabled)

    def section(self, section_id: str) -> Section | None:
        for section in self.sections:
            if section.id == section_id:
                return section
        return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a mapping at the top level")
    return data


def _parse_sources(raw: Mapping[str, Any], where: str) -> tuple[Source, ...]:
    entries = _require(raw, "sources", where)
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{where}: 'sources' must be a non-empty list")

    sources: list[Source] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"{where}: sources[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be a mapping")
        source_id = str(_require(entry, "id", label)).strip()
        if not source_id:
            raise ConfigError(f"{label}: 'id' must not be blank")
        if source_id in seen_ids:
            raise ConfigError(f"{where}: duplicate source id '{source_id}'")
        seen_ids.add(source_id)

        url = str(_require(entry, "url", label)).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError(f"{label}: 'url' must be http(s), got {url!r}")

        hint = entry.get("section_hint")
        sources.append(
            Source(
                id=source_id,
                name=str(entry.get("name", source_id)).strip() or source_id,
                url=url,
                weight=_as_float(entry.get("weight", 0.0), "weight", label),
                section_hint=str(hint).strip() if hint else None,
                enabled=_as_bool(entry.get("enabled", True), "enabled", label),
            )
        )
    return tuple(sources)


def _parse_sections(raw: Mapping[str, Any], where: str) -> tuple[Section, ...]:
    entries = _require(raw, "sections", where)
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{where}: 'sections' must be a non-empty list")

    sections: list[Section] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"{where}: sections[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be a mapping")
        section_id = str(_require(entry, "id", label)).strip()
        if section_id in seen_ids:
            raise ConfigError(f"{where}: duplicate section id '{section_id}'")
        seen_ids.add(section_id)

        strong = _as_str_list(entry.get("strong"), "strong", label)
        supporting = _as_str_list(entry.get("supporting"), "supporting", label)
        if not strong and not supporting:
            raise ConfigError(f"{label}: section '{section_id}' has no keywords")

        sections.append(
            Section(
                id=section_id,
                title=str(entry.get("title", section_id)).strip() or section_id,
                emoji=str(entry.get("emoji", "")).strip(),
                multiplier=_as_float(entry.get("multiplier", 1.0), "multiplier", label),
                strong=strong,
                supporting=supporting,
                certifies_domain=_as_bool(
                    entry.get("certifies_domain", True), "certifies_domain", label
                ),
            )
        )
    return tuple(sections)


def load_config(config_dir: str | os.PathLike[str] | None = None) -> Config:
    """Read settings.yaml, sources.yaml and topics.yaml into a `Config`."""
    directory = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    directory = directory.expanduser().resolve()

    settings_raw = _load_yaml(directory / "settings.yaml")
    sources_raw = _load_yaml(directory / "sources.yaml")
    topics_raw = _load_yaml(directory / "topics.yaml")

    digest_raw = settings_raw.get("digest", {}) or {}
    fetch_raw = settings_raw.get("fetch", {}) or {}
    state_raw = settings_raw.get("state", {}) or {}
    delivery_raw = settings_raw.get("delivery", {}) or {}
    telegram_raw = delivery_raw.get("telegram", {}) or {}
    email_raw = delivery_raw.get("email", {}) or {}
    scoring_raw = topics_raw.get("scoring", {}) or {}

    where = "settings.yaml"
    undated = str(digest_raw.get("undated_items", "window")).strip().lower()
    if undated not in {"window", "drop"}:
        raise ConfigError(f"{where}: digest.undated_items must be 'window' or 'drop'")
    section_order = str(digest_raw.get("section_order", "fixed")).strip().lower()
    if section_order not in {"fixed", "volume"}:
        raise ConfigError(f"{where}: digest.section_order must be 'fixed' or 'volume'")

    digest = DigestSettings(
        timezone=str(digest_raw.get("timezone", "Asia/Kolkata")),
        lookback_hours=_as_int(digest_raw.get("lookback_hours", 26), "lookback_hours", where),
        undated_items=undated,
        max_items_per_source=_as_int(
            digest_raw.get("max_items_per_source", 4), "max_items_per_source", where
        ),
        max_items_per_section=_as_int(
            digest_raw.get("max_items_per_section", 6), "max_items_per_section", where
        ),
        max_items_total=_as_int(digest_raw.get("max_items_total", 24), "max_items_total", where),
        min_score=_as_float(digest_raw.get("min_score", 3.0), "min_score", where),
        send_when_empty=_as_bool(digest_raw.get("send_when_empty", True), "send_when_empty", where),
        section_order=section_order,
        publisher_aliases=_as_str_map(
            digest_raw.get("publisher_aliases"), "publisher_aliases", where
        ),
    )
    if digest.lookback_hours <= 0:
        raise ConfigError(f"{where}: digest.lookback_hours must be positive")

    fetch = FetchSettings(
        timeout_seconds=_as_float(fetch_raw.get("timeout_seconds", 20), "timeout_seconds", where),
        max_workers=max(1, _as_int(fetch_raw.get("max_workers", 8), "max_workers", where)),
        retries=max(0, _as_int(fetch_raw.get("retries", 2), "retries", where)),
        backoff_seconds=_as_float(fetch_raw.get("backoff_seconds", 2.0), "backoff_seconds", where),
        user_agent=str(fetch_raw.get("user_agent", "bfsi-insurance-digest-bot/1.0")),
        degraded_failure_ratio=_as_float(
            fetch_raw.get("degraded_failure_ratio", 0.34), "degraded_failure_ratio", where
        ),
    )

    state_path = Path(str(state_raw.get("path", "state/seen.json"))).expanduser()
    if not state_path.is_absolute():
        state_path = PROJECT_ROOT / state_path
    state = StateSettings(
        path=state_path,
        retention_days=_as_int(state_raw.get("retention_days", 14), "retention_days", where),
    )

    telegram = TelegramSettings(
        enabled=_as_bool(telegram_raw.get("enabled", True), "enabled", where),
        disable_web_page_preview=_as_bool(
            telegram_raw.get("disable_web_page_preview", True), "disable_web_page_preview", where
        ),
        max_message_chars=min(
            4000,
            max(500, _as_int(telegram_raw.get("max_message_chars", 3500), "max_message_chars", where)),
        ),
        include_summaries=_as_bool(
            telegram_raw.get("include_summaries", True), "include_summaries", where
        ),
        summary_chars=_as_int(telegram_raw.get("summary_chars", 170), "summary_chars", where),
    )

    email = EmailSettings(
        enabled=_as_bool(email_raw.get("enabled", True), "enabled", where),
        subject_template=str(
            email_raw.get("subject_template", "BFSI & Life Insurance Daily — {date}")
        ),
        smtp_host=str(email_raw.get("smtp_host", "smtp.gmail.com")),
        smtp_port=_as_int(email_raw.get("smtp_port", 465), "smtp_port", where),
        use_ssl=_as_bool(email_raw.get("use_ssl", True), "use_ssl", where),
        summary_chars=_as_int(email_raw.get("summary_chars", 300), "summary_chars", where),
    )

    scoring = Scoring(
        strong_weight=_as_float(scoring_raw.get("strong_weight", 3.0), "strong_weight", "topics.yaml"),
        supporting_weight=_as_float(
            scoring_raw.get("supporting_weight", 1.0), "supporting_weight", "topics.yaml"
        ),
        title_multiplier=_as_float(
            scoring_raw.get("title_multiplier", 1.5), "title_multiplier", "topics.yaml"
        ),
        hint_bonus=_as_float(scoring_raw.get("hint_bonus", 2.0), "hint_bonus", "topics.yaml"),
        gate_bypass_weight=_as_float(
            scoring_raw.get("gate_bypass_weight", 2.0), "gate_bypass_weight", "topics.yaml"
        ),
        max_keywords_counted=_as_int(
            scoring_raw.get("max_keywords_counted", 8), "max_keywords_counted", "topics.yaml"
        ),
    )

    sections = _parse_sections(topics_raw, "topics.yaml")
    sources = _parse_sources(sources_raw, "sources.yaml")

    known_sections = {section.id for section in sections}
    for source in sources:
        if source.section_hint and source.section_hint not in known_sections:
            raise ConfigError(
                f"sources.yaml: source '{source.id}' hints at unknown section "
                f"'{source.section_hint}' (known: {', '.join(sorted(known_sections))})"
            )
    if not any(source.enabled for source in sources):
        raise ConfigError("sources.yaml: every source is disabled, nothing to fetch")

    return Config(
        digest=digest,
        fetch=fetch,
        state=state,
        telegram=telegram,
        email=email,
        scoring=scoring,
        sections=sections,
        sources=sources,
        domain_gate=_as_str_list(topics_raw.get("domain_gate"), "domain_gate", "topics.yaml"),
        exclude=_as_str_list(topics_raw.get("exclude"), "exclude", "topics.yaml"),
        config_dir=directory,
    )
