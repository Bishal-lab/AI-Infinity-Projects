"""KPI definitions.

Every rate here states its denominator explicitly, and every one of them can
return "not applicable" rather than a number. That is the whole point: a channel
with no completion step must not contribute a zero to a completion rate, and a
count of interactions must never be divided by a count of people.

The rules, in one place:

* A rate is ``None`` (rendered "n/a") when either side is not measured for that
  channel, when the denominator is zero, or when the numerator counts events and
  the denominator counts people.
* A rate is *suppressed* when its denominator is below ``privacy.min_group_size``.
* Nothing is ever zero-filled to make a chart look complete.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd

from ..config import Settings, StageLadder, get_settings, get_stage_ladder
from ..db import query_df
from ..models import KpiValue, MeasureUnit, SupportStatus
from .filters import FilterState


@dataclass(frozen=True)
class KpiDefinition:
    key: str
    label: str
    numerator: str
    denominator: str
    formula: str
    description: str
    threshold_key: str | None = None
    channels: tuple[str, ...] = ()  # empty means all channels


#: The published KPI set. The Definitions page renders this table verbatim, so
#: the documentation and the computation cannot drift apart.
KPI_DEFINITIONS: tuple[KpiDefinition, ...] = (
    KpiDefinition(
        key="delivery_rate",
        label="Delivery rate",
        numerator="DELIVERED",
        denominator="TARGETED",
        formula="delivered / targeted",
        description=(
            "Share of the intended audience the message technically reached. "
            "Not applicable to channels with no delivery step (LMS assignment, "
            "Teams invitations, Viva posts)."
        ),
        threshold_key="delivery_rate",
    ),
    KpiDefinition(
        key="open_rate",
        label="Open / view rate",
        numerator="OPENED",
        denominator="DELIVERED",
        formula="opened / delivered",
        description=(
            "Share of delivered messages that were seen. Measured against "
            "delivered rather than targeted, so a delivery failure is not scored "
            "as disinterest. Set rules.open_rate_basis to 'targeted' to change this."
        ),
        threshold_key="open_rate",
    ),
    KpiDefinition(
        key="engagement_rate",
        label="Engagement rate",
        numerator="ENGAGED",
        denominator="DELIVERED",
        formula="engaged / delivered",
        description=(
            "Share of delivered messages that produced an active response — a "
            "click, reply, reaction or attendance. The headline effectiveness "
            "measure, because it is far less corrupted by tracking artefacts "
            "than open rate."
        ),
        threshold_key="engagement_rate",
    ),
    KpiDefinition(
        key="click_to_open",
        label="Engagement of those who looked",
        numerator="ENGAGED",
        denominator="OPENED",
        formula="engaged / opened",
        description=(
            "Of the people who saw the message, how many acted. Isolates content "
            "quality from reach — a low number here is a message problem, not a "
            "distribution problem."
        ),
    ),
    KpiDefinition(
        key="completion_rate",
        label="Completion rate",
        numerator="COMPLETED",
        denominator="TARGETED",
        formula="completed / targeted",
        description=(
            "Share of the assigned audience that met the campaign's success "
            "criterion. Measured against everyone assigned, because that is the "
            "population management is accountable for — not just those who started."
        ),
        threshold_key="completion_rate",
        channels=("lms", "teams_webinar"),
    ),
    KpiDefinition(
        key="completion_of_starters",
        label="Completion of those who started",
        numerator="COMPLETED",
        denominator="OPENED",
        formula="completed / opened",
        description=(
            "Of the people who began, how many finished. Separates 'nobody "
            "started' from 'people started and gave up'."
        ),
        channels=("lms", "teams_webinar"),
    ),
    KpiDefinition(
        key="attendance_rate",
        label="Attendance rate",
        numerator="ENGAGED",
        denominator="OPENED",
        formula="attended / registered",
        description=(
            "Of those who registered for the webinar, how many actually joined. "
            "Registration is the OPENED stage for this channel."
        ),
        threshold_key="attendance_rate",
        channels=("teams_webinar",),
    ),
    KpiDefinition(
        key="registration_rate",
        label="Registration rate",
        numerator="OPENED",
        denominator="TARGETED",
        formula="registered / invited",
        description="Of those invited to the webinar, how many registered.",
        channels=("teams_webinar",),
    ),
    KpiDefinition(
        key="stayed_to_end",
        label="Stayed to the end",
        numerator="COMPLETED",
        denominator="ENGAGED",
        formula="completed / attended",
        description=(
            "Of the people who joined the session, how many stayed past the "
            "completion threshold. This is the one rate a plain attendance "
            "report can support — unlike attendance or show-up rate it needs no "
            "invite list, so it stays measurable when Targeted and Registered "
            "do not."
        ),
        channels=("teams_webinar",),
    ),
    KpiDefinition(
        key="show_up_rate",
        label="Show-up rate",
        numerator="ENGAGED",
        denominator="TARGETED",
        formula="attended / invited",
        description=(
            "Of everyone invited, how many attended. Shown separately from "
            "attendance rate because the two are chronically conflated and tell "
            "very different stories."
        ),
        channels=("teams_webinar",),
    ),
)

KPI_BY_KEY = {d.key: d for d in KPI_DEFINITIONS}


# --------------------------------------------------------------------------- #
# The rate primitive
# --------------------------------------------------------------------------- #


def rate(
    key: str,
    label: str,
    numerator: float | None,
    denominator: float | None,
    *,
    numerator_status: SupportStatus = SupportStatus.MEASURED,
    denominator_status: SupportStatus = SupportStatus.MEASURED,
    numerator_unit: MeasureUnit = MeasureUnit.PERSONS,
    denominator_unit: MeasureUnit = MeasureUnit.PERSONS,
    min_group_size: int = 0,
    caveats: tuple[str, ...] = (),
    formula: str = "",
) -> KpiValue:
    """Divide two stage counts, refusing to produce a misleading number.

    Refuses when: either side is not measured, the units disagree (events over
    people is not a rate), or the denominator is zero. Suppresses when the
    denominator describes fewer people than ``min_group_size``.
    """
    common = {
        "key": key,
        "label": label,
        "numerator": numerator,
        "denominator": denominator,
        "unit": numerator_unit,
        "caveats": caveats,
        "formula": formula,
    }

    for status in (numerator_status, denominator_status):
        if status is SupportStatus.NOT_APPLICABLE:
            return KpiValue(value=None, status=SupportStatus.NOT_APPLICABLE, **common)
    for status in (numerator_status, denominator_status):
        if status is SupportStatus.NOT_MEASURED:
            return KpiValue(value=None, status=SupportStatus.NOT_MEASURED, **common)

    if numerator_unit is not denominator_unit:
        # Interactions over people is not a percentage of anything.
        return KpiValue(
            value=None,
            status=SupportStatus.NOT_APPLICABLE,
            **{
                **common,
                "caveats": caveats
                + (
                    f"cannot divide {numerator_unit.value} by "
                    f"{denominator_unit.value} — these count different things",
                ),
            },
        )

    if numerator is None or denominator is None or pd.isna(numerator) or pd.isna(denominator):
        return KpiValue(value=None, status=SupportStatus.NOT_MEASURED, **common)
    if denominator <= 0:
        return KpiValue(value=None, **common)
    if min_group_size and denominator < min_group_size:
        return KpiValue(value=None, suppressed=True, **common)

    return KpiValue(value=float(numerator) / float(denominator), **common)


# --------------------------------------------------------------------------- #
# Funnel queries
# --------------------------------------------------------------------------- #


def funnel_by_channel(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> pd.DataFrame:
    """One row per channel per stage, with support status and unit preserved."""
    where, params = filters.where("f")
    return query_df(
        con,
        f"""
        WITH scoped AS (
            SELECT * FROM v_funnel f WHERE {where}
        ),
        per_campaign AS (
            SELECT campaign_id, channel, stage, stage_ordinal, measure_unit,
                   support_status, aggregation,
                   CASE WHEN aggregation = 'max'
                        THEN MAX(metric_value) ELSE SUM(metric_value) END AS metric_value
            FROM scoped
            GROUP BY campaign_id, channel, stage, stage_ordinal, measure_unit,
                     support_status, aggregation
        )
        SELECT p.channel,
               c.display_name AS channel_display,
               c.sort_order,
               p.stage,
               p.stage_ordinal,
               ANY_VALUE(p.measure_unit) AS measure_unit,
               -- A stage counts as measured for the group if any campaign in
               -- scope measured it; otherwise report the weakest reason.
               CASE WHEN BOOL_OR(p.support_status = 'measured') THEN 'measured'
                    WHEN BOOL_OR(p.support_status = 'not_measured') THEN 'not_measured'
                    ELSE 'not_applicable' END AS support_status,
               SUM(p.metric_value) AS metric_value,
               s.native_name,
               s.caveat
        FROM per_campaign p
        LEFT JOIN dim_channel c ON c.channel = p.channel
        LEFT JOIN dim_channel_stage_support s
               ON s.channel = p.channel AND s.stage = p.stage
        GROUP BY p.channel, c.display_name, c.sort_order, p.stage, p.stage_ordinal,
                 s.native_name, s.caveat
        ORDER BY c.sort_order, p.stage_ordinal
        """,
        params,
    )


def combined_funnel(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    ladder: StageLadder | None = None,
    data_version: int = 0,
) -> pd.DataFrame:
    """The cross-channel funnel.

    Two bases, and the difference matters enough that the UI makes you choose:

    ``sum``   adds the channels together. A person reached by email *and*
              WhatsApp counts twice, so this is a **message** funnel, not a
              people funnel. Always available.
    ``dedup`` counts distinct employees across channels. A real people funnel,
              but it needs identity resolution and excludes aggregate-only
              channels entirely.

    Channels that do not measure a stage are excluded from that stage in both
    the numerator and the denominator — never zero-filled.
    """
    settings = settings or get_settings()
    ladder = ladder or get_stage_ladder()

    if filters.funnel_basis == "dedup":
        return _combined_funnel_dedup(con, filters, ladder)

    per_channel = funnel_by_channel(con, filters, data_version)
    if per_channel.empty:
        return pd.DataFrame(
            columns=["stage", "stage_ordinal", "metric_value", "support_status",
                     "contributing_channels", "excluded_channels"]
        )

    rows: list[dict] = []
    for stage in ladder.stage_names:
        block = per_channel[per_channel["stage"] == stage]
        usable = block[
            (block["support_status"] == "measured")
            & (block["measure_unit"] == MeasureUnit.PERSONS.value)
        ]
        excluded = block[~block.index.isin(usable.index)]
        rows.append(
            {
                "stage": stage,
                "stage_label": ladder.stage(stage).label,
                "stage_ordinal": ladder.stage(stage).ordinal,
                "metric_value": float(usable["metric_value"].sum()) if len(usable) else None,
                "support_status": "measured" if len(usable) else "not_applicable",
                "contributing_channels": ", ".join(
                    sorted(usable["channel_display"].dropna().unique())
                ),
                "excluded_channels": ", ".join(
                    sorted(excluded["channel_display"].dropna().unique())
                ),
            }
        )
    return pd.DataFrame(rows)


def _combined_funnel_dedup(
    con: duckdb.DuckDBPyConnection, filters: FilterState, ladder: StageLadder
) -> pd.DataFrame:
    """Distinct employees per stage, across channels.

    Aggregate-only channels cannot appear here at all — there are no people in
    their data to deduplicate.
    """
    where, params = filters.where("r", include_dates=False)
    frame = query_df(
        con,
        f"""
        SELECT
            COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.targeted  IS TRUE) AS TARGETED,
            COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.delivered IS TRUE) AS DELIVERED,
            COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.opened    IS TRUE) AS OPENED,
            COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.engaged   IS TRUE) AS ENGAGED,
            COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.completed IS TRUE) AS COMPLETED
        FROM fact_recipient_stage r
        JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
        WHERE {where}
        """,
        params,
    )
    aggregate_channels = [
        ladder.channel(c).display_name for c in ladder.channel_names
        if ladder.channel(c).grain == "aggregate"
    ]
    rows: list[dict] = []
    for stage in ladder.stage_names:
        # A stage nobody measures is n/a even here.
        supporting = ladder.channels_supporting(stage)
        in_scope = [c for c in supporting if not filters.channels or c in filters.channels]
        value = float(frame.iloc[0][stage]) if not frame.empty and in_scope else None
        rows.append(
            {
                "stage": stage,
                "stage_label": ladder.stage(stage).label,
                "stage_ordinal": ladder.stage(stage).ordinal,
                "metric_value": value,
                "support_status": "measured" if in_scope else "not_applicable",
                "contributing_channels": ", ".join(
                    ladder.channel(c).display_name for c in in_scope
                ),
                "excluded_channels": ", ".join(aggregate_channels),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# KPI assembly
# --------------------------------------------------------------------------- #


def _stage_lookup(frame: pd.DataFrame, channel: str | None = None) -> dict[str, dict]:
    block = frame if channel is None else frame[frame["channel"] == channel]
    out: dict[str, dict] = {}
    for _, row in block.iterrows():
        out[row["stage"]] = {
            "value": row["metric_value"],
            "status": SupportStatus(row["support_status"]),
            "unit": MeasureUnit(row.get("measure_unit", "persons")),
            "caveat": row.get("caveat"),
        }
    return out


def _kpi_from_definition(
    definition: KpiDefinition,
    stages: dict[str, dict],
    settings: Settings,
    *,
    open_rate_basis: str | None = None,
) -> KpiValue:
    denominator_stage = definition.denominator
    if definition.key == "open_rate" and (open_rate_basis or settings.open_rate_basis) == "targeted":
        denominator_stage = "TARGETED"

    numerator = stages.get(definition.numerator)
    denominator = stages.get(denominator_stage)
    missing = KpiValue(
        key=definition.key,
        label=definition.label,
        value=None,
        status=SupportStatus.NOT_MEASURED,
        formula=definition.formula,
    )
    if numerator is None or denominator is None:
        return missing

    caveats = tuple(
        c for c in (numerator.get("caveat"), denominator.get("caveat")) if c
    )
    return rate(
        definition.key,
        definition.label,
        numerator["value"],
        denominator["value"],
        numerator_status=numerator["status"],
        denominator_status=denominator["status"],
        numerator_unit=numerator["unit"],
        denominator_unit=denominator["unit"],
        min_group_size=settings.min_group_size,
        caveats=caveats,
        formula=definition.formula,
    )


def channel_kpis(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    data_version: int = 0,
) -> dict[str, dict[str, KpiValue]]:
    """Every applicable KPI, per channel."""
    settings = settings or get_settings()
    frame = funnel_by_channel(con, filters, data_version)
    out: dict[str, dict[str, KpiValue]] = {}
    for channel in frame["channel"].unique():
        stages = _stage_lookup(frame, channel)
        out[channel] = {
            d.key: _kpi_from_definition(d, stages, settings)
            for d in KPI_DEFINITIONS
            if not d.channels or channel in d.channels
        }
    return out


def cross_channel_rate(
    definition: KpiDefinition,
    per_channel: pd.DataFrame,
    settings: Settings,
) -> KpiValue:
    """Combine one rate across channels on a *consistent* basis.

    The subtlety that makes or breaks this number: different channels measure
    different stages. Summing every channel's OPENED and dividing by every
    channel's DELIVERED mixes two different populations — with the sample data
    that produces an open rate of 126%, which is visibly wrong, and with real
    data it would produce something merely plausible and still wrong.

    So both sides are restricted to the channels that measure **both** stages.
    Channels outside that intersection are named in the caveat rather than
    silently dropped.
    """
    denominator_stage = definition.denominator
    if definition.key == "open_rate" and settings.open_rate_basis == "targeted":
        denominator_stage = "TARGETED"

    def usable(stage: str) -> pd.DataFrame:
        block = per_channel[per_channel["stage"] == stage]
        return block[
            (block["support_status"] == "measured")
            & (block["measure_unit"] == MeasureUnit.PERSONS.value)
        ]

    numerator_block = usable(definition.numerator)
    denominator_block = usable(denominator_stage)
    shared = sorted(
        set(numerator_block["channel"]) & set(denominator_block["channel"])
    )

    common = {
        "key": definition.key,
        "label": definition.label,
        "formula": definition.formula,
    }
    if not shared:
        return KpiValue(
            value=None,
            status=SupportStatus.NOT_APPLICABLE,
            caveats=(
                f"no channel in this view measures both {definition.numerator} and "
                f"{denominator_stage}, so this rate has no common basis.",
            ),
            **common,
        )

    numerator = float(
        numerator_block[numerator_block["channel"].isin(shared)]["metric_value"].sum()
    )
    denominator = float(
        denominator_block[denominator_block["channel"].isin(shared)]["metric_value"].sum()
    )

    all_channels = set(per_channel["channel"])
    excluded = sorted(all_channels - set(shared))
    names = dict(zip(per_channel["channel"], per_channel["channel_display"]))
    caveats = (
        f"Basis: {', '.join(names.get(c, c) for c in shared)}.",
    )
    if excluded:
        caveats += (
            f"Excluded (does not measure both stages): "
            f"{', '.join(names.get(c, c) for c in excluded)}.",
        )

    return rate(
        definition.key,
        definition.label,
        numerator,
        denominator,
        min_group_size=settings.min_group_size,
        caveats=caveats,
        formula=definition.formula,
    )


def headline_kpis(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    ladder: StageLadder | None = None,
    data_version: int = 0,
) -> dict[str, KpiValue]:
    """The executive tile row: cross-channel rates plus workforce coverage."""
    settings = settings or get_settings()
    ladder = ladder or get_stage_ladder()

    per_channel = funnel_by_channel(con, filters, data_version)
    headline_keys = ("delivery_rate", "open_rate", "engagement_rate", "completion_rate")
    out = {
        key: cross_channel_rate(KPI_BY_KEY[key], per_channel, settings)
        for key in headline_keys
    }
    out["coverage_rate"] = workforce_coverage(con, filters, settings, data_version)
    return out


def workforce_coverage(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> KpiValue:
    """Distinct employees reached (stage >= Opened) over in-scope headcount.

    Needs identity resolution. Without a roster the denominator is unknowable
    and this returns "not measured" rather than a flattering approximation.
    """
    settings = settings or get_settings()

    # No roster means no denominator and no way to recognise a person across
    # channels. Falling through would divide 0 reached by a configured headcount
    # and report a confident, red 0% — which is not a low coverage figure, it is
    # the absence of one.
    resolved = query_df(
        con, "SELECT COUNT(*) AS n FROM v_recipient_identity"
    )
    if resolved.empty or int(resolved.iloc[0]["n"]) == 0:
        return KpiValue(
            key="coverage_rate",
            label="Workforce coverage",
            value=None,
            status=SupportStatus.NOT_MEASURED,
            caveats=(
                "No employee roster is loaded, so no channel identifier can be "
                "resolved to a person and workforce coverage cannot be measured. "
                "Load an HR extract to enable it.",
            ),
            formula="distinct employees reached (>= Opened) / in-scope headcount",
        )

    where, params = filters.where("r", include_dates=False)
    segment_where, segment_params = filters.segment_where("e")

    reached = query_df(
        con,
        f"""
        SELECT COUNT(DISTINCT ri.employee_key) AS reached
        FROM fact_recipient_stage r
        JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
        LEFT JOIN dim_employee e ON e.employee_key = ri.employee_key
        WHERE {where} AND {segment_where} AND r.opened IS TRUE
        """,
        params + segment_params,
    )
    headcount_frame = query_df(
        con,
        f"SELECT COUNT(*) AS headcount FROM dim_employee e "
        f"WHERE {segment_where} AND COALESCE(e.is_active, TRUE)",
        segment_params,
    )
    headcount = int(headcount_frame.iloc[0]["headcount"]) if not headcount_frame.empty else 0
    if not headcount:
        headcount = settings.headcount or 0

    caveats = (
        "Counts distinct employees whose identifier matched the roster. Channels "
        "that report only aggregates (Viva Engage) cannot contribute.",
    )
    return rate(
        # Keyed to match thresholds.coverage_rate in settings.yaml.
        "coverage_rate",
        "Workforce coverage",
        float(reached.iloc[0]["reached"]) if not reached.empty else None,
        float(headcount) if headcount else None,
        min_group_size=settings.min_group_size,
        caveats=caveats,
        formula="distinct employees reached (>= Opened) / in-scope headcount",
    )


def channel_effectiveness_index(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    data_version: int = 0,
) -> pd.DataFrame:
    """A 0-100 composite of the rates each channel actually supports.

    Renormalised across supported stages, so a channel is not punished for
    lacking a stage it was never able to measure. Deliberately only meaningful
    *within* a campaign: channels serve different audiences with different
    intent, and a global league table mostly measures audience, not channel.
    """
    settings = settings or get_settings()
    weights = {"delivery_rate": 0.15, "open_rate": 0.35, "engagement_rate": 0.35,
               "completion_rate": 0.15}
    per_channel = channel_kpis(con, filters, settings, data_version)

    rows: list[dict] = []
    for channel, kpis in per_channel.items():
        usable = {
            key: kpis[key]
            for key in weights
            if key in kpis and kpis[key].is_renderable
        }
        if not usable:
            rows.append({"channel": channel, "index": None, "components": 0,
                         "basis": "no comparable rates"})
            continue
        total_weight = sum(weights[k] for k in usable)
        score = sum(weights[k] * usable[k].value for k in usable) / total_weight
        rows.append(
            {
                "channel": channel,
                "index": round(score * 100, 1),
                "components": len(usable),
                "basis": ", ".join(KPI_BY_KEY[k].label for k in usable),
            }
        )
    return pd.DataFrame(rows).sort_values("index", ascending=False, na_position="last")


def dedupe_availability(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> tuple[bool, str]:
    """Whether the deduplicated funnel basis can be trusted, and why not if not.

    Requires a good identity match rate on at least two recipient-grain
    channels. Below that, adding the channels up is the only honest option and
    the UI says so rather than quietly offering a wrong number.
    """
    settings = settings or get_settings()
    frame = query_df(
        con,
        "SELECT channel, recipients, resolved, resolution_rate "
        "FROM v_identity_resolution WHERE recipients > 0",
    )
    if frame.empty:
        return False, "no recipient-level data has been loaded yet"

    threshold = settings.dedupe_min_resolution
    per_channel = (
        frame.groupby("channel")
        .apply(lambda g: g["resolved"].sum() / max(g["recipients"].sum(), 1), include_groups=False)
        .to_dict()
    )
    good = {c: r for c, r in per_channel.items() if r >= threshold}
    if len(good) >= 2:
        return True, (
            f"{len(good)} channels match the employee roster at or above "
            f"{threshold:.0%}"
        )

    short = ", ".join(
        f"{c} {r:.0%}" for c, r in sorted(per_channel.items(), key=lambda kv: kv[1])
        if r < threshold
    )
    return False, (
        f"cross-channel deduplication needs at least two channels matching the "
        f"roster at {threshold:.0%} or better. Currently short: {short or 'n/a'}. "
        f"Load an employee roster, or add employee IDs to the exports."
    )
