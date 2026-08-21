"""Core value types shared by ingestion, metrics and the UI.

The two ideas that everything else depends on live here:

*Support status* is three-valued. A stage that a channel cannot measure is not
the same as a stage that measured zero, and neither is the same as a stage the
platform supports but this particular export omitted. Collapsing these into a
number is how a funnel ends up telling management that nobody completed a
campaign that had no completion step.

*Measure unit* separates people from interactions. A Viva Engage post can
collect more reactions than the community has members, so those counts can
never be a numerator over an audience size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SupportStatus(str, Enum):
    """Whether a (channel, stage) pair carries a meaningful number."""

    MEASURED = "measured"
    #: The platform has no such concept. Renders "n/a"; excluded from rates.
    NOT_APPLICABLE = "not_applicable"
    #: The platform could report it, but this export did not carry the column.
    NOT_MEASURED = "not_measured"


class MeasureUnit(str, Enum):
    PERSONS = "persons"
    EVENTS = "events"


#: The canonical funnel, in order. Ordinals are used for "max stage reached".
STAGE_ORDER: tuple[str, ...] = ("TARGETED", "DELIVERED", "OPENED", "ENGAGED", "COMPLETED")
STAGE_ORDINAL: dict[str, int] = {name: i + 1 for i, name in enumerate(STAGE_ORDER)}

#: Recipient-grain boolean columns, one per stage, in stage order.
STAGE_COLUMNS: dict[str, str] = {
    "TARGETED": "targeted",
    "DELIVERED": "delivered",
    "OPENED": "opened",
    "ENGAGED": "engaged",
    "COMPLETED": "completed",
}

#: Sources that describe people, not campaigns.
DIMENSION_SOURCES: frozenset[str] = frozenset({"roster", "campaign_registry"})


@dataclass(frozen=True)
class StageSupport:
    """One cell of the stage-support matrix, from ``config/stage_ladder.yaml``."""

    channel: str
    stage: str
    ordinal: int
    status: SupportStatus
    unit: MeasureUnit
    native_name: str | None = None
    caveat: str | None = None

    @property
    def is_measured(self) -> bool:
        return self.status is SupportStatus.MEASURED

    @property
    def counts_people(self) -> bool:
        """True when this cell may take part in person-based funnel arithmetic."""
        return self.is_measured and self.unit is MeasureUnit.PERSONS


@dataclass(frozen=True)
class KpiValue:
    """A rate, plus everything the UI needs to render it honestly.

    Never a bare float: the caller must be able to tell "30%" from "we cannot
    measure this" from "we suppressed this to protect a small group", without
    re-deriving the reason.
    """

    key: str
    label: str
    value: float | None
    numerator: float | None = None
    denominator: float | None = None
    unit: MeasureUnit = MeasureUnit.PERSONS
    status: SupportStatus = SupportStatus.MEASURED
    suppressed: bool = False
    caveats: tuple[str, ...] = ()
    formula: str = ""

    @property
    def is_renderable(self) -> bool:
        return self.value is not None and not self.suppressed

    def display(self, as_percent: bool = True, decimals: int = 1) -> str:
        """The string to show in a tile or table cell."""
        if self.suppressed:
            return "suppressed"
        if self.status is SupportStatus.NOT_APPLICABLE:
            return "n/a"
        if self.status is SupportStatus.NOT_MEASURED:
            return "not measured"
        if self.value is None:
            return "—"
        if as_percent:
            return f"{self.value * 100:.{decimals}f}%"
        return f"{self.value:,.{decimals}f}"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

#: A run of digits and the separators phone numbers are written with. Whether it
#: is actually redacted depends on the digit count, checked in ``_redact_number``
#: — the pattern alone cannot tell ``+44 7724 829869`` from ``550 5.1.1``.
_NUMBER_RE = re.compile(r"\+?\d[\d\s().-]*\d")

#: Digits required before a run is treated as a phone number. Nine is the
#: shortest national significant number in general use, and sits above the eight
#: digits of a compact ISO date — so ``2026-01-05`` and an SMTP ``550 5.1.1``
#: stay legible while every international phone number is caught. A short
#: employee number will pass through: emails and phone numbers are the shapes
#: these exports actually carry, and over-redacting costs the operator the
#: diagnostic the message exists to give them.
_PHONE_MIN_DIGITS = 9

#: Example values are a diagnostic, not a data extract. Anything longer is
#: truncated: a whole SMTP transcript in the issue log helps nobody.
_EXAMPLE_MAX_CHARS = 120


def redact_example(value: str) -> str:
    """Strip identifier-shaped substrings out of a value bound for the issue log.

    Failed values are quoted back to the operator so they can see *what* the
    export contained, and they are persisted twice — to
    ``load_issue.example_values`` and inside the report JSON on ``load_batch``.
    That makes them a route for raw personal data into a warehouse the rest of
    the pipeline is careful to keep hashed. It takes no malice: an ESP that
    writes ``550 5.1.1 <ada@acme.com>: mailbox unavailable`` into its
    delivery-status column puts a real address there, and the rows that fail are
    precisely the ones a bounce report is about — so the leaked set is not even
    random.

    The *shape* survives, which is what makes the message useful in the first
    place: the operator still sees that the column held an SMTP diagnostic
    rather than ``delivered``, with the column name and failure count alongside.
    """
    # Emails first: the local part of an address is digit-bearing often enough
    # that the number pass would otherwise chew a hole in the middle of one.
    value = _EMAIL_RE.sub("<email>", value)
    value = _NUMBER_RE.sub(_redact_number, value)
    if len(value) > _EXAMPLE_MAX_CHARS:
        value = value[:_EXAMPLE_MAX_CHARS] + "…"
    return value


def _redact_number(match: re.Match[str]) -> str:
    """Replace a digit run only if it is long enough to be a phone number."""
    text = match.group(0)
    digits = sum(character.isdigit() for character in text)
    return "<number>" if digits >= _PHONE_MIN_DIGITS else text


@dataclass
class LoadIssue:
    """One problem found while reading a file, surfaced on the admin page."""

    severity: str  # info | warning | error
    code: str
    message: str
    column_name: str | None = None
    example_values: tuple[str, ...] = ()
    occurrence_count: int = 0

    def __post_init__(self) -> None:
        # Redact here rather than at each call site: every issue in the codebase
        # is built through this class, so one gate covers the coercion failures,
        # the unknown-column lists and anything added later.
        self.example_values = tuple(
            redact_example(str(v)) for v in self.example_values
        )


@dataclass
class LoadReport:
    """The full outcome of ingesting one file."""

    file_name: str
    file_sha256: str = ""
    source: str | None = None
    detection_confidence: float = 0.0
    detection_runner_up: str | None = None
    mapping_version: str = ""
    campaign_id: str | None = None
    campaign_resolution: str = ""  # column | filename | manual | registry
    rows_read: int = 0
    rows_loaded: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    unmapped_columns: list[str] = field(default_factory=list)
    stages_not_measured: list[str] = field(default_factory=list)
    identity_resolution_rate: float | None = None
    date_range: tuple[str, str] | None = None
    elapsed_ms: int = 0
    status: str = "pending"  # loaded | loaded_with_warnings | rejected | duplicate
    issues: list[LoadIssue] = field(default_factory=list)

    def add_issue(self, severity: str, code: str, message: str, **kw: Any) -> None:
        self.issues.append(LoadIssue(severity=severity, code=code, message=message, **kw))

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "file_sha256": self.file_sha256,
            "source": self.source,
            "detection_confidence": self.detection_confidence,
            "detection_runner_up": self.detection_runner_up,
            "mapping_version": self.mapping_version,
            "campaign_id": self.campaign_id,
            "campaign_resolution": self.campaign_resolution,
            "rows_read": self.rows_read,
            "rows_loaded": self.rows_loaded,
            "rows_updated": self.rows_updated,
            "rows_rejected": self.rows_rejected,
            "unmapped_columns": self.unmapped_columns,
            "stages_not_measured": self.stages_not_measured,
            "identity_resolution_rate": self.identity_resolution_rate,
            "date_range": list(self.date_range) if self.date_range else None,
            "elapsed_ms": self.elapsed_ms,
            "status": self.status,
            "issues": [
                {
                    "severity": i.severity,
                    "code": i.code,
                    "message": i.message,
                    "column_name": i.column_name,
                    "example_values": list(i.example_values),
                    "occurrence_count": i.occurrence_count,
                }
                for i in self.issues
            ],
        }


class IngestError(Exception):
    """Raised when a file cannot be ingested at all."""


class ConfigError(Exception):
    """Raised when a YAML config file is invalid, naming the offending key."""
