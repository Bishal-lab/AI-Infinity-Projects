"""The source-adapter contract, and the generic engine that implements most of it.

``detect`` and ``parse`` have working, YAML-driven implementations right here.
A channel module exists only to hold what is *genuinely* channel-specific — the
handful of derivations that cannot be expressed as a column alias, such as Teams
deriving COMPLETED from a dwell-time ratio, or WhatsApp OR-ing a reply flag with
a button-tap flag. Everything else is configuration.

That split is also the seam for future API connectors: a Microsoft Graph puller
would produce a DataFrame and hand it to the same ``parse``, reusing the mapping
and the loader unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, SourceMapping, StageLadder
from ..models import STAGE_COLUMNS, LoadReport, SupportStatus
from . import normalize
from .identity import make_key


@dataclass
class FileProbe:
    """The cheap look at a file that source detection works from."""

    path: Path
    headers: tuple[str, ...]

    @property
    def normalised_headers(self) -> set[str]:
        return {normalize.normalise_header(h) for h in self.headers}

    @property
    def filename(self) -> str:
        return self.path.name.lower()


@dataclass
class DetectionScore:
    source: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def __lt__(self, other: DetectionScore) -> bool:
        return self.score < other.score


@dataclass
class LoadContext:
    """Everything an adapter needs that is not in the file itself."""

    settings: Settings
    ladder: StageLadder
    mapping: SourceMapping
    path: Path
    report: LoadReport
    campaign_override: str | None = None
    #: Canonical fields the export actually supplied a column for. An adapter
    #: needs this to tell "the column was there and said no" from "the column
    #: was never there" — a distinction the parsed values alone cannot carry.
    mapped_fields: set[str] = field(default_factory=set)

    @property
    def dayfirst(self) -> bool:
        """Whether 03/04/2026 means 3 April, for *this* source.

        The mapping wins over the global setting when it declares an order, so
        one platform exporting MM/DD/YYYY does not force every other platform's
        dates to be misread.
        """
        order = self.mapping.date_order
        if order is not None:
            return order == "DMY"
        return self.settings.dayfirst


@dataclass
class ParseResult:
    """Canonically-named, coerced columns plus what we learned while parsing."""

    canonical: pd.DataFrame
    #: Stages the channel *can* measure but this export did not carry. Recorded
    #: per campaign so the funnel can say "not measured" rather than "0".
    stages_not_measured: set[str] = field(default_factory=set)
    campaign_raw: pd.Series | None = None
    post_ids: pd.Series | None = None


class SourceAdapter:
    """Base class. Subclasses normally override only ``post_process``."""

    source: str = ""
    grain: str = "recipient"

    def __init__(self, mapping: SourceMapping) -> None:
        self.mapping = mapping
        self.source = mapping.source
        self.grain = mapping.grain

    # -- detection ---------------------------------------------------------- #

    def detect(self, probe: FileProbe) -> DetectionScore:
        """Score how well this file looks like this adapter's source.

        Filename globs and header fingerprints are scored together: a filename
        alone is weak evidence (people rename exports), and headers alone can be
        shared between systems, but the two agreeing is strong.
        """
        spec = self.mapping.detect
        score = 0.0
        reasons: list[str] = []
        headers = probe.normalised_headers

        for pattern in spec.filename_glob:
            regex = re.escape(pattern).replace(r"\*", ".*")
            if re.fullmatch(regex, probe.filename) or re.search(
                regex.strip(".*"), probe.filename
            ):
                score += 2
                reasons.append(f"filename matches {pattern!r}")
                break

        for header in spec.strong:
            if normalize.normalise_header(header) in headers:
                score += 3
                reasons.append(f"strong header {header!r}")
        for header in spec.weak:
            if normalize.normalise_header(header) in headers:
                score += 1
                reasons.append(f"header {header!r}")
        for header in spec.exclude:
            if normalize.normalise_header(header) in headers:
                score -= 5
                reasons.append(f"excluded by {header!r}")

        return DetectionScore(source=self.source, score=score, reasons=reasons)

    # -- parsing ------------------------------------------------------------ #

    def parse(self, df: pd.DataFrame, ctx: LoadContext) -> ParseResult:
        """Map source columns onto canonical fields and coerce them."""
        mapping = ctx.mapping
        report = ctx.report
        index = self._header_index(df)
        used_columns: set[str] = set()
        canonical = pd.DataFrame(index=df.index)

        campaign_raw = self._resolve_campaign(df, ctx, index, used_columns)
        post_ids = self._resolve_post(df, ctx, index, used_columns)
        recipient = self._resolve_recipient(df, ctx, index, used_columns)
        if recipient is not None:
            canonical["recipient_key"] = recipient["key"]
            canonical["recipient_raw"] = recipient["raw"]
            canonical["id_type"] = recipient["id_type"]

        stages_not_measured: set[str] = set()

        for name, spec in mapping.fields.items():
            if spec.is_const:
                canonical[name] = spec.const
                continue

            column = self._find_column(spec.aliases, index)
            if column is None:
                if spec.required:
                    report.add_issue(
                        "error",
                        "MISSING_REQUIRED_COLUMN",
                        f"required field '{name}' not found — looked for any of "
                        f"{list(spec.aliases)}. Add your export's header to "
                        f"config/mappings/{mapping.source}.yaml under "
                        f"fields.{name}.aliases.",
                        column_name=name,
                    )
                else:
                    self._note_optional_missing(name, spec, ctx, stages_not_measured)
                continue

            used_columns.add(column)
            ctx.mapped_fields.add(name)
            result = normalize.coerce(
                df[column],
                spec.type,
                dayfirst=ctx.dayfirst,
                true_values=spec.true_values,
                false_values=spec.false_values,
            )
            canonical[name] = result.series
            if result.failures:
                report.add_issue(
                    "warning",
                    "COERCION_FAILED",
                    f"{result.failures} value(s) in column '{column}' could not be "
                    f"read as {spec.type}; they were left blank rather than guessed.",
                    column_name=column,
                    example_values=result.examples,
                    occurrence_count=result.failures,
                )
        self._report_unknown_columns(df, used_columns, ctx)

        result = ParseResult(
            canonical=canonical,
            stages_not_measured=stages_not_measured,
            campaign_raw=campaign_raw,
            post_ids=post_ids,
        )
        result.canonical = self.post_process(result.canonical, ctx)

        # Judge stage coverage only *after* post_process. Several channels build
        # a stage from columns that are not named after it — WhatsApp derives
        # Opened from a read-receipt timestamp, Teams derives Completed from a
        # dwell ratio — so checking before the derivations would warn about
        # stages that are, in fact, measured.
        self._report_stage_coverage(result, ctx)
        return result

    # -- hooks for subclasses ----------------------------------------------- #

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        """Channel-specific derivations. Default: nothing to derive."""
        return canonical

    def stage_availability(
        self, canonical: pd.DataFrame, ctx: LoadContext
    ) -> dict[str, SupportStatus]:
        """Per-stage support status for *this* export.

        Starts from the channel's declared ladder and downgrades a MEASURED
        stage to NOT_MEASURED when the export carried no usable values for it.
        """
        statuses: dict[str, SupportStatus] = {}
        for stage, column in STAGE_COLUMNS.items():
            declared = ctx.ladder.get(self.source, stage)
            if declared.status is not SupportStatus.MEASURED:
                statuses[stage] = declared.status
                continue
            if column not in canonical.columns or canonical[column].isna().all():
                statuses[stage] = SupportStatus.NOT_MEASURED
            else:
                statuses[stage] = SupportStatus.MEASURED
        return statuses

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _header_index(df: pd.DataFrame) -> dict[str, str]:
        return {normalize.normalise_header(c): str(c) for c in df.columns}

    @staticmethod
    def _find_column(aliases: tuple[str, ...], index: dict[str, str]) -> str | None:
        for alias in aliases:
            column = index.get(normalize.normalise_header(alias))
            if column is not None:
                return column
        return None

    def _note_optional_missing(
        self,
        name: str,
        spec: Any,
        ctx: LoadContext,
        stages_not_measured: set[str],
    ) -> None:
        if ctx.mapping.on_missing_optional == "warn":
            ctx.report.add_issue(
                "info",
                "MISSING_OPTIONAL_COLUMN",
                f"optional field '{name}' not found in this export.",
                column_name=name,
            )

    def _report_stage_coverage(self, result: ParseResult, ctx: LoadContext) -> None:
        """Warn about stages this channel supports but this export did not carry."""
        if self.grain != "recipient":
            return
        for stage, status in self.stage_availability(result.canonical, ctx).items():
            if status is not SupportStatus.NOT_MEASURED:
                continue
            result.stages_not_measured.add(stage)
            ctx.report.stages_not_measured.append(stage)
            ctx.report.add_issue(
                "warning",
                "STAGE_NOT_MEASURED",
                f"{ctx.ladder.channel(self.source).display_name} normally reports "
                f"{stage}, but this export carried no usable values for it. That "
                f"stage will show as 'not measured' rather than zero, so it cannot "
                f"be mistaken for a real result.",
                column_name=STAGE_COLUMNS[stage],
            )

    def _report_unknown_columns(
        self, df: pd.DataFrame, used: set[str], ctx: LoadContext
    ) -> None:
        if ctx.mapping.on_unknown_columns == "ignore":
            return
        unknown = [str(c) for c in df.columns if str(c) not in used]
        if not unknown:
            return
        ctx.report.unmapped_columns = unknown
        ctx.report.add_issue(
            "info",
            "UNMAPPED_COLUMNS",
            f"{len(unknown)} column(s) in this export are not mapped and were "
            f"ignored. If one of them holds data you need, add it to "
            f"config/mappings/{ctx.mapping.source}.yaml.",
            example_values=tuple(unknown[:5]),
            occurrence_count=len(unknown),
        )

    def _resolve_campaign(
        self,
        df: pd.DataFrame,
        ctx: LoadContext,
        index: dict[str, str],
        used: set[str],
    ) -> pd.Series | None:
        """Find the campaign each row belongs to.

        Resolution order — column, then filename pattern, then the admin's
        explicit choice. Teams attendance reports in particular carry no campaign
        identifier at all, which is why the manual path has to exist.
        """
        spec = ctx.mapping.keys.get("campaign")
        if spec is None:
            return None

        if ctx.campaign_override:
            ctx.report.campaign_resolution = "manual"
            return pd.Series(ctx.campaign_override, index=df.index)

        column = self._find_column(spec.aliases, index)
        if column is not None:
            used.add(column)
            values = df[column].map(lambda v: None if pd.isna(v) else str(v).strip())
            if values.notna().any():
                ctx.report.campaign_resolution = "column"
                return values

        if spec.fallback_from_filename:
            match = re.search(spec.fallback_from_filename, ctx.path.name)
            if match:
                campaign = match.groupdict().get("campaign") or match.group(0)
                ctx.report.campaign_resolution = "filename"
                ctx.report.add_issue(
                    "info",
                    "CAMPAIGN_FROM_FILENAME",
                    f"no campaign column found; took campaign {campaign!r} from the "
                    f"filename.",
                )
                return pd.Series(campaign, index=df.index)

        if spec.required:
            ctx.report.add_issue(
                "error",
                "CAMPAIGN_UNRESOLVED",
                "could not work out which campaign this file belongs to. Add a "
                "campaign column to the export, rename the file to match the "
                "pattern in the mapping file, or assign a campaign on the "
                "Data & admin page.",
            )
        return None

    def _resolve_post(
        self,
        df: pd.DataFrame,
        ctx: LoadContext,
        index: dict[str, str],
        used: set[str],
    ) -> pd.Series | None:
        spec = ctx.mapping.keys.get("post")
        if spec is None:
            return None
        column = self._find_column(spec.aliases, index)
        if column is None:
            return None
        used.add(column)
        return df[column].map(lambda v: "-" if pd.isna(v) else str(v).strip())

    def _resolve_recipient(
        self,
        df: pd.DataFrame,
        ctx: LoadContext,
        index: dict[str, str],
        used: set[str],
    ) -> dict[str, pd.Series] | None:
        # Channel exports call this key "recipient"; the roster calls the same
        # thing "employee". Both name the person the row is about.
        spec = ctx.mapping.keys.get("recipient") or ctx.mapping.keys.get("employee")
        if spec is None:
            return None
        column = self._find_column(spec.aliases, index)
        if column is None:
            if spec.required:
                ctx.report.add_issue(
                    "error",
                    "MISSING_RECIPIENT_COLUMN",
                    f"no recipient column found — looked for any of "
                    f"{list(spec.aliases)}.",
                )
            return None

        used.add(column)
        salt = ctx.settings.salt() if ctx.settings.hash_identifiers else None
        raw = df[column].map(
            lambda v: None
            if pd.isna(v)
            else str(v).strip()
        )
        keys = df[column].map(
            lambda v: make_key(
                v,
                spec.canonicalise or ("strip", "lower"),
                salt=salt,
                hash_enabled=ctx.settings.hash_identifiers,
                country_code=ctx.settings.default_country_code,
            )
        )
        blanks = int(keys.isna().sum())
        if blanks:
            ctx.report.add_issue(
                "warning",
                "BLANK_RECIPIENT",
                f"{blanks} row(s) have no recipient identifier and were dropped.",
                column_name=column,
                occurrence_count=blanks,
            )
        return {
            "key": keys,
            "raw": raw,
            "id_type": pd.Series(spec.id_type or "unknown", index=df.index),
        }
