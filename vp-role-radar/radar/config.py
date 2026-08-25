"""Loading and validating the YAML configuration.

The radar is driven by three files in ``config/`` — settings, sources and
profile. They are read once at start-up into frozen dataclasses so that nothing
downstream has to reason about missing keys or stringly-typed numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"

#: Every adapter name `sources.yaml` may use. Checked at load time so a typo is
#: a clear error at start-up rather than a silently skipped source at 08:00.
KNOWN_KINDS = frozenset(
    {"workday", "greenhouse", "lever", "rss", "careerjet", "jooble", "adzuna"}
)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class ConfigError(ValueError):
    """Raised when the YAML is missing something the radar cannot invent."""


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
    kind: str
    url: str
    company: str = ""
    query: str = ""
    location: str = ""
    locale: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class SavedSearch:
    label: str
    url: str
    #: True for links seen returning the right kind of listing while this was
    #: built. Shown in the docs, not in the digest — a reader does not care.
    verified: bool = False


@dataclass(frozen=True)
class SeniorityLevel:
    id: str
    title: str
    value: float
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Region:
    id: str
    title: str
    value: float
    countries: tuple[str, ...]
    cities: tuple[str, ...]
    preferred_cities: tuple[str, ...]

    @property
    def all_places(self) -> tuple[str, ...]:
        return self.countries + self.preferred_cities + self.cities


@dataclass(frozen=True)
class Profile:
    """profile.yaml: who the radar is looking for."""

    candidate: Mapping[str, Any]
    title_multiplier: float
    max_keywords_counted: int
    tier_thresholds: Mapping[str, float]
    experience_floor: int
    experience_ideal_min: int
    experience_ideal_max: int
    exclude_title: tuple[str, ...]
    exclude_anywhere: tuple[str, ...]
    seniority_points: float
    seniority_levels: tuple[SeniorityLevel, ...]
    function_points: float
    function_strong: tuple[str, ...]
    function_supporting: tuple[str, ...]
    domain_points: float
    domain_strong: tuple[str, ...]
    domain_adjacent: tuple[str, ...]
    domain_adjacent_value: float
    geography_points: float
    regions: tuple[Region, ...]
    edge_points: float
    edge_keywords: tuple[str, ...]
    queries: tuple[str, ...]

    def region(self, region_id: str) -> Region | None:
        for region in self.regions:
            if region.id == region_id:
                return region
        return None


@dataclass(frozen=True)
class RadarSettings:
    timezone: str = "Asia/Kolkata"
    lookback_days: int = 7
    undated_items: str = "window"
    max_items_per_source: int = 8
    max_items_per_tier: int = 10
    max_items_total: int = 30
    min_fit: float = 45.0
    send_when_empty: bool = False
    #: Weekday name, lowercase, on which an empty digest is sent anyway as
    #: proof of life. Empty string switches that off.
    proof_of_life_weekday: str = "monday"


@dataclass(frozen=True)
class FetchSettings:
    timeout_seconds: float = 25.0
    max_workers: int = 6
    retries: int = 2
    backoff_seconds: float = 2.0
    user_agent: str = "vp-role-radar/1.0"
    degraded_failure_ratio: float = 0.5


@dataclass(frozen=True)
class StateSettings:
    path: Path = PROJECT_ROOT / "state" / "seen.json"
    retention_days: int = 45


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool = True
    subject_template: str = "VP Role Radar — {count} {noun} ({date})"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    use_ssl: bool = True
    summary_chars: int = 260


@dataclass(frozen=True)
class DigestFileSettings:
    enabled: bool = True
    path: Path = PROJECT_ROOT / "state" / "latest-digest.md"


@dataclass(frozen=True)
class Config:
    radar: RadarSettings
    fetch: FetchSettings
    state: StateSettings
    email: EmailSettings
    digest_file: DigestFileSettings
    profile: Profile
    sources: tuple[Source, ...]
    saved_searches: tuple[SavedSearch, ...] = ()
    config_dir: Path = DEFAULT_CONFIG_DIR

    @property
    def enabled_sources(self) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if s.enabled)


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

        kind = str(_require(entry, "kind", label)).strip().lower()
        if kind not in KNOWN_KINDS:
            raise ConfigError(
                f"{label}: unknown kind '{kind}' "
                f"(known: {', '.join(sorted(KNOWN_KINDS))})"
            )

        url = str(_require(entry, "url", label)).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError(f"{label}: 'url' must be http(s), got {url!r}")

        sources.append(
            Source(
                id=source_id,
                name=str(entry.get("name", source_id)).strip() or source_id,
                kind=kind,
                url=url,
                company=str(entry.get("company", "")).strip(),
                query=str(entry.get("query", "")).strip(),
                location=str(entry.get("location", "")).strip(),
                locale=str(entry.get("locale", "")).strip(),
                enabled=_as_bool(entry.get("enabled", True), "enabled", label),
            )
        )
    return tuple(sources)


def _parse_regions(raw: Any, where: str) -> tuple[Region, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{where}: geography.regions must be a non-empty list")
    regions: list[Region] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        label = f"{where}: geography.regions[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be a mapping")
        region_id = str(_require(entry, "id", label)).strip()
        if region_id in seen:
            raise ConfigError(f"{where}: duplicate region id '{region_id}'")
        seen.add(region_id)
        countries = _as_str_list(entry.get("countries"), "countries", label)
        cities = _as_str_list(entry.get("cities"), "cities", label)
        preferred = _as_str_list(entry.get("preferred_cities"), "preferred_cities", label)
        if not (countries or cities or preferred):
            raise ConfigError(f"{label}: region '{region_id}' names no places")
        regions.append(
            Region(
                id=region_id,
                title=str(entry.get("title", region_id)).strip() or region_id,
                value=_as_float(entry.get("value", 1.0), "value", label),
                countries=countries,
                cities=cities,
                preferred_cities=preferred,
            )
        )
    return tuple(regions)


def _parse_profile(raw: Mapping[str, Any], where: str) -> Profile:
    dimensions = _require(raw, "dimensions", where)
    if not isinstance(dimensions, dict):
        raise ConfigError(f"{where}: 'dimensions' must be a mapping")

    def dimension(name: str) -> Mapping[str, Any]:
        value = _require(dimensions, name, f"{where}: dimensions")
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: dimensions.{name} must be a mapping")
        return value

    seniority = dimension("seniority")
    levels_raw = _require(seniority, "levels", f"{where}: dimensions.seniority")
    if not isinstance(levels_raw, list) or not levels_raw:
        raise ConfigError(f"{where}: dimensions.seniority.levels must be a non-empty list")
    levels: list[SeniorityLevel] = []
    for index, entry in enumerate(levels_raw):
        label = f"{where}: dimensions.seniority.levels[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be a mapping")
        keywords = _as_str_list(entry.get("keywords"), "keywords", label)
        if not keywords:
            raise ConfigError(f"{label}: a seniority level with no keywords matches nothing")
        levels.append(
            SeniorityLevel(
                id=str(_require(entry, "id", label)).strip(),
                title=str(entry.get("title", entry.get("id"))).strip(),
                value=_as_float(entry.get("value", 1.0), "value", label),
                keywords=keywords,
            )
        )

    function = dimension("function")
    domain = dimension("domain")
    geography = dimension("geography")
    edge = dimension("edge")

    function_strong = _as_str_list(function.get("strong"), "strong", where)
    if not function_strong:
        raise ConfigError(
            f"{where}: dimensions.function.strong is empty — it is the function "
            f"gate, so an empty list would admit every senior role anywhere"
        )

    scoring = raw.get("scoring", {}) or {}
    tiers_raw = scoring.get("tiers", {}) or {}
    tiers: dict[str, float] = {}
    for name in ("strong", "possible", "stretch"):
        tiers[name] = _as_float(
            tiers_raw.get(name, {"strong": 70, "possible": 55, "stretch": 45}[name]),
            f"tiers.{name}",
            where,
        )
    if not tiers["strong"] > tiers["possible"] > tiers["stretch"]:
        raise ConfigError(
            f"{where}: scoring.tiers must descend strong > possible > stretch, got {tiers}"
        )

    experience = raw.get("experience", {}) or {}
    exclude = raw.get("exclude", {}) or {}
    if not isinstance(exclude, dict):
        raise ConfigError(f"{where}: 'exclude' must be a mapping of title/anywhere lists")

    return Profile(
        candidate=raw.get("candidate", {}) or {},
        title_multiplier=_as_float(scoring.get("title_multiplier", 2.0), "title_multiplier", where),
        max_keywords_counted=_as_int(
            scoring.get("max_keywords_counted", 10), "max_keywords_counted", where
        ),
        tier_thresholds=tiers,
        experience_floor=_as_int(experience.get("floor_years", 10), "floor_years", where),
        experience_ideal_min=_as_int(experience.get("ideal_min", 15), "ideal_min", where),
        experience_ideal_max=_as_int(experience.get("ideal_max", 25), "ideal_max", where),
        exclude_title=_as_str_list(exclude.get("title"), "exclude.title", where),
        exclude_anywhere=_as_str_list(exclude.get("anywhere"), "exclude.anywhere", where),
        seniority_points=_as_float(seniority.get("points", 30), "seniority.points", where),
        seniority_levels=tuple(levels),
        function_points=_as_float(function.get("points", 30), "function.points", where),
        function_strong=function_strong,
        function_supporting=_as_str_list(function.get("supporting"), "supporting", where),
        domain_points=_as_float(domain.get("points", 20), "domain.points", where),
        domain_strong=_as_str_list(domain.get("strong"), "strong", where),
        domain_adjacent=_as_str_list(domain.get("adjacent"), "adjacent", where),
        domain_adjacent_value=_as_float(
            domain.get("adjacent_value", 0.55), "adjacent_value", where
        ),
        geography_points=_as_float(geography.get("points", 10), "geography.points", where),
        regions=_parse_regions(geography.get("regions"), where),
        edge_points=_as_float(edge.get("points", 10), "edge.points", where),
        edge_keywords=_as_str_list(edge.get("keywords"), "keywords", where),
        queries=_as_str_list(raw.get("queries"), "queries", where),
    )


def _parse_saved_searches(raw: Any, where: str) -> tuple[SavedSearch, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: 'saved_searches' must be a list")
    searches: list[SavedSearch] = []
    for index, entry in enumerate(raw):
        label = f"{where}: saved_searches[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be a mapping")
        url = str(_require(entry, "url", label)).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError(f"{label}: 'url' must be http(s), got {url!r}")
        searches.append(
            SavedSearch(
                label=str(_require(entry, "label", label)).strip(),
                url=url,
                verified=_as_bool(entry.get("verified", False), "verified", label),
            )
        )
    return tuple(searches)


def load_config(config_dir: str | os.PathLike[str] | None = None) -> Config:
    """Read settings.yaml, sources.yaml and profile.yaml into a `Config`."""
    directory = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    directory = directory.expanduser().resolve()

    settings_raw = _load_yaml(directory / "settings.yaml")
    sources_raw = _load_yaml(directory / "sources.yaml")
    profile_raw = _load_yaml(directory / "profile.yaml")

    radar_raw = settings_raw.get("radar", {}) or {}
    fetch_raw = settings_raw.get("fetch", {}) or {}
    state_raw = settings_raw.get("state", {}) or {}
    delivery_raw = settings_raw.get("delivery", {}) or {}
    email_raw = delivery_raw.get("email", {}) or {}
    digest_file_raw = delivery_raw.get("digest_file", {}) or {}

    where = "settings.yaml"
    undated = str(radar_raw.get("undated_items", "window")).strip().lower()
    if undated not in {"window", "drop"}:
        raise ConfigError(f"{where}: radar.undated_items must be 'window' or 'drop'")

    proof_day = str(radar_raw.get("proof_of_life_weekday", "monday")).strip().lower()
    if proof_day and proof_day not in _WEEKDAYS:
        raise ConfigError(
            f"{where}: radar.proof_of_life_weekday must be a weekday name or empty, "
            f"got {proof_day!r}"
        )

    radar = RadarSettings(
        timezone=str(radar_raw.get("timezone", "Asia/Kolkata")),
        lookback_days=_as_int(radar_raw.get("lookback_days", 7), "lookback_days", where),
        undated_items=undated,
        max_items_per_source=_as_int(
            radar_raw.get("max_items_per_source", 8), "max_items_per_source", where
        ),
        max_items_per_tier=_as_int(
            radar_raw.get("max_items_per_tier", 10), "max_items_per_tier", where
        ),
        max_items_total=_as_int(radar_raw.get("max_items_total", 30), "max_items_total", where),
        min_fit=_as_float(radar_raw.get("min_fit", 45), "min_fit", where),
        send_when_empty=_as_bool(radar_raw.get("send_when_empty", False), "send_when_empty", where),
        proof_of_life_weekday=proof_day,
    )
    if radar.lookback_days <= 0:
        raise ConfigError(f"{where}: radar.lookback_days must be positive")

    fetch = FetchSettings(
        timeout_seconds=_as_float(fetch_raw.get("timeout_seconds", 25), "timeout_seconds", where),
        max_workers=max(1, _as_int(fetch_raw.get("max_workers", 6), "max_workers", where)),
        retries=max(0, _as_int(fetch_raw.get("retries", 2), "retries", where)),
        backoff_seconds=_as_float(fetch_raw.get("backoff_seconds", 2.0), "backoff_seconds", where),
        user_agent=str(fetch_raw.get("user_agent", "vp-role-radar/1.0")),
        degraded_failure_ratio=_as_float(
            fetch_raw.get("degraded_failure_ratio", 0.5), "degraded_failure_ratio", where
        ),
    )

    state_path = Path(str(state_raw.get("path", "state/seen.json"))).expanduser()
    if not state_path.is_absolute():
        state_path = PROJECT_ROOT / state_path
    state = StateSettings(
        path=state_path,
        retention_days=_as_int(state_raw.get("retention_days", 45), "retention_days", where),
    )

    email = EmailSettings(
        enabled=_as_bool(email_raw.get("enabled", True), "enabled", where),
        subject_template=str(
            email_raw.get("subject_template", "VP Role Radar — {count} {noun} ({date})")
        ),
        smtp_host=str(email_raw.get("smtp_host", "smtp.gmail.com")),
        smtp_port=_as_int(email_raw.get("smtp_port", 465), "smtp_port", where),
        use_ssl=_as_bool(email_raw.get("use_ssl", True), "use_ssl", where),
        summary_chars=_as_int(email_raw.get("summary_chars", 260), "summary_chars", where),
    )

    digest_path = Path(str(digest_file_raw.get("path", "state/latest-digest.md"))).expanduser()
    if not digest_path.is_absolute():
        digest_path = PROJECT_ROOT / digest_path
    digest_file = DigestFileSettings(
        enabled=_as_bool(digest_file_raw.get("enabled", True), "enabled", where),
        path=digest_path,
    )

    profile = _parse_profile(profile_raw, "profile.yaml")
    sources = _parse_sources(sources_raw, "sources.yaml")
    if not any(source.enabled for source in sources):
        raise ConfigError("sources.yaml: every source is disabled, nothing to fetch")

    return Config(
        radar=radar,
        fetch=fetch,
        state=state,
        email=email,
        digest_file=digest_file,
        profile=profile,
        sources=sources,
        saved_searches=_parse_saved_searches(settings_raw.get("saved_searches"), where),
        config_dir=directory,
    )
