"""Delivery over the Telegram Bot API.

Credentials come from the environment, never from the config files:

    TELEGRAM_BOT_TOKEN   the token @BotFather issued
    TELEGRAM_CHAT_ID     one chat id, or several separated by commas
"""

from __future__ import annotations

import logging
import os
import time
from typing import Sequence

import requests

from ..config import Config
from . import DeliveryResult

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"

#: Telegram tolerates roughly one message per second to a given chat. The digest
#: is at most a handful of messages, so a plain pause is enough.
_PAUSE_SECONDS = 1.1
_MAX_ATTEMPTS = 4


class TelegramError(RuntimeError):
    pass


def token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def chat_ids() -> list[str]:
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def configured() -> bool:
    return bool(token() and chat_ids())


def missing_settings() -> list[str]:
    missing = []
    if not token():
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_ids():
        missing.append("TELEGRAM_CHAT_ID")
    return missing


def _call(method: str, payload: dict, timeout: float) -> dict:
    """One Bot API call, honouring 429 retry_after and retrying 5xx."""
    url = f"{API_ROOT}/bot{token()}/{method}"
    last_error = "unknown error"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 429:
                wait = 3.0
                try:
                    wait = float(response.json().get("parameters", {}).get("retry_after", 3))
                except ValueError:
                    pass
                log.warning("Telegram rate limit; waiting %.0fs", wait)
                time.sleep(min(wait, 30.0) + 0.5)
                last_error = "rate limited"
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.ok and body.get("ok"):
                return body.get("result", {})
            description = body.get("description") or f"HTTP {response.status_code}"
            # 4xx here means a bad token, a wrong chat id or malformed HTML —
            # none of which a retry will fix.
            if 400 <= response.status_code < 500:
                raise TelegramError(f"{method} rejected: {description}")
            last_error = description
        if attempt < _MAX_ATTEMPTS:
            time.sleep(2.0 * attempt)

    raise TelegramError(f"{method} failed after {_MAX_ATTEMPTS} attempts: {last_error}")


def verify(timeout: float = 20.0) -> str:
    """Check the token and every chat id. Returns a human-readable summary."""
    if not configured():
        raise TelegramError("missing " + ", ".join(missing_settings()))
    me = _call("getMe", {}, timeout)
    names = []
    for chat in chat_ids():
        info = _call("getChat", {"chat_id": chat}, timeout)
        label = info.get("title") or info.get("username") or info.get("first_name") or chat
        names.append(f"{label} ({chat})")
    return f"bot @{me.get('username', '?')} → " + ", ".join(names)


#: Update kinds that carry a chat. `message` covers the normal case — you
#: messaging the bot — and the rest cover a group, a channel, or the bot being
#: added somewhere, which are the other ways a chat id comes into existence.
_CHAT_BEARING = (
    "message", "edited_message", "channel_post", "edited_channel_post",
    "my_chat_member", "chat_member",
)


def recent_chats(timeout: float = 20.0) -> list[dict]:
    """Every chat that has spoken to this bot lately, newest first.

    This is how you find TELEGRAM_CHAT_ID: a bot cannot message you first, so
    you message it, and its inbox is then the only place the id exists. Needs
    the token alone — the chat id is what it is for.

    Telegram keeps unread updates for 24 hours and returns nothing at all while
    a webhook is set, so an empty list means "say something to the bot", not
    "the token is wrong".
    """
    if not token():
        raise TelegramError("missing TELEGRAM_BOT_TOKEN")

    seen: dict[str, dict] = {}
    for update in _call("getUpdates", {"limit": 100, "timeout": 0}, timeout):
        for kind in _CHAT_BEARING:
            payload = update.get(kind)
            if not isinstance(payload, dict):
                continue
            chat = payload.get("chat")
            if not isinstance(chat, dict) or chat.get("id") is None:
                continue
            chat_id = str(chat["id"])
            label = (
                chat.get("title")
                or " ".join(
                    part for part in (chat.get("first_name"), chat.get("last_name")) if part
                )
                or chat.get("username")
                or "—"
            )
            # Updates arrive oldest first, so a later one legitimately wins.
            seen[chat_id] = {
                "id": chat_id,
                "type": chat.get("type", "?"),
                "label": label,
                "username": chat.get("username", ""),
                "date": payload.get("date"),
            }
    return sorted(seen.values(), key=lambda c: c["date"] or 0, reverse=True)


def send(messages: Sequence[str], config: Config, timeout: float = 30.0) -> DeliveryResult:
    """Send the rendered messages to every configured chat."""
    if not configured():
        return DeliveryResult(
            "telegram", False, "not configured: " + ", ".join(missing_settings())
        )
    if not messages:
        return DeliveryResult("telegram", True, "nothing to send")

    chats = chat_ids()
    sent = 0
    try:
        for chat in chats:
            for index, text in enumerate(messages):
                _call(
                    "sendMessage",
                    {
                        "chat_id": chat,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": config.telegram.disable_web_page_preview,
                    },
                    timeout,
                )
                sent += 1
                if index < len(messages) - 1 or chat != chats[-1]:
                    time.sleep(_PAUSE_SECONDS)
    except TelegramError as exc:
        return DeliveryResult("telegram", False, str(exc), recipients=len(chats), messages=sent)

    return DeliveryResult(
        "telegram",
        True,
        f"{sent} message(s) to {len(chats)} chat(s)",
        recipients=len(chats),
        messages=sent,
    )
