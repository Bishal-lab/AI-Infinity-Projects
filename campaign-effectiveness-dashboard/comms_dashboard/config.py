"""Loading and validating the YAML configuration.

Everything the dashboard does is driven by three sets of files:

* ``config/settings.yaml``      — deployment settings, thresholds, privacy
* ``config/stage_ladder.yaml``  — the canonical funnel and the support matrix
* ``config/mappings/*.yaml``    — one per source: detection rules + column aliases

Validation is deliberately loud and specific. A typo in a YAML key should say
which key in which file, not surface three modules later as a KeyError.
"""

from __future__ import annotations

import os
import secrets
import stat
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .models import ConfigError, MeasureUnit, StageSupport, SupportStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - message passthrough
        raise ConfigError(f"{path.name}: invalid YAML — {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a mapping at the top level")
    return data


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Threshold:
    """A RAG band. Green at or above target, amber at or above target*factor."""

    target: float
    amber_factor: float = 0.85

    def rag(self, value: float | None) -> str:
        if value is None:
            return "grey"
        if value >= self.target:
            return "green"
        if value >= self.target * self.amber_factor:
            return "amber"
        return "red"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    root: Path

    # -- app ---------------------------------------------------------------- #
    @property
    def title(self) -> str:
        return self.raw["app"].get("title", "Internal Communications Effectiveness")

    @property
    def org_name(self) -> str:
        return self.raw["app"].get("org_name", "")

    @property
    def headcount(self) -> int | None:
        value = self.raw["app"].get("headcount")
        return int(value) if value else None

    # -- paths -------------------------------------------------------------- #
    def path(self, name: str) -> Path:
        """Resolve a configured path relative to the project root."""
        paths = self.raw["paths"]
        if name not in paths:
            raise ConfigError(f"settings.yaml: paths.{name} is not configured")
        p = Path(paths[name])
        return p if p.is_absolute() else (self.root / p)

    # -- parsing ------------------------------------------------------------ #
    @property
    def date_order(self) -> str:
        return str(self.raw["parsing"].get("date_order", "ISO")).upper()

    @property
    def dayfirst(self) -> bool:
        """Whether 03/04/2026 means 3 April. Configured, never inferred."""
        return self.date_order == "DMY"

    @property
    def default_country_code(self) -> str:
        return str(self.raw["parsing"].get("default_country_code", "+44"))

    @property
    def period_grain(self) -> str:
        return str(self.raw["parsing"].get("period_grain", "month"))

    # -- taxonomy ----------------------------------------------------------- #
    @property
    def departments(self) -> list[str]:
        return list(self.raw.get("taxonomy", {}).get("departments", []))

    @property
    def regions(self) -> list[str]:
        return list(self.raw.get("taxonomy", {}).get("regions", []))

    @property
    def department_aliases(self) -> dict[str, str]:
        return dict(self.raw.get("taxonomy", {}).get("department_aliases", {}) or {})

    # -- rules -------------------------------------------------------------- #
    @property
    def webinar_completion_fraction(self) -> float:
        return float(self.raw.get("rules", {}).get("webinar_completion_fraction", 0.5))

    @property
    def open_rate_basis(self) -> str:
        basis = str(self.raw.get("rules", {}).get("open_rate_basis", "delivered")).lower()
        if basis not in {"delivered", "targeted"}:
            raise ConfigError(
                "settings.yaml: rules.open_rate_basis must be 'delivered' or 'targeted', "
                f"got {basis!r}"
            )
        return basis

    @property
    def dedupe_min_resolution(self) -> float:
        return float(self.raw.get("rules", {}).get("dedupe_min_resolution", 0.8))

    # -- thresholds --------------------------------------------------------- #
    @property
    def thresholds(self) -> dict[str, Threshold]:
        out: dict[str, Threshold] = {}
        for key, spec in (self.raw.get("thresholds") or {}).items():
            if not isinstance(spec, dict) or "target" not in spec:
                raise ConfigError(f"settings.yaml: thresholds.{key} needs a 'target'")
            out[key] = Threshold(
                target=float(spec["target"]),
                amber_factor=float(spec.get("amber_factor", 0.85)),
            )
        return out

    # -- privacy ------------------------------------------------------------ #
    @property
    def hash_identifiers(self) -> bool:
        return bool(self.raw.get("privacy", {}).get("hash_identifiers", True))

    @property
    def min_group_size(self) -> int:
        return int(self.raw.get("privacy", {}).get("min_group_size", 5))

    @property
    def complementary_suppression(self) -> bool:
        return bool(self.raw.get("privacy", {}).get("complementary_suppression", True))

    @property
    def allow_individual_drilldown(self) -> bool:
        return bool(self.raw.get("privacy", {}).get("allow_individual_drilldown", False))

    @property
    def retention_months(self) -> int:
        return int(self.raw.get("privacy", {}).get("retention_months", 24))

    def salt(self) -> bytes:
        """The pseudonymisation salt, generated on first use.

        Kept in a file outside git and outside the container image. Losing it
        breaks every existing join, so the file is created once and never
        rotated automatically.

        Permissions are enforced on every read, not only at creation. The
        documented backup procedure — copy the file away, restore it later — is
        the realistic way a salt ends up world-readable, and a salt created
        correctly two years ago says nothing about the mode of the copy sitting
        there now.
        """
        salt_path = self.raw.get("privacy", {}).get("salt_file", "config/secret_salt")
        p = Path(salt_path)
        if not p.is_absolute():
            p = self.root / p
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            # Created through os.open with the mode set up front: write_text()
            # followed by chmod leaves the salt readable at the process umask
            # for the window in between, and O_EXCL means two processes racing
            # to first use cannot each generate one and then disagree.
            try:
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:  # another process won the race; use theirs
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(secrets.token_hex(32))
        _enforce_salt_permissions(p)
        return p.read_text(encoding="utf-8").strip().encode("utf-8")


def _enforce_salt_permissions(path: Path) -> None:
    """Narrow the salt to owner-only, and say so out loud if that is impossible.

    Silence was the real problem with the previous ``except OSError: pass``: on
    a filesystem that cannot represent POSIX modes — a Windows share, an SMB
    mount, some container volume drivers — the salt stays world-readable and
    nothing tells the operator, who has every reason to believe the README's
    claim that it is protected.
    """
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:  # pragma: no cover - the read below reports it properly
        return
    if not mode & 0o077:
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        warnings.warn(
            f"Could not restrict permissions on the pseudonymisation salt "
            f"{path} (mode {mode:04o}). Anyone who can read this file can "
            f"reverse every hashed identifier in the warehouse. Move it to a "
            f"filesystem that supports POSIX permissions, or set "
            f"privacy.salt_file to a path that does.",
            RuntimeWarning,
            stacklevel=2,
        )


def load_settings(path: Path | None = None) -> Settings:
    path = path or (CONFIG_DIR / "settings.yaml")
    raw = _read_yaml(path)
    for section in ("app", "paths", "parsing"):
        _require(raw, section, path.name)
    settings = Settings(raw=raw, root=PROJECT_ROOT)
    if settings.date_order not in {"DMY", "MDY", "ISO"}:
        raise ConfigError(
            f"settings.yaml: parsing.date_order must be DMY, MDY or ISO, "
            f"got {settings.date_order!r}"
        )
    settings.open_rate_basis  # noqa: B018 - validate eagerly
    settings.thresholds  # noqa: B018 - validate eagerly
    return settings


# --------------------------------------------------------------------------- #
# Stage ladder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Stage:
    name: str
    ordinal: int
    label: str
    description: str = ""


@dataclass(frozen=True)
class Channel:
    name: str
    display_name: str
    grain: str
    sort_order: int


@dataclass(frozen=True)
class StageLadder:
    stages: tuple[Stage, ...]
    channels: tuple[Channel, ...]
    support: dict[tuple[str, str], StageSupport] = field(default_factory=dict)

    def stage(self, name: str) -> Stage:
        for s in self.stages:
            if s.name == name:
                return s
        raise ConfigError(f"stage_ladder.yaml: unknown stage {name!r}")

    def channel(self, name: str) -> Channel:
        for c in self.channels:
            if c.name == name:
                return c
        raise ConfigError(f"stage_ladder.yaml: unknown channel {name!r}")

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in sorted(self.channels, key=lambda c: c.sort_order))

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in sorted(self.stages, key=lambda s: s.ordinal))

    def get(self, channel: str, stage: str) -> StageSupport:
        try:
            return self.support[(channel, stage)]
        except KeyError as exc:
            raise ConfigError(
                f"stage_ladder.yaml: no support entry for channel {channel!r}, "
                f"stage {stage!r}"
            ) from exc

    def channels_supporting(self, stage: str) -> tuple[str, ...]:
        """Channels whose value for ``stage`` may enter person-based arithmetic."""
        return tuple(
            c for c in self.channel_names if self.get(c, stage).counts_people
        )

    def recipient_channels(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.channels if c.grain == "recipient")


def load_stage_ladder(path: Path | None = None) -> StageLadder:
    path = path or (CONFIG_DIR / "stage_ladder.yaml")
    raw = _read_yaml(path)

    stage_specs = _require(raw, "stages", path.name)
    stages = tuple(
        Stage(
            name=_require(s, "name", f"{path.name}:stages"),
            ordinal=int(_require(s, "ordinal", f"{path.name}:stages")),
            label=s.get("label", s["name"].title()),
            description=(s.get("description") or "").strip(),
        )
        for s in stage_specs
    )
    stage_names = {s.name for s in stages}

    channel_specs = _require(raw, "channels", path.name)
    channels: list[Channel] = []
    support: dict[tuple[str, str], StageSupport] = {}

    for cname, spec in channel_specs.items():
        where = f"{path.name}:channels.{cname}"
        grain = spec.get("grain", "recipient")
        if grain not in {"recipient", "aggregate"}:
            raise ConfigError(f"{where}: grain must be 'recipient' or 'aggregate'")
        channels.append(
            Channel(
                name=cname,
                display_name=spec.get("display_name", cname.replace("_", " ").title()),
                grain=grain,
                sort_order=int(spec.get("sort_order", 99)),
            )
        )
        cell_specs = _require(spec, "stages", where)
        missing = stage_names - set(cell_specs)
        if missing:
            # An omitted cell is ambiguous — force the author to say which of the
            # three statuses they mean rather than defaulting to one.
            raise ConfigError(
                f"{where}.stages: every stage needs an explicit entry; missing "
                f"{sorted(missing)}"
            )
        for sname, cell in cell_specs.items():
            if sname not in stage_names:
                raise ConfigError(f"{where}.stages: unknown stage {sname!r}")
            try:
                status = SupportStatus(cell.get("status", "measured"))
            except ValueError as exc:
                raise ConfigError(
                    f"{where}.stages.{sname}.status must be one of "
                    f"{[s.value for s in SupportStatus]}"
                ) from exc
            try:
                unit = MeasureUnit(cell.get("unit", "persons"))
            except ValueError as exc:
                raise ConfigError(
                    f"{where}.stages.{sname}.unit must be 'persons' or 'events'"
                ) from exc
            ordinal = next(s.ordinal for s in stages if s.name == sname)
            support[(cname, sname)] = StageSupport(
                channel=cname,
                stage=sname,
                ordinal=ordinal,
                status=status,
                unit=unit,
                native_name=cell.get("native_name"),
                caveat=(cell.get("caveat") or "").strip() or None,
            )

    return StageLadder(stages=stages, channels=tuple(channels), support=support)


# --------------------------------------------------------------------------- #
# Source mappings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    """How one canonical field is found and coerced from a source column."""

    name: str
    aliases: tuple[str, ...] = ()
    type: str = "string"
    required: bool = False
    const: Any = None
    true_values: tuple[str, ...] = ()
    false_values: tuple[str, ...] = ()

    @property
    def is_const(self) -> bool:
        return self.const is not None


@dataclass(frozen=True)
class KeySpec:
    name: str
    aliases: tuple[str, ...] = ()
    id_type: str | None = None
    canonicalise: tuple[str, ...] = ()
    fallback_from_filename: str | None = None
    required: bool = True


@dataclass(frozen=True)
class DetectSpec:
    priority: int = 10
    filename_glob: tuple[str, ...] = ()
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    min_score: int = 4


@dataclass(frozen=True)
class SourceMapping:
    source: str
    display_name: str
    grain: str
    mapping_version: str
    detect: DetectSpec
    read: dict[str, Any]
    keys: dict[str, KeySpec]
    fields: dict[str, FieldSpec]
    on_unknown_columns: str = "warn"
    on_missing_optional: str = "warn"
    on_missing_required: str = "reject_file"

    @property
    def date_order(self) -> str | None:
        """Per-source day/month order, or None to inherit the global setting.

        Two platforms in the same organisation routinely disagree: an LMS may
        export MM/DD/YYYY while the mail tool exports DD/MM/YYYY. A single
        global setting cannot be right for both, and picking one silently
        corrupts the other — so a mapping may override it for its own source.
        """
        order = self.read.get("date_order")
        if order is None:
            return None
        order = str(order).upper()
        if order not in {"DMY", "MDY", "ISO"}:
            raise ConfigError(
                f"{self.source}.yaml: read.date_order must be DMY, MDY or ISO, "
                f"got {order!r}"
            )
        return order

    def required_field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields.values() if f.required and not f.is_const)


def _field_from_spec(name: str, spec: Any, where: str) -> FieldSpec:
    if not isinstance(spec, dict):
        raise ConfigError(f"{where}.fields.{name}: expected a mapping")
    return FieldSpec(
        name=name,
        aliases=tuple(spec.get("aliases", ()) or ()),
        type=spec.get("type", "string"),
        required=bool(spec.get("required", False)),
        const=spec.get("const"),
        true_values=tuple(str(v) for v in spec.get("true_values", ()) or ()),
        false_values=tuple(str(v) for v in spec.get("false_values", ()) or ()),
    )


def load_source_mapping(path: Path) -> SourceMapping:
    raw = _read_yaml(path)
    where = path.name
    detect_raw = raw.get("detect", {}) or {}
    fingerprint = detect_raw.get("header_fingerprint", {}) or {}

    keys: dict[str, KeySpec] = {}
    for kname, kspec in (raw.get("keys") or {}).items():
        keys[kname] = KeySpec(
            name=kname,
            aliases=tuple(kspec.get("aliases", ()) or ()),
            id_type=kspec.get("id_type"),
            canonicalise=tuple(kspec.get("canonicalise", ()) or ()),
            fallback_from_filename=kspec.get("fallback_from_filename"),
            required=bool(kspec.get("required", True)),
        )

    fields = {
        name: _field_from_spec(name, spec, where)
        for name, spec in (raw.get("fields") or {}).items()
    }

    return SourceMapping(
        source=_require(raw, "source", where),
        display_name=raw.get("display_name", raw["source"]),
        grain=raw.get("grain", "recipient"),
        mapping_version=str(raw.get("mapping_version", "0")),
        detect=DetectSpec(
            priority=int(detect_raw.get("priority", 10)),
            filename_glob=tuple(detect_raw.get("filename_glob", ()) or ()),
            strong=tuple(fingerprint.get("strong", ()) or ()),
            weak=tuple(fingerprint.get("weak", ()) or ()),
            exclude=tuple(fingerprint.get("exclude", ()) or ()),
            min_score=int(detect_raw.get("min_score", 4)),
        ),
        read=dict(raw.get("read", {}) or {}),
        keys=keys,
        fields=fields,
        on_unknown_columns=raw.get("on_unknown_columns", "warn"),
        on_missing_optional=raw.get("on_missing_optional", "warn"),
        on_missing_required=raw.get("on_missing_required", "reject_file"),
    )


def load_all_mappings(directory: Path | None = None) -> dict[str, SourceMapping]:
    directory = directory or (CONFIG_DIR / "mappings")
    if not directory.exists():
        raise ConfigError(f"mapping directory not found: {directory}")
    mappings: dict[str, SourceMapping] = {}
    for path in sorted(directory.glob("*.yaml")):
        mapping = load_source_mapping(path)
        if mapping.source in mappings:
            raise ConfigError(
                f"{path.name}: duplicate source {mapping.source!r} "
                f"(already defined in another mapping file)"
            )
        mappings[mapping.source] = mapping
    if not mappings:
        raise ConfigError(f"no mapping files found in {directory}")
    return mappings


# --------------------------------------------------------------------------- #
# Cached accessors used by the app and CLI
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def get_stage_ladder() -> StageLadder:
    return load_stage_ladder()


@lru_cache(maxsize=1)
def get_mappings() -> dict[str, SourceMapping]:
    return load_all_mappings()


def reset_caches() -> None:
    """Drop cached config — used by tests and the admin page's reload button."""
    get_settings.cache_clear()
    get_stage_ladder.cache_clear()
    get_mappings.cache_clear()
