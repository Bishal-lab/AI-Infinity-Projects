"""Turning a `Digest` into the three things that actually get sent:
a Telegram message (or several), an HTML email, and its plain-text twin.

Both renderers work from the same digest and say the same things, so the two
channels never disagree about what the morning's news was.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable, Sequence

from .config import Config
from .digest import Digest, DigestSection
from .model import Article
from .normalize import truncate

BRAND = "BFSI & Life Insurance Brief"

# Email palette. Deliberately conservative: Gmail strips <style> blocks in some
# contexts, so every colour is inlined and nothing depends on CSS the client
# might drop.
_INK = "#16202a"
_MUTED = "#5b6b7a"
_ACCENT = "#0b5fa5"
_RULE = "#e3e8ee"
_CANVAS = "#f4f6f8"


# Semantic, and deliberately not the accent: a price moving is a different kind
# of fact from a section heading, and must not read as one.
_UP = "#127a3d"
_DOWN = "#b3261e"


def _pct(value: float | None) -> str:
    """A signed percentage, or an em dash when there is nothing to show."""
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _price(quote) -> str:
    symbol = {"INR": "₹"}.get(quote.currency, "")
    if symbol:
        return f"{symbol}{quote.price:,.2f}"
    return f"{quote.price:,.2f}{(' ' + quote.currency) if quote.currency else ''}"


def _quote_line(quote) -> str:
    """"Max Financial Services  ₹1,234.50  +1.20%  (1mo +4.10%)", as plain text."""
    if not quote.ok:
        return f"{quote.entry.name} — price unavailable ({quote.error})"
    parts = [quote.entry.name, _price(quote), _pct(quote.day_change_pct)]
    month = quote.month_change_pct
    if month is not None:
        parts.append(f"(1mo {_pct(month)})")
    return "  ".join(parts)


def _esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def _time(moment: datetime | None, zone) -> str:
    if moment is None:
        return ""
    return moment.astimezone(zone).strftime("%H:%M")


def _long_date(moment: datetime) -> str:
    # %-d is not portable, so trim the zero by hand.
    return moment.strftime("%A, %d %B %Y").replace(" 0", " ")


def _short_date(moment: datetime) -> str:
    return moment.strftime("%d %b %Y").lstrip("0")


def _meta(article: Article, zone) -> str:
    """The grey line under a headline: who, when, how well corroborated."""
    bits = [article.attribution]
    stamp = _time(article.published, zone)
    if stamp:
        bits.append(stamp)
    if article.duplicate_count:
        bits.append(f"+{article.duplicate_count} more")
    return " · ".join(bit for bit in bits if bit)


def subject_line(digest: Digest, config: Config) -> str:
    """Fill the configured subject template, tolerating unknown placeholders."""
    lead = digest.sections[0] if digest.sections else None
    values = {
        "date": _short_date(digest.generated_at),
        "long_date": _long_date(digest.generated_at),
        "count": digest.total_items,
        "top_section": lead.title if lead else "No updates",
        "top_headline": lead.articles[0].title if lead and lead.articles else "",
    }
    try:
        return config.email.subject_template.format(**values)
    except (KeyError, IndexError, ValueError):
        return f"{BRAND} — {values['date']}"


def _empty_note(digest: Digest) -> str:
    hours = int((digest.window_end - digest.window_start).total_seconds() // 3600)
    if digest.sources_total and digest.sources_ok == 0:
        # A quiet news day and a broken bot look identical from the inbox, so
        # say which one this is.
        return (
            f"None of the {digest.sources_total} news sources could be reached, so there "
            "is nothing to report. This is a delivery problem rather than a quiet news "
            "day — run 'python -m bot check-sources' to see which feeds are failing."
        )
    return (
        "No BFSI or life insurance stories cleared the relevance threshold in the "
        f"last {hours} hours."
    )


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #

def render_telegram(digest: Digest, config: Config) -> list[str]:
    """Render to one or more Telegram HTML messages under the size cap.

    Splitting happens on item boundaries, and a section that spans a split
    repeats its heading, so no message ever starts mid-thought.
    """
    zone = digest.generated_at.tzinfo
    settings = config.telegram
    limit = settings.max_message_chars

    header = (
        f"🗞 <b>{_esc(BRAND)}</b>\n"
        f"<i>{_esc(_long_date(digest.generated_at))} · "
        f"{_esc(digest.generated_at.strftime('%H:%M %Z').strip())}</i>"
    )

    # (kind, text) — headings are repeated when a section spans two messages.
    blocks: list[tuple[str, str]] = []
    if digest.is_empty:
        blocks.append(("item", _esc(_empty_note(digest))))
    else:
        if digest.quotes:
            blocks.append(("heading", "📈 <b>WATCHLIST</b>"))
            for quote in digest.quotes:
                blocks.append(("item", _telegram_quote(quote)))
        for section in digest.sections:
            blocks.append(
                ("heading", f"{section.emoji} <b>{_esc(section.title.upper())}</b>".strip())
            )
            for index, article in enumerate(section.articles, start=1):
                blocks.append(("item", _telegram_item(index, article, zone, settings)))

    messages: list[str] = []
    current = header
    heading: str | None = None

    for kind, text in blocks:
        if kind == "heading":
            heading = text
        candidate = f"{current}\n\n{text}" if current else text
        if len(candidate) <= limit:
            current = candidate
            continue
        messages.append(current)
        if kind == "item" and heading:
            current = f"{heading}\n\n{text}"
        else:
            current = text
    if current:
        messages.append(current)

    # A message that is nothing but a section heading means the split landed
    # badly; drop the stub rather than send a header with no news under it.
    headings = {text for kind, text in blocks if kind == "heading"}
    if len(messages) > 1 and messages[-1] in headings:
        messages.pop()

    footer = _esc(_footer_text(digest))
    if footer:
        tail = f"\n\n<i>{footer}</i>"
        if len(messages[-1]) + len(tail) <= limit:
            messages[-1] += tail
        else:
            messages.append(f"<i>{footer}</i>")
    return messages


def _telegram_quote(quote) -> str:
    if not quote.ok:
        return f"{_esc(quote.entry.name)} — <i>price unavailable</i>"
    arrow = {"up": "▲", "down": "▼"}.get(quote.direction, "·")
    line = (
        f"{arrow} <b>{_esc(quote.entry.name)}</b> {_esc(_price(quote))} "
        f"{_esc(_pct(quote.day_change_pct))}"
    )
    month = quote.month_change_pct
    if month is not None:
        line += f" <i>(1mo {_esc(_pct(month))})</i>"
    for article in quote.stories[:2]:
        line += (
            f'\n   ↳ <a href="{_esc(article.url)}">'
            f"{_esc(truncate(article.title, 70))}</a>"
        )
    return line


def _telegram_item(index: int, article: Article, zone, settings) -> str:
    line = f"{index}. <a href=\"{_esc(article.url)}\">{_esc(article.title)}</a>"
    meta = _meta(article, zone)
    if meta:
        line += f"\n<i>{_esc(meta)}</i>"
    if settings.include_summaries and article.summary:
        line += f"\n{_esc(truncate(article.summary, settings.summary_chars))}"
    return line


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #

def render_email_html(digest: Digest, config: Config) -> str:
    zone = digest.generated_at.tzinfo
    limit = config.email.summary_chars

    parts: list[str] = [
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(BRAND)}</title></head>"
        f'<body style="margin:0;padding:0;background:{_CANVAS};">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{_esc(_preheader(digest))}</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_CANVAS};padding:24px 12px;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:680px;background:#ffffff;border:1px solid {_RULE};'
        f'border-radius:10px;overflow:hidden;">'
        # Masthead
        f'<tr><td style="padding:22px 26px 16px;border-bottom:3px solid {_ACCENT};">'
        f'<div style="font:600 20px/1.3 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f"Roboto,Helvetica,Arial,sans-serif;color:{_INK};\">{_esc(BRAND)}</div>"
        f'<div style="font:400 13px/1.5 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f"Roboto,Helvetica,Arial,sans-serif;color:{_MUTED};padding-top:4px;\">"
        f"{_esc(_long_date(digest.generated_at))} · "
        f"{_esc(digest.generated_at.strftime('%H:%M %Z').strip())} · "
        f"{digest.total_items} stor{'y' if digest.total_items == 1 else 'ies'}</div>"
        f"</td></tr>"
    ]

    if digest.is_empty:
        parts.append(
            f'<tr><td style="padding:28px 26px;font:400 15px/1.6 -apple-system,'
            f"BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
            f'color:{_MUTED};">{_esc(_empty_note(digest))}</td></tr>'
        )
    else:
        if digest.total_items > 3:
            parts.append(_html_contents(digest))
        parts.append(_html_watchlist(digest))
        for section in digest.sections:
            parts.append(_html_section(section, zone, limit))

    parts.append(
        f'<tr><td style="padding:16px 26px 22px;border-top:1px solid {_RULE};'
        f"font:400 12px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        f'Helvetica,Arial,sans-serif;color:{_MUTED};">{_esc(_footer_text(digest))}</td></tr>'
        f"</table></td></tr></table></body></html>"
    )
    return "".join(parts)


def _html_watchlist(digest: Digest) -> str:
    """The price strip, above the news.

    Each row carries the day move and, underneath, the headlines from this very
    brief that name the company — which is the only reason a price belongs in a
    news e-mail at all.
    """
    quotes = [q for q in digest.quotes if q.ok or q.error]
    if not quotes:
        return ""

    font = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
            "Helvetica,Arial,sans-serif")
    rows = [
        f'<tr><td style="padding:18px 26px 4px;">'
        f'<div style="font:600 12px/1.4 {font};letter-spacing:.09em;'
        f'text-transform:uppercase;color:{_ACCENT};">📈 Watchlist</div></td></tr>'
    ]

    for quote in quotes:
        if not quote.ok:
            rows.append(
                f'<tr><td style="padding:8px 26px 12px;border-bottom:1px solid {_RULE};'
                f'font:400 13px/1.5 {font};color:{_MUTED};">'
                f"{_esc(quote.entry.name)} — price unavailable</td></tr>"
            )
            continue

        colour = {"up": _UP, "down": _DOWN}.get(quote.direction, _MUTED)
        month = quote.month_change_pct
        trail = (
            f'<span style="color:{_MUTED};"> &nbsp;1mo {_esc(_pct(month))}</span>'
            if month is not None else ""
        )
        stories = "".join(
            f'<div style="font:400 13px/1.5 {font};color:{_MUTED};padding-top:3px;">'
            f'↳ <a href="{_esc(article.url)}" style="color:{_MUTED};">'
            f"{_esc(truncate(article.title, 88))}</a></div>"
            for article in quote.stories[:3]
        )
        rows.append(
            f'<tr><td style="padding:8px 26px 12px;border-bottom:1px solid {_RULE};">'
            f'<span style="font:600 15px/1.5 {font};color:{_INK};">'
            f"{_esc(quote.entry.name)}</span>"
            f'<span style="font:400 15px/1.5 {font};color:{_INK};"> &nbsp;'
            f"{_esc(_price(quote))}</span>"
            f'<span style="font:600 15px/1.5 {font};color:{colour};"> &nbsp;'
            f"{_esc(_pct(quote.day_change_pct))}</span>{trail}"
            f"{stories}</td></tr>"
        )
    return "".join(rows)


def _preheader(digest: Digest) -> str:
    """The grey line Gmail shows next to the subject in the inbox list."""
    if digest.is_empty:
        return "No qualifying BFSI updates today."
    lead = digest.sections[0].articles[0].title
    return truncate(lead, 110)


def _html_contents(digest: Digest) -> str:
    links = " &nbsp;·&nbsp; ".join(
        f'<a href="#sec-{_esc(section.section.id)}" style="color:{_ACCENT};'
        f'text-decoration:none;">{_esc(section.title)} ({len(section.articles)})</a>'
        for section in digest.sections
    )
    return (
        f'<tr><td style="padding:14px 26px;background:#fbfcfd;border-bottom:1px solid {_RULE};'
        f"font:400 13px/1.9 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        f'Helvetica,Arial,sans-serif;color:{_MUTED};">{links}</td></tr>'
    )


def _html_section(section: DigestSection, zone, limit: int) -> str:
    rows = [
        f'<tr><td id="sec-{_esc(section.section.id)}" style="padding:22px 26px 6px;">'
        f'<div style="font:600 12px/1.4 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f"Roboto,Helvetica,Arial,sans-serif;letter-spacing:.09em;text-transform:uppercase;"
        f'color:{_ACCENT};">{_esc(section.emoji)} {_esc(section.title)}</div></td></tr>'
    ]
    for article in section.articles:
        rows.append(_html_article(article, zone, limit))
    return "".join(rows)


def _html_article(article: Article, zone, limit: int) -> str:
    summary = truncate(article.summary, limit) if article.summary else ""
    body = (
        f'<div style="font:400 14px/1.6 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f"Roboto,Helvetica,Arial,sans-serif;color:{_INK};padding-top:5px;\">{_esc(summary)}</div>"
        if summary
        else ""
    )
    return (
        f'<tr><td style="padding:10px 26px 14px;border-bottom:1px solid {_RULE};">'
        f'<a href="{_esc(article.url)}" style="font:600 16px/1.45 -apple-system,'
        f"BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        f'color:{_INK};text-decoration:none;">{_esc(article.title)}</a>'
        f'<div style="font:400 12px/1.5 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f"Roboto,Helvetica,Arial,sans-serif;color:{_MUTED};padding-top:4px;\">"
        f"{_esc(_meta(article, zone))}</div>{body}</td></tr>"
    )


def render_email_text(digest: Digest, config: Config) -> str:
    """Plain-text alternative — the part that survives every mail client."""
    zone = digest.generated_at.tzinfo
    lines = [
        BRAND,
        f"{_long_date(digest.generated_at)} · "
        f"{digest.generated_at.strftime('%H:%M %Z').strip()}",
        "=" * 60,
        "",
    ]
    if digest.is_empty:
        lines.append(_empty_note(digest))
    else:
        if digest.quotes:
            lines.append("WATCHLIST")
            lines.append("-" * 9)
            for quote in digest.quotes:
                lines.append(_quote_line(quote))
                for article in quote.stories[:3]:
                    lines.append(f"   -> {truncate(article.title, 88)}")
            lines.append("")
            lines.append("")
        for section in digest.sections:
            lines.append(f"{section.title.upper()}")
            lines.append("-" * len(section.title))
            for index, article in enumerate(section.articles, start=1):
                lines.append(f"{index}. {article.title}")
                meta = _meta(article, zone)
                if meta:
                    lines.append(f"   {meta}")
                if article.summary:
                    lines.append(f"   {truncate(article.summary, config.email.summary_chars)}")
                lines.append(f"   {article.url}")
                lines.append("")
            lines.append("")
    lines.append("-" * 60)
    lines.append(_footer_text(digest))
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------- #

def _footer_text(digest: Digest) -> str:
    window = (
        f"{digest.window_start.strftime('%d %b %H:%M')} – "
        f"{digest.window_end.strftime('%d %b %H:%M')}"
    )
    parts = [
        f"Window {window} ({digest.window_end.strftime('%Z').strip() or 'local'})",
        f"{digest.sources_ok}/{digest.sources_total} sources responded",
    ]
    if digest.failures:
        names = ", ".join(name for name, _ in digest.failures[:4])
        if len(digest.failures) > 4:
            names += f" and {len(digest.failures) - 4} more"
        parts.append(f"unavailable: {names}")
    return " · ".join(parts)
