"""Turning a digest into the three things that get delivered: an HTML e-mail,
its plain-text alternative, and the Markdown copy the scheduled Claude session
reads to post the same brief into chat.

House rules for all three: every opening states its fit score and the reasons
behind it, the apply link is never more than one click away, and the footer
always says what was searched and what could not be reached. A brief that hides
a broken source is worse than no brief.
"""

from __future__ import annotations

import html
from datetime import datetime

from .config import Config
from .digest import Digest, DigestTier
from .links import all_searches
from .model import Opening
from .normalize import truncate

_TIER_COLOURS = {
    "strong": "#0b7a3b",
    "possible": "#8a6100",
    "stretch": "#5a5f6a",
}
_TIER_BADGES = {"strong": "STRONG", "possible": "POSSIBLE", "stretch": "STRETCH"}


def _esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def _long_date(moment: datetime) -> str:
    return moment.strftime("%A, %d %B %Y")


def _short_date(moment: datetime) -> str:
    return moment.strftime("%d %b")


def _posted(opening: Opening, zone) -> str:
    if not opening.posted:
        return "date not stated"
    return "posted " + opening.posted.astimezone(zone).strftime("%d %b")


def _meta(opening: Opening, zone) -> str:
    """The one-line context under a heading: where, when, from whom."""
    bits = [bit for bit in (opening.where, _posted(opening, zone), opening.attribution) if bit]
    if opening.duplicate_count:
        bits.append(f"also on {opening.duplicate_count} other board(s)")
    return " · ".join(bits)


def subject_line(digest: Digest, config: Config) -> str:
    count = len(digest.openings)
    date = _short_date(digest.generated_at)
    if count == 0:
        return f"VP Role Radar — nothing new ({date})"
    noun = "new opening" if count == 1 else "new openings"
    strong = sum(1 for opening in digest.openings if opening.tier == "strong")
    subject = config.email.subject_template.format(count=count, noun=noun, date=date)
    if strong:
        subject += f" · {strong} strong"
    return subject


def _empty_note(digest: Digest) -> str:
    """What an empty brief says. It has to distinguish 'nothing matched' from
    'nothing could be reached', because those need different responses."""
    if digest.failures and digest.failure_ratio() >= 0.5:
        return (
            "No openings — but most sources could not be read this morning, so "
            "treat this as a broken run rather than a quiet market. See the "
            "footer, and run check-sources."
        )
    return (
        "No new openings cleared the fit bar since the last run. The saved "
        "searches below are still worth a look — they reach the boards this "
        "radar cannot read on its own."
    )


# --------------------------------------------------------------------------- #
# HTML e-mail
# --------------------------------------------------------------------------- #

def render_email_html(digest: Digest, config: Config) -> str:
    zone = _zone(config)
    parts: list[str] = []
    parts.append(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "</head>"
        '<body style="margin:0;padding:0;background:#f4f5f7;">'
        f'<div style="display:none;max-height:0;overflow:hidden;">{_esc(_preheader(digest))}</div>'
        '<div style="max-width:680px;margin:0 auto;padding:24px 16px;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        'color:#14161a;">'
    )
    parts.append(
        '<h1 style="margin:0 0 4px;font-size:20px;line-height:1.3;">VP Role Radar</h1>'
        f'<p style="margin:0 0 20px;color:#5a5f6a;font-size:13px;">'
        f"{_esc(_long_date(digest.generated_at))} · "
        f'{len(digest.openings)} new</p>'
    )

    if digest.is_empty:
        parts.append(
            '<p style="margin:0 0 20px;padding:14px 16px;background:#fff;border-radius:8px;'
            f'border:1px solid #e3e5e9;font-size:14px;line-height:1.5;">{_esc(_empty_note(digest))}</p>'
        )
    else:
        for tier in digest.tiers:
            parts.append(_html_tier(tier, zone, config.email.summary_chars))

    parts.append(_html_searches(config))
    parts.append(_html_footer(digest, config))
    parts.append("</div></body></html>")
    return "".join(parts)


def _preheader(digest: Digest) -> str:
    if digest.is_empty:
        return "Nothing new cleared the fit bar."
    top = digest.openings[0]
    return f"{top.title} — {top.company} ({top.fit:.0f}/100)"


def _html_tier(tier: DigestTier, zone, summary_chars: int) -> str:
    colour = _TIER_COLOURS.get(tier.id, "#14161a")
    items = "".join(_html_opening(o, zone, summary_chars) for o in tier.openings)
    return (
        f'<h2 style="margin:24px 0 10px;font-size:13px;letter-spacing:.08em;'
        f'text-transform:uppercase;color:{colour};">{_esc(tier.title)} '
        f'<span style="color:#9aa0a6;">({len(tier.openings)})</span></h2>{items}'
    )


def _html_opening(opening: Opening, zone, summary_chars: int) -> str:
    colour = _TIER_COLOURS.get(opening.tier, "#14161a")
    badge = _TIER_BADGES.get(opening.tier, opening.tier.upper())
    summary = truncate(opening.summary, summary_chars) if opening.summary else ""
    reasons = " · ".join(opening.reasons)
    band = opening.experience_band

    return (
        '<div style="background:#ffffff;border:1px solid #e3e5e9;border-radius:8px;'
        'padding:14px 16px;margin:0 0 10px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;color:{colour};'
        f'margin-bottom:6px;">{badge} · {opening.fit:.0f}/100'
        f'{f" · {_esc(band)}" if band else ""}</div>'
        f'<div style="font-size:16px;font-weight:600;line-height:1.35;margin-bottom:2px;">'
        f'<a href="{_esc(opening.url)}" style="color:#14161a;text-decoration:none;">'
        f"{_esc(opening.title)}</a></div>"
        f'<div style="font-size:14px;color:#14161a;margin-bottom:4px;">{_esc(opening.company)}</div>'
        f'<div style="font-size:12px;color:#5a5f6a;margin-bottom:8px;">{_esc(_meta(opening, zone))}</div>'
        + (
            f'<div style="font-size:13px;color:#3c4149;line-height:1.5;margin-bottom:8px;">'
            f"{_esc(summary)}</div>"
            if summary
            else ""
        )
        + (
            f'<div style="font-size:12px;color:#3c4149;background:#f7f8fa;border-radius:6px;'
            f'padding:8px 10px;margin-bottom:10px;"><strong>Why it fits:</strong> {_esc(reasons)}</div>'
            if reasons
            else ""
        )
        + f'<a href="{_esc(opening.url)}" style="display:inline-block;font-size:13px;'
        f'font-weight:600;color:#ffffff;background:#14161a;border-radius:6px;'
        f'padding:8px 14px;text-decoration:none;">View the role</a>'
        "</div>"
    )


def _html_searches(config: Config) -> str:
    searches = all_searches(config)
    if not searches:
        return ""
    items = "".join(
        f'<li style="margin:0 0 6px;"><a href="{_esc(s.url)}" '
        f'style="color:#1a4fd6;text-decoration:none;">{_esc(s.label)}</a></li>'
        for s in searches
    )
    return (
        '<h2 style="margin:26px 0 8px;font-size:13px;letter-spacing:.08em;'
        'text-transform:uppercase;color:#5a5f6a;">Search these yourself</h2>'
        '<p style="margin:0 0 8px;font-size:12px;color:#5a5f6a;line-height:1.5;">'
        "The boards below carry the most senior India and Gulf roles but publish "
        "nothing this radar can read automatically. One click each, no login.</p>"
        f'<ul style="margin:0;padding-left:18px;font-size:13px;">{items}</ul>'
    )


def _html_footer(digest: Digest, config: Config) -> str:
    return (
        '<hr style="border:0;border-top:1px solid #e3e5e9;margin:24px 0 12px;">'
        f'<p style="margin:0;font-size:11px;color:#8b9098;line-height:1.6;">'
        f"{_esc(_footer_text(digest, config))}</p>"
    )


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #

def render_email_text(digest: Digest, config: Config) -> str:
    zone = _zone(config)
    lines: list[str] = [
        "VP ROLE RADAR",
        _long_date(digest.generated_at),
        "",
    ]
    if digest.is_empty:
        lines += [_empty_note(digest), ""]
    else:
        for tier in digest.tiers:
            lines.append(f"{tier.title.upper()} ({len(tier.openings)})")
            lines.append("-" * 60)
            for index, opening in enumerate(tier.openings, start=1):
                band = f" · {opening.experience_band}" if opening.experience_band else ""
                lines.append(f"{index}. {opening.title}")
                lines.append(f"   {opening.company} — {opening.fit:.0f}/100{band}")
                lines.append(f"   {_meta(opening, zone)}")
                if opening.reasons:
                    lines.append(f"   Why it fits: {' · '.join(opening.reasons)}")
                if opening.summary:
                    lines.append(f"   {truncate(opening.summary, config.email.summary_chars)}")
                lines.append(f"   {opening.url}")
                lines.append("")
            lines.append("")

    searches = all_searches(config)
    if searches:
        lines.append("SEARCH THESE YOURSELF")
        lines.append("-" * 60)
        for search in searches:
            lines.append(f"- {search.label}")
            lines.append(f"  {search.url}")
        lines.append("")

    lines.append("-" * 60)
    lines.append(_footer_text(digest, config))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Markdown — the copy the scheduled Claude session reads
# --------------------------------------------------------------------------- #

def render_markdown(digest: Digest, config: Config) -> str:
    """The state-branch copy.

    Written for a reader that is going to summarise it rather than skim it, so
    every field is labelled and the apply URL is bare — see
    docs/claude-routine.md.
    """
    zone = _zone(config)
    looking_for = str(config.profile.candidate.get("looking_for", "")).strip()

    lines = [
        f"# VP Role Radar — {_long_date(digest.generated_at)}",
        "",
        f"**New openings:** {len(digest.openings)}",
    ]
    if looking_for:
        lines.append(f"**Brief:** {looking_for}")
    lines.append("")

    if digest.is_empty:
        lines += [_empty_note(digest), ""]
    else:
        for tier in digest.tiers:
            lines.append(f"## {tier.title} ({len(tier.openings)})")
            lines.append("")
            for opening in tier.openings:
                lines.append(f"### {opening.title} — {opening.company or 'employer not stated'}")
                lines.append("")
                lines.append(f"- **Fit:** {opening.fit:.0f}/100 ({tier.title})")
                lines.append(f"- **Where:** {opening.where or 'not stated'}")
                if opening.experience_band:
                    lines.append(f"- **Experience asked:** {opening.experience_band}")
                lines.append(f"- **Listed:** {_posted(opening, zone)} · {opening.attribution}")
                if opening.reasons:
                    lines.append(f"- **Why it fits:** {' · '.join(opening.reasons)}")
                if opening.summary:
                    lines.append(f"- **Summary:** {truncate(opening.summary, 300)}")
                lines.append(f"- **Apply:** {opening.url}")
                lines.append("")

    searches = all_searches(config)
    if searches:
        lines.append("## Search these yourself")
        lines.append("")
        for search in searches:
            lines.append(f"- [{search.label}]({search.url})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(_footer_text(digest, config))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #

def _zone(config: Config):
    from .digest import local_zone

    return local_zone(config.radar.timezone)


def _footer_text(digest: Digest, config: Config) -> str:
    stats = digest.stats
    read = digest.sources_total - len(digest.failures) - len(digest.skipped)
    parts = [
        f"{stats.postings_fetched} postings read from {read}/{digest.sources_total} sources",
        f"{stats.accepted} matched the profile",
        f"{stats.published} new",
    ]
    text = " · ".join(parts) + "."

    if digest.skipped:
        text += (
            " Switched off for want of an API key: "
            + ", ".join(f"{name} ({why})" for name, why in digest.skipped)
            + "."
        )
    if digest.failures:
        text += (
            " Could not be read: "
            + ", ".join(f"{name} ({why})" for name, why in digest.failures)
            + "."
        )
        if digest.failure_ratio() > config.fetch.degraded_failure_ratio:
            text += " That is most of them — run check-sources."
    return text
