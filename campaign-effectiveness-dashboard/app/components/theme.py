"""Palette and chart chrome.

Every colour the dashboard uses is declared here once. The categorical slots are
assigned to channels in a **fixed order that never changes** — email is always
blue, WhatsApp always orange — so filtering to three channels never repaints the
survivors, and a reader who learns the colours on one page keeps them on every
other.

The palettes are not chosen by eye. They were run through the dataviz skill's
validator (``scripts/validate_palette.js``) and pass every hard gate:

    categorical light  worst adjacent CVD ΔE 9.1, normal-vision ΔE 19.6
    categorical dark   worst adjacent CVD ΔE 8.4, normal-vision ΔE 19.3
    funnel ordinal     monotone lightness, all adjacent ΔL >= 0.06, both modes

Three light-mode slots sit below 3:1 against the surface, so the validator's
relief rule applies: every chart here ships visible direct labels and a table
view, and identity is never carried by colour alone.
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["light", "dark"]

# Categorical slots, in the validated order. Index = channel sort_order - 1.
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
}

# Ordinal ramp for the funnel: one hue, light to dark, widened steps so adjacent
# stages are visibly different.
FUNNEL_RAMP = {
    "light": ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"],
    "dark": ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"],
}

# Sequential ramp for heatmaps (continuous magnitude, may recede to the surface).
SEQUENTIAL = {
    "light": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#104281"],
    "dark": ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"],
}

# Status colours are reserved: never reused as a series colour, and always
# shipped with a symbol and a label so state is not carried by hue alone.
STATUS = {
    "green": "#0ca30c",
    "amber": "#fab219",
    "red": "#d03b3b",
    "grey": "#898781",
}

CHROME = {
    "light": {
        "surface": "#fcfcfb",
        "page": "#f9f9f7",
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "surface": "#1a1a19",
        "page": "#0d0d0d",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "border": "rgba(255,255,255,0.10)",
    },
}

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def channel_colours(channel_order: list[str], mode: Mode = "light") -> dict[str, str]:
    """Fixed channel -> colour map.

    Assigned by the channel's position in the stage ladder, not by whatever
    happens to be on screen, so the mapping is stable across pages and filters.
    A sixth channel would fold to grey rather than inventing a hue.
    """
    slots = CATEGORICAL[mode]
    return {
        channel: slots[i] if i < len(slots) else CHROME[mode]["muted"]
        for i, channel in enumerate(channel_order)
    }


def funnel_colours(stage_count: int = 5, mode: Mode = "light") -> list[str]:
    ramp = FUNNEL_RAMP[mode]
    if stage_count <= len(ramp):
        return ramp[:stage_count]
    return ramp + [ramp[-1]] * (stage_count - len(ramp))  # pragma: no cover


def apply_layout(fig, mode: Mode = "light", *, height: int = 360, showlegend: bool = True):
    """Apply the shared chart chrome: recessive grid, muted axes, no chart junk."""
    chrome = CHROME[mode]
    fig.update_layout(
        font=dict(family=FONT_STACK, size=13, color=chrome["text_secondary"]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=32, b=8),
        height=height,
        showlegend=showlegend,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(color=chrome["text_secondary"]),
        ),
        hoverlabel=dict(font=dict(family=FONT_STACK, size=12)),
        # Explicitly empty: setting only a title font makes Plotly render the
        # literal string "undefined" above the plot. Section headings are
        # Streamlit subheaders, so charts carry no title of their own.
        title=dict(text="", font=dict(color=chrome["text_primary"], size=15)),
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor=chrome["axis"],
        tickfont=dict(color=chrome["muted"], size=12),
        title_font=dict(color=chrome["muted"], size=12),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=chrome["grid"],
        gridwidth=1,
        zeroline=False,
        linecolor="rgba(0,0,0,0)",
        tickfont=dict(color=chrome["muted"], size=12),
        title_font=dict(color=chrome["muted"], size=12),
    )
    return fig


def detect_mode() -> Mode:
    """Follow the Streamlit theme the user is actually running."""
    try:
        import streamlit as st

        base = st.get_option("theme.base")
        return "dark" if base == "dark" else "light"
    except Exception:  # pragma: no cover - defensive, outside a Streamlit run
        return "light"
