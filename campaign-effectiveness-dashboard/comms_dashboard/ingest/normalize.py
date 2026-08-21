"""Value coercion.

Real exports say "Yes", "TRUE", 1, "Delivered" and "✓" for the same thing, write
a duration as ``01:05:00`` on one row and ``45 min`` on the next, and hand you
``85%`` where you wanted 0.85. This module turns all of that into typed columns.

Two rules run through everything here:

* **Blank is unknown, not False.** An empty cell means the export did not tell
  us. Coercing that to ``False`` invents a measurement, and the funnel would
  report it as a real negative.
* **Date order is configured, never inferred.** ``03/04/2026`` is genuinely
  ambiguous and guessing per-file is the most common silent corruption in a tool
  like this. ``parsing.date_order`` in settings.yaml decides, globally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from ..models import redact_example

DEFAULT_TRUE = {
    "true", "t", "yes", "y", "1", "1.0", "on", "success", "ok", "✓", "x",
    "delivered", "opened", "read", "clicked", "attended", "registered",
    "completed", "complete", "passed", "pass", "active", "enabled",
}
DEFAULT_FALSE = {
    "false", "f", "no", "n", "0", "0.0", "off", "failed", "fail",
    "bounced", "blocked", "dropped", "undelivered", "expired", "declined",
    "not started", "notstarted", "in progress", "inprogress", "incomplete",
    "not registered", "notregistered", "none", "never", "inactive", "disabled",
}

_HEADER_CLEAN = re.compile(r"[^a-z0-9]+")
_NUMERIC_CLEAN = re.compile(r"[^\d.\-]")


@dataclass
class ColumnResult:
    """A coerced column plus what went wrong while coercing it."""

    series: pd.Series
    failures: int = 0
    examples: tuple[str, ...] = ()
    #: How many cells were blank — reported separately from failures, because a
    #: blank is legitimately "unknown" while a failure is a value we could not read.
    blanks: int = 0
    notes: list[str] = field(default_factory=list)


def normalise_header(name: object) -> str:
    """Fold a column header for alias matching.

    ``"Unique Opens "``, ``"unique_opens"`` and ``"UNIQUE-OPENS"`` all collapse
    to ``uniqueopens``, so an admin adding an alias does not have to reproduce
    the source system's exact punctuation.
    """
    return _HEADER_CLEAN.sub("", str(name).strip().lower())


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _collect(failed: list[str], limit: int = 3) -> tuple[str, ...]:
    """Up to ``limit`` distinct failed values, redacted for the issue log.

    ``LoadIssue`` redacts on construction too — this is not belt and braces but
    ordering: de-duplicating *after* redaction means two different addresses in
    the same column collapse to one ``<email>`` example, instead of spending the
    whole quota proving the same point three times.
    """
    seen: list[str] = []
    for value in failed:
        cleaned = redact_example(value)
        if cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return tuple(seen)


def to_bool(
    series: pd.Series,
    true_values: tuple[str, ...] = (),
    false_values: tuple[str, ...] = (),
) -> ColumnResult:
    """Coerce to nullable boolean. Blank stays ``None``."""
    trues = DEFAULT_TRUE | {str(v).strip().lower() for v in true_values}
    falses = DEFAULT_FALSE | {str(v).strip().lower() for v in false_values}
    # An explicit list wins over the defaults: a mapping that calls "registered"
    # False must not be overridden by the default that calls it True.
    if true_values:
        falses -= {str(v).strip().lower() for v in true_values}
    if false_values:
        trues -= {str(v).strip().lower() for v in false_values}

    out: list[bool | None] = []
    failed: list[str] = []
    blanks = 0
    for value in series:
        if _is_blank(value):
            out.append(None)
            blanks += 1
            continue
        if isinstance(value, bool):
            out.append(value)
            continue
        token = str(value).strip().lower()
        if token in trues:
            out.append(True)
        elif token in falses:
            out.append(False)
        else:
            out.append(None)
            failed.append(str(value))
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="object"),
        failures=len(failed),
        examples=_collect(failed),
        blanks=blanks,
    )


def to_bool_from_count(series: pd.Series) -> ColumnResult:
    """A count column read as a flag: ``>0`` is True, ``0`` is False, blank is None.

    This is how email tools report opens and clicks — the number matters less
    than whether it happened at all, and a 0 there is a genuine negative.
    """
    numbers = to_number(series)
    out: list[bool | None] = [
        None if _is_blank(v) or pd.isna(v) else bool(float(v) > 0) for v in numbers.series
    ]
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="object"),
        failures=numbers.failures,
        examples=numbers.examples,
        blanks=numbers.blanks,
    )


def to_number(series: pd.Series) -> ColumnResult:
    out: list[float | None] = []
    failed: list[str] = []
    blanks = 0
    for value in series:
        if _is_blank(value):
            out.append(None)
            blanks += 1
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
            continue
        cleaned = _NUMERIC_CLEAN.sub("", str(value))
        try:
            out.append(float(cleaned))
        except ValueError:
            out.append(None)
            failed.append(str(value))
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="float64"),
        failures=len(failed),
        examples=_collect(failed),
        blanks=blanks,
    )


def to_percent(series: pd.Series) -> ColumnResult:
    """Coerce to a 0–1 fraction.

    ``"85%"`` and ``85`` both become 0.85; ``0.85`` stays 0.85. A bare ``1`` is
    read as 100%, not 1% — the far more common intent in a progress column.
    """
    out: list[float | None] = []
    failed: list[str] = []
    blanks = 0
    for value in series:
        if _is_blank(value):
            out.append(None)
            blanks += 1
            continue
        text = str(value).strip()
        had_sign = text.endswith("%")
        cleaned = _NUMERIC_CLEAN.sub("", text)
        try:
            number = float(cleaned)
        except ValueError:
            out.append(None)
            failed.append(text)
            continue
        out.append(number / 100.0 if (had_sign or number > 1.0) else number)
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="float64"),
        failures=len(failed),
        examples=_collect(failed),
        blanks=blanks,
    )


_DURATION_HMS = re.compile(r"^(\d+):([0-5]?\d)(?::([0-5]?\d))?$")
_DURATION_UNITS = re.compile(
    r"(?:(?P<h>\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours))?\s*"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes))?\s*"
    r"(?:(?P<s>\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds))?$",
    re.IGNORECASE,
)
_DURATION_ISO = re.compile(
    r"^P?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$", re.IGNORECASE
)


def to_duration_minutes(series: pd.Series) -> ColumnResult:
    """Coerce a duration to float minutes.

    Handles the spellings that turn up in the same Teams attendance column:
    ``65``, ``"65 min"``, ``"1h 05m"``, ``"01:05:00"``, ``"1.5 hours"`` and the
    ISO-8601 ``PT1H5M`` that Graph API returns.
    """
    out: list[float | None] = []
    failed: list[str] = []
    blanks = 0
    for value in series:
        if _is_blank(value):
            out.append(None)
            blanks += 1
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
            continue
        if isinstance(value, pd.Timedelta):
            out.append(value.total_seconds() / 60.0)
            continue
        text = str(value).strip()

        match = _DURATION_HMS.match(text)
        if match:
            hours, minutes, seconds = match.groups()
            out.append(int(hours) * 60 + int(minutes) + (int(seconds or 0) / 60.0))
            continue

        iso = _DURATION_ISO.match(text)
        if iso and any(iso.groupdict().values()) and text.upper().startswith(("P", "T")):
            h, m, s = (float(iso.group(k) or 0) for k in ("h", "m", "s"))
            out.append(h * 60 + m + s / 60.0)
            continue

        units = _DURATION_UNITS.match(text)
        if units and any(units.groupdict().values()):
            h, m, s = (float(units.group(k) or 0) for k in ("h", "m", "s"))
            out.append(h * 60 + m + s / 60.0)
            continue

        # A bare number with stray text around it: assume minutes.
        cleaned = _NUMERIC_CLEAN.sub("", text)
        try:
            out.append(float(cleaned))
        except ValueError:
            out.append(None)
            failed.append(text)
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="float64"),
        failures=len(failed),
        examples=_collect(failed),
        blanks=blanks,
    )


def to_datetime(series: pd.Series, dayfirst: bool) -> ColumnResult:
    """Parse timestamps with a *configured* day/month order.

    Excel serial numbers (the 40000-ish integers you get when a date column
    survives a round trip through Excel) are recognised and converted.
    """
    blanks = int(sum(1 for v in series if _is_blank(v)))
    working = series.copy()

    # Excel serials: plausible dates from 1990 to 2050 land in this range.
    numeric = pd.to_numeric(working, errors="coerce")
    serial_mask = numeric.between(32000, 55000) & working.map(
        lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    )

    parsed = pd.to_datetime(
        working.where(~serial_mask),
        errors="coerce",
        dayfirst=dayfirst,
        format="mixed",
    )
    if serial_mask.any():
        serial_dates = pd.to_datetime(
            numeric[serial_mask], unit="D", origin="1899-12-30", errors="coerce"
        )
        parsed = parsed.astype("datetime64[ns]")
        parsed.loc[serial_mask] = serial_dates

    failed_mask = parsed.isna() & series.map(lambda v: not _is_blank(v))
    failed = [str(v) for v in series[failed_mask]]
    return ColumnResult(
        series=parsed,
        failures=int(failed_mask.sum()),
        examples=_collect(failed),
        blanks=blanks,
    )


def to_date(series: pd.Series, dayfirst: bool) -> ColumnResult:
    result = to_datetime(series, dayfirst)
    result.series = result.series.dt.date
    return result


def to_string(series: pd.Series) -> ColumnResult:
    out = series.map(lambda v: None if _is_blank(v) else str(v).strip())
    return ColumnResult(
        series=pd.Series(out, index=series.index, dtype="object"),
        blanks=int(sum(1 for v in out if v is None)),
    )


COERCERS = {
    "bool": to_bool,
    "bool_from_count": to_bool_from_count,
    "number": to_number,
    "percent": to_percent,
    "duration_minutes": to_duration_minutes,
    "datetime": to_datetime,
    "date": to_date,
    "string": to_string,
}


def coerce(
    series: pd.Series,
    type_name: str,
    *,
    dayfirst: bool = True,
    true_values: tuple[str, ...] = (),
    false_values: tuple[str, ...] = (),
) -> ColumnResult:
    """Dispatch to the coercer named in the mapping YAML."""
    if type_name == "bool":
        return to_bool(series, true_values, false_values)
    if type_name in {"datetime", "date"}:
        return COERCERS[type_name](series, dayfirst)  # type: ignore[operator]
    if type_name not in COERCERS:
        raise ValueError(
            f"unknown field type {type_name!r}; expected one of {sorted(COERCERS)}"
        )
    return COERCERS[type_name](series)  # type: ignore[operator]


def apply_alias_map(value: object, aliases: dict[str, str]) -> object:
    """Fold a taxonomy value through the alias table (``"Fin"`` -> ``"Finance"``)."""
    if _is_blank(value):
        return None
    text = str(value).strip()
    if text in aliases:
        return aliases[text]
    lowered = {k.strip().lower(): v for k, v in aliases.items()}
    return lowered.get(text.lower(), text)
