"""Chart builders.

Plotly, rendered through ``st.plotly_chart``, which serves plotly.js from
Streamlit's own bundled static assets — no CDN fetch at runtime, which is what
makes this work on an air-gapped network. (The CDN trap is elsewhere:
``plotly.io.write_html(..., include_plotlyjs="cdn")``. The Excel export path
uses native Excel charts and never touches Plotly.)

Every chart here follows the same rules: thin marks, recessive grid, a legend
whenever more than one series is present, selective direct labels rather than a
number on every point, and — because three light-mode slots sit below 3:1 — a
table view alongside anything colour-encoded.

The rule that shapes these charts more than any styling choice: **a stage a
channel cannot measure is drawn as a gap with an "n/a" label, never as a zero
bar.** A zero bar is a claim that nobody did the thing.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .theme import CHROME, STATUS, Mode, apply_layout, funnel_colours

NA_LABEL = {"not_applicable": "n/a", "not_measured": "not measured"}


def _fmt_int(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.0f}"


def combined_funnel_chart(
    funnel: pd.DataFrame, mode: Mode = "light", height: int = 330
) -> go.Figure:
    """Horizontal funnel of the canonical stages.

    Stages where no channel in view measures anything are drawn as an explicit
    "n/a" annotation at the baseline rather than a zero-length bar.
    """
    chrome = CHROME[mode]
    frame = funnel.sort_values("stage_ordinal")
    colours = funnel_colours(len(frame), mode)

    measured = frame[frame["metric_value"].notna()]
    unmeasured = frame[frame["metric_value"].isna()]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=measured["stage_label"],
            x=measured["metric_value"],
            orientation="h",
            marker=dict(
                color=[colours[int(o) - 1] for o in measured["stage_ordinal"]],
                line=dict(color=chrome["surface"], width=2),
            ),
            text=[_fmt_int(v) for v in measured["metric_value"]],
            textposition="outside",
            textfont=dict(color=chrome["text_secondary"], size=12),
            customdata=measured[["contributing_channels"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>%{x:,.0f} people"
                "<br>from: %{customdata[0]}<extra></extra>"
            ),
            showlegend=False,
            width=0.62,
            cliponaxis=False,
        )
    )

    for _, row in unmeasured.iterrows():
        fig.add_annotation(
            y=row["stage_label"],
            x=0,
            text=f"  {NA_LABEL.get(row['support_status'], 'n/a')}"
            f" — no channel in view measures this stage",
            showarrow=False,
            xanchor="left",
            font=dict(color=chrome["muted"], size=12, style="italic"),
        )

    fig.update_yaxes(autorange="reversed", categoryorder="array",
                     categoryarray=list(frame["stage_label"]))
    apply_layout(fig, mode, height=height, showlegend=False)
    # Headroom so the outside value labels are not clipped by the plot edge.
    largest = measured["metric_value"].max() if not measured.empty else 1
    fig.update_xaxes(showgrid=True, gridcolor=chrome["grid"], title=None,
                     range=[0, float(largest) * 1.18])
    fig.update_layout(margin=dict(l=8, r=64, t=16, b=8), bargap=0.28)
    return fig


def channel_funnel_chart(
    per_channel: pd.DataFrame,
    channel_colour_map: dict[str, str],
    mode: Mode = "light",
    height: int = 380,
) -> go.Figure:
    """Grouped bars: every channel's funnel side by side.

    Channels keep their fixed colour, so this reads consistently against every
    other page. Unmeasured stages simply have no bar — and the accompanying
    table (always rendered beside this chart) says which and why.
    """
    chrome = CHROME[mode]
    fig = go.Figure()

    stage_order = (
        per_channel.sort_values("stage_ordinal")["stage"].drop_duplicates().tolist()
    )
    # Persons only. An interaction count is not on the same scale as a headcount
    # — a community with 2,542 reactions and 389 viewers would flatten every
    # person-counting channel to an invisible sliver against it. Events get
    # their own chart rather than a second y-axis, which would let the author
    # pick the story by picking the scales.
    per_channel = per_channel[per_channel["measure_unit"] == "persons"]

    for channel, block in per_channel.groupby("channel", sort=False):
        block = block.sort_values("stage_ordinal")
        usable = block[block["support_status"] == "measured"]
        display = block["channel_display"].iloc[0] if len(block) else channel
        fig.add_trace(
            go.Bar(
                name=display,
                x=usable["stage"],
                y=usable["metric_value"],
                marker=dict(
                    color=channel_colour_map.get(channel, chrome["muted"]),
                    line=dict(color=chrome["surface"], width=2),
                ),
                customdata=usable[["measure_unit"]].values,
                hovertemplate=(
                    f"<b>{display}</b><br>%{{x}}: %{{y:,.0f}} "
                    "%{customdata[0]}<extra></extra>"
                ),
            )
        )

    fig.update_xaxes(categoryorder="array", categoryarray=stage_order)
    apply_layout(fig, mode, height=height)
    fig.update_layout(barmode="group", bargap=0.30, bargroupgap=0.08)
    return fig


def rate_comparison_chart(
    frame: pd.DataFrame,
    channel_colour_map: dict[str, str],
    value_column: str,
    label_column: str = "channel_display",
    mode: Mode = "light",
    height: int = 300,
    as_percent: bool = True,
) -> go.Figure:
    """Ranked horizontal bars with direct value labels.

    Direct labels are not decoration here — they are the relief the palette
    validator requires for the light-mode slots that fall below 3:1 contrast.
    """
    chrome = CHROME[mode]
    frame = frame[frame[value_column].notna()].sort_values(value_column)
    if frame.empty:
        return _empty_chart("No comparable values in this view", mode, height)

    labels = [
        f"{v:.1%}" if as_percent else f"{v:,.1f}" for v in frame[value_column]
    ]
    fig = go.Figure(
        go.Bar(
            x=frame[value_column],
            y=frame[label_column],
            orientation="h",
            marker=dict(
                color=[
                    channel_colour_map.get(c, chrome["muted"])
                    for c in frame.get("channel", frame[label_column])
                ],
                line=dict(color=chrome["surface"], width=2),
            ),
            text=labels,
            textposition="outside",
            textfont=dict(color=chrome["text_secondary"], size=12),
            hovertemplate="<b>%{y}</b><br>%{x:.1%}<extra></extra>"
            if as_percent
            else "<b>%{y}</b><br>%{x:,.1f}<extra></extra>",
            showlegend=False,
            width=0.6,
            cliponaxis=False,
        )
    )
    apply_layout(fig, mode, height=height, showlegend=False)
    largest = float(frame[value_column].max())
    fig.update_xaxes(showgrid=True, gridcolor=chrome["grid"],
                     tickformat=".0%" if as_percent else None,
                     range=[0, largest * 1.18] if largest > 0 else None)
    fig.update_layout(margin=dict(l=8, r=64, t=16, b=8))
    return fig


def trend_chart(
    frame: pd.DataFrame,
    channel_colour_map: dict[str, str],
    mode: Mode = "light",
    height: int = 320,
) -> go.Figure:
    """Engagement over time, one line per channel.

    One y-axis only. Two measures of different scale get two charts, never a
    second axis — a dual-axis chart lets the author choose the story by choosing
    the scales.
    """
    chrome = CHROME[mode]
    if frame.empty:
        return _empty_chart("No activity in this period", mode, height)

    fig = go.Figure()
    for channel, block in frame.groupby("channel", sort=False):
        block = block.sort_values("period_start")
        display = block["channel_display"].iloc[0]
        fig.add_trace(
            go.Scatter(
                name=display,
                x=block["period_start"],
                y=block["metric_value"],
                mode="lines+markers",
                line=dict(color=channel_colour_map.get(channel, chrome["muted"]), width=2),
                marker=dict(size=8, line=dict(color=chrome["surface"], width=2)),
                hovertemplate=f"<b>{display}</b><br>%{{x|%b %Y}}: "
                "%{y:,.0f} people<extra></extra>",
            )
        )
    apply_layout(fig, mode, height=height)
    fig.update_layout(hovermode="x unified")
    return fig


def coverage_heatmap(
    frame: pd.DataFrame,
    mode: Mode = "light",
    height: int = 400,
    value_column: str = "reach_rate",
) -> go.Figure:
    """Segment x channel reach, on a single-hue sequential ramp.

    Sequential, not categorical: this encodes magnitude, so it is one hue running
    light to dark. Suppressed cells are blank with an explicit note, never zero.
    """
    chrome = CHROME[mode]
    if frame.empty:
        return _empty_chart("No segment data — load an employee roster", mode, height)

    pivot = frame.pivot_table(
        index="segment", columns="channel", values=value_column, aggfunc="first"
    )
    from .theme import SEQUENTIAL

    ramp = SEQUENTIAL[mode]
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=list(pivot.columns),
            y=list(pivot.index),
            colorscale=[[i / (len(ramp) - 1), c] for i, c in enumerate(ramp)],
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1%}<extra></extra>",
            colorbar=dict(
                tickformat=".0%",
                outlinewidth=0,
                tickfont=dict(color=chrome["muted"], size=11),
                thickness=12,
            ),
            xgap=2,
            ygap=2,
            zmin=0,
        )
    )
    apply_layout(fig, mode, height=height, showlegend=False)
    fig.update_yaxes(showgrid=False)
    return fig


def coverage_gap_chart(
    frame: pd.DataFrame, mode: Mode = "light", height: int = 380
) -> go.Figure:
    """Reached vs never-reached headcount per segment, stacked.

    A 2px surface gap between the segments keeps the boundary readable without
    a border colour competing with the fills.
    """
    chrome = CHROME[mode]
    usable = frame[~frame.get("suppressed", False).fillna(False)] if "suppressed" in frame else frame
    usable = usable[usable["reached"].notna()]
    if usable.empty:
        return _empty_chart("No segment data — load an employee roster", mode, height)

    usable = usable.sort_values("coverage_rate", na_position="first")
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Reached",
            y=usable["segment"],
            x=usable["reached"],
            orientation="h",
            marker=dict(color="#2a78d6" if mode == "light" else "#3987e5",
                        line=dict(color=chrome["surface"], width=2)),
            hovertemplate="<b>%{y}</b><br>Reached: %{x:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            name="Never reached",
            y=usable["segment"],
            x=usable["never_reached"],
            orientation="h",
            marker=dict(color=chrome["grid"], line=dict(color=chrome["surface"], width=2)),
            hovertemplate="<b>%{y}</b><br>Never reached: %{x:,.0f}<extra></extra>",
        )
    )
    for _, row in usable.iterrows():
        if pd.notna(row.get("coverage_rate")):
            fig.add_annotation(
                y=row["segment"],
                x=row["headcount"],
                text=f"  {row['coverage_rate']:.0%}",
                showarrow=False,
                xanchor="left",
                font=dict(color=chrome["text_secondary"], size=12),
            )
    apply_layout(fig, mode, height=height)
    fig.update_layout(barmode="stack", bargap=0.3, margin=dict(l=8, r=64, t=32, b=8))
    fig.update_xaxes(showgrid=True, gridcolor=chrome["grid"])
    fig.update_yaxes(showgrid=False)
    return fig


def dwell_histogram(
    values: pd.Series, session_minutes: float | None, mode: Mode = "light", height: int = 300
) -> go.Figure:
    """How long webinar attendees actually stayed."""
    chrome = CHROME[mode]
    values = values.dropna()
    values = values[values > 0]
    if values.empty:
        return _empty_chart("No attendance duration recorded", mode, height)

    fig = go.Figure(
        go.Histogram(
            x=values,
            nbinsx=20,
            marker=dict(color="#2a78d6" if mode == "light" else "#3987e5",
                        line=dict(color=chrome["surface"], width=2)),
            hovertemplate="%{x} min: %{y} attendees<extra></extra>",
        )
    )
    if session_minutes:
        fig.add_vline(
            x=session_minutes,
            line=dict(color=chrome["muted"], width=2, dash="dot"),
            annotation_text="session length",
            annotation_font=dict(color=chrome["muted"], size=11),
        )
    apply_layout(fig, mode, height=height, showlegend=False)
    fig.update_xaxes(title="Minutes attended")
    fig.update_yaxes(title="Attendees")
    return fig


def score_histogram(
    values: pd.Series, pass_mark: float = 70, mode: Mode = "light", height: int = 300
) -> go.Figure:
    """Assessment score distribution with the pass mark marked."""
    chrome = CHROME[mode]
    values = values.dropna()
    if values.empty:
        return _empty_chart("No assessment scores recorded", mode, height)

    fig = go.Figure(
        go.Histogram(
            x=values,
            nbinsx=20,
            marker=dict(color="#2a78d6" if mode == "light" else "#3987e5",
                        line=dict(color=chrome["surface"], width=2)),
            hovertemplate="score %{x}: %{y} learners<extra></extra>",
        )
    )
    fig.add_vline(
        x=pass_mark,
        line=dict(color=STATUS["amber"], width=2, dash="dot"),
        annotation_text="pass mark",
        annotation_font=dict(color=chrome["muted"], size=11),
    )
    apply_layout(fig, mode, height=height, showlegend=False)
    fig.update_xaxes(title="Score")
    fig.update_yaxes(title="Learners")
    return fig


def bullet_chart(
    labels: list[str],
    actuals: list[float],
    targets: list[float],
    mode: Mode = "light",
    height: int = 300,
) -> go.Figure:
    """Actual against target, per campaign.

    The target is a tick mark rather than a second bar — one bar means one
    measured value, and a second bar would read as twice the data.
    """
    chrome = CHROME[mode]
    if not labels:
        return _empty_chart("No campaigns with targets in this view", mode, height)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=labels,
            x=actuals,
            orientation="h",
            marker=dict(color="#2a78d6" if mode == "light" else "#3987e5",
                        line=dict(color=chrome["surface"], width=2)),
            text=[f"{v:.0%}" for v in actuals],
            textposition="outside",
            textfont=dict(color=chrome["text_secondary"], size=12),
            name="Actual",
            hovertemplate="<b>%{y}</b><br>Actual: %{x:.1%}<extra></extra>",
            width=0.5,
            cliponaxis=False,
        )
    )
    for label, target in zip(labels, targets):
        fig.add_shape(
            type="line",
            x0=target,
            x1=target,
            y0=label,
            y1=label,
            yref="y",
            xref="x",
            line=dict(color=chrome["text_primary"], width=3),
        )
    fig.add_trace(
        go.Scatter(
            x=targets,
            y=labels,
            mode="markers",
            marker=dict(symbol="line-ns", size=22, line=dict(
                color=chrome["text_primary"], width=3)),
            name="Target",
            hovertemplate="<b>%{y}</b><br>Target: %{x:.1%}<extra></extra>",
        )
    )
    apply_layout(fig, mode, height=height)
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=chrome["grid"])
    fig.update_layout(margin=dict(l=8, r=64, t=32, b=8))
    return fig


def _empty_chart(message: str, mode: Mode, height: int) -> go.Figure:
    """A chart with no data says so, rather than rendering empty axes."""
    chrome = CHROME[mode]
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        showarrow=False,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        font=dict(color=chrome["muted"], size=13),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    apply_layout(fig, mode, height=height, showlegend=False)
    return fig
