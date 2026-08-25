"""The command line: the surface a scheduler actually calls."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bot import cli
from bot.channels import DeliveryResult
from bot.envfile import load_env_file


@pytest.fixture
def wired(monkeypatch, recent_fetch, fixture_sources, config, tmp_path):
    """Point the CLI at fixture feeds, a temporary seen-store, and fake channels.

    Both channels are forced on. The shipped settings.yaml sends by e-mail only,
    but the fan-out, --only and partial-failure machinery is still live code and
    is what these tests are about; `test_the_shipped_config_is_email_only` below
    is what pins the default itself.
    """
    state_path = tmp_path / "seen.json"
    patched = replace(
        config,
        sources=fixture_sources,
        state=replace(config.state, path=state_path),
        telegram=replace(config.telegram, enabled=True),
        email=replace(config.email, enabled=True),
    )
    monkeypatch.setattr(cli, "load_config", lambda directory=None: patched)
    # Both the pipeline and `check-sources` reach the network through their own
    # imported reference; patch each so no test can touch a real feed.
    monkeypatch.setattr("bot.digest.fetch_all", recent_fetch)
    monkeypatch.setattr(cli, "fetch_all", recent_fetch)
    return patched


@pytest.fixture
def channels(monkeypatch):
    """Records what each channel was asked to send."""
    from bot.channels import email_smtp, telegram

    sent = {"telegram": [], "email": []}

    def fake_telegram(messages, config, **kwargs):
        sent["telegram"].append(list(messages))
        return DeliveryResult("telegram", True, "fake")

    def fake_email(subject, text_body, html_body, config, **kwargs):
        sent["email"].append(subject)
        return DeliveryResult("email", True, "fake")

    monkeypatch.setattr(telegram, "send", fake_telegram)
    monkeypatch.setattr(email_smtp, "send", fake_email)
    return sent


def test_dry_run_writes_files_and_sends_nothing(wired, channels, tmp_path):
    out = tmp_path / "out"
    code = cli.main(["run", "--dry-run", "--out", str(out)])
    assert code == cli.EXIT_OK
    written = sorted(p.name.split("-")[0] for p in out.iterdir())
    assert written == ["digest", "digest", "telegram"]
    assert channels == {"telegram": [], "email": []}


def test_run_delivers_to_both_channels_and_records_what_was_sent(wired, channels):
    assert cli.main(["run"]) == cli.EXIT_OK
    assert len(channels["telegram"]) == 1
    assert len(channels["email"]) == 1
    stored = json.loads(wired.state.path.read_text())
    assert stored["entries"]


def test_only_restricts_delivery_to_one_channel(wired, channels):
    assert cli.main(["run", "--only", "telegram"]) == cli.EXIT_OK
    assert channels["telegram"] and not channels["email"]


def test_a_failed_delivery_is_reported_and_the_news_is_not_marked_as_sent(
    wired, monkeypatch
):
    """Otherwise a bad morning would silently swallow that day's stories."""
    from bot.channels import email_smtp, telegram

    monkeypatch.setattr(
        telegram, "send", lambda *a, **k: DeliveryResult("telegram", False, "boom")
    )
    monkeypatch.setattr(
        email_smtp, "send", lambda *a, **k: DeliveryResult("email", False, "boom")
    )
    assert cli.main(["run"]) == cli.EXIT_DELIVERY_FAILED
    assert not wired.state.path.exists()


def test_a_partial_delivery_still_records_the_news(wired, monkeypatch):
    from bot.channels import email_smtp, telegram

    monkeypatch.setattr(telegram, "send", lambda *a, **k: DeliveryResult("telegram", True))
    monkeypatch.setattr(
        email_smtp, "send", lambda *a, **k: DeliveryResult("email", False, "smtp down")
    )
    assert cli.main(["run"]) == cli.EXIT_DELIVERY_FAILED
    assert wired.state.path.exists()


def test_the_second_run_of_a_day_is_quiet(wired, channels):
    cli.main(["run"])
    channels["telegram"].clear()
    cli.main(["run"])
    # The brief still goes out (send_when_empty), but with nothing repeated.
    assert "No BFSI or life insurance stories" in channels["telegram"][0][0]


def test_no_state_replays_everything(wired, channels):
    cli.main(["run"])
    channels["telegram"].clear()
    cli.main(["run", "--no-state"])
    assert "IRDAI" in channels["telegram"][0][0]


def test_preview_prints_the_brief_without_sending(wired, channels, capsys):
    assert cli.main(["preview"]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "BFSI & Life Insurance Brief" in printed
    assert channels == {"telegram": [], "email": []}


def test_preview_explains_what_it_passed_over(wired, capsys):
    cli.main(["preview", "--explain"])
    printed = capsys.readouterr().out
    assert "Passed over:" in printed
    assert "excluded on" in printed or "outside the BFSI domain" in printed


def test_preview_never_touches_the_seen_store(wired):
    cli.main(["preview"])
    assert not wired.state.path.exists()


def test_check_sources_reports_health(wired, capsys):
    code = cli.main(["check-sources"])
    printed = capsys.readouterr().out
    assert "feeds healthy" in printed
    assert code == cli.EXIT_OK


def test_a_bad_config_exits_with_its_own_code(monkeypatch, tmp_path):
    assert cli.main(["--config", str(tmp_path / "nowhere"), "preview"]) == cli.EXIT_CONFIG_ERROR


def test_env_file_loads_values_without_overriding_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# a comment\nexport TELEGRAM_BOT_TOKEN="from-file"\nEMAIL_TO=me@example.com\n\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-environment")
    monkeypatch.delenv("EMAIL_TO", raising=False)

    load_env_file(env)

    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "from-environment"
    assert os.environ["EMAIL_TO"] == "me@example.com"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "absent") == 0


# ------------------------------------------------------- telegram-chats setup

def _update(chat_id, kind="message", chat_type="private", date=100, **chat):
    return {kind: {"date": date, "chat": {"id": chat_id, "type": chat_type, **chat}}}


@pytest.fixture
def telegram_api(monkeypatch, wired):
    """Stand in for the Bot API, recording which methods were called."""
    from bot.channels import telegram

    calls, updates = [], []

    def fake_call(method, payload, timeout):
        calls.append(method)
        if method == "getMe":
            return {"username": "Edubfsi_bot", "first_name": "BFSI Brief"}
        if method == "getUpdates":
            return list(updates)
        raise AssertionError(f"unexpected API call: {method}")

    monkeypatch.setattr(telegram, "_call", fake_call)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    return type("API", (), {"calls": calls, "updates": updates})()


def test_telegram_chats_reports_the_id_and_who_it_belongs_to(telegram_api, capsys):
    telegram_api.updates.append(_update(4242, first_name="Bishal", username="bishal"))

    assert cli.main(["telegram-chats"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "4242" in out and "Bishal" in out and "@bishal" in out
    assert "Set TELEGRAM_CHAT_ID to 4242" in out


def test_the_chat_id_lookup_needs_only_the_token(telegram_api, monkeypatch, capsys):
    """TELEGRAM_CHAT_ID is the thing being looked up, so requiring it would make
    the command useless exactly when it is needed."""
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    telegram_api.updates.append(_update(7))

    assert cli.main(["telegram-chats"]) == cli.EXIT_OK
    assert "getUpdates" in telegram_api.calls


def test_an_empty_inbox_says_to_message_the_bot_first(telegram_api, capsys):
    """A bot cannot open a conversation, so 'no chats' is the normal first
    result, not a failure to explain away."""
    assert cli.main(["telegram-chats"]) == cli.EXIT_DELIVERY_FAILED
    out = capsys.readouterr().out
    assert "https://t.me/Edubfsi_bot" in out
    assert "webhook" in out and "24 hours" in out


def test_a_missing_token_is_caught_before_the_network(monkeypatch, wired, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        "bot.channels.telegram._call",
        lambda *a, **k: pytest.fail("should not have called the API"),
    )
    assert cli.main(["telegram-chats"]) == cli.EXIT_DELIVERY_FAILED
    assert "BotFather" in capsys.readouterr().out


def test_group_and_channel_chats_are_found_too(telegram_api, capsys):
    """The bot may be added to a group instead — same id, different update."""
    telegram_api.updates.extend([
        _update(-100123, kind="channel_post", chat_type="channel", title="BFSI Desk", date=50),
        _update(-200456, kind="my_chat_member", chat_type="group", title="Team", date=60),
    ])
    assert cli.main(["telegram-chats"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "-100123" in out and "BFSI Desk" in out
    assert "-200456" in out and "Team" in out


def test_the_newest_chat_is_recommended_and_repeats_collapse(telegram_api, capsys):
    telegram_api.updates.extend([
        _update(111, date=10, first_name="Old"),
        _update(222, date=90, first_name="Newest"),
        _update(111, date=20, first_name="Old"),
    ])
    assert cli.main(["telegram-chats"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Set TELEGRAM_CHAT_ID to 222" in out
    assert out.count("111") == 1
    assert "separate them with commas" in out


# --------------------------------------------------------- email-only delivery

def test_the_shipped_config_is_email_only(config):
    """The brief goes to Gmail. Telegram is off by choice, not by accident —
    if someone flips it back on, that should be a deliberate edit."""
    assert config.email.enabled
    assert not config.telegram.enabled


def test_a_disabled_channel_is_not_a_delivery_failure(monkeypatch, wired, capsys):
    """`test-delivery` has to come back clean on an e-mail-only setup, or its
    verdict is worthless exactly where it matters most."""
    from bot.channels import email_smtp, telegram

    patched = replace(
        wired,
        telegram=replace(wired.telegram, enabled=False),
        email=replace(wired.email, enabled=True),
    )
    monkeypatch.setattr(cli, "load_config", lambda directory=None: patched)
    monkeypatch.setattr(email_smtp, "configured", lambda: True)
    monkeypatch.setattr(email_smtp, "verify", lambda config: "smtp ok")
    monkeypatch.setattr(
        telegram, "configured", lambda: pytest.fail("a disabled channel was probed")
    )

    assert cli.main(["test-delivery"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "off in settings.yaml" in out
    assert "1 channel(s) ready" in out


def test_an_unconfigured_enabled_channel_still_fails(monkeypatch, wired, capsys):
    """The flag suppresses the check; it must not suppress a real problem."""
    from bot.channels import email_smtp

    patched = replace(wired, telegram=replace(wired.telegram, enabled=False))
    monkeypatch.setattr(cli, "load_config", lambda directory=None: patched)
    monkeypatch.setattr(email_smtp, "configured", lambda: False)
    monkeypatch.setattr(email_smtp, "missing_settings", lambda: ["GMAIL_ADDRESS"])

    assert cli.main(["test-delivery"]) == cli.EXIT_DELIVERY_FAILED
    assert "GMAIL_ADDRESS" in capsys.readouterr().out


def test_every_channel_off_is_reported_as_a_problem(monkeypatch, wired, capsys):
    """Silence is the one outcome the bot exists to prevent."""
    patched = replace(
        wired,
        telegram=replace(wired.telegram, enabled=False),
        email=replace(wired.email, enabled=False),
    )
    monkeypatch.setattr(cli, "load_config", lambda directory=None: patched)

    assert cli.main(["test-delivery"]) == cli.EXIT_DELIVERY_FAILED
    assert "nowhere to go" in capsys.readouterr().out


def test_run_sends_only_by_email_under_the_shipped_config(monkeypatch, wired, channels):
    """End to end on the real default: the digest reaches e-mail and Telegram is
    never contacted, even though its credentials would work."""
    patched = replace(wired, telegram=replace(wired.telegram, enabled=False))
    monkeypatch.setattr(cli, "load_config", lambda directory=None: patched)

    assert cli.main(["run"]) == cli.EXIT_OK
    assert channels["email"]
    assert channels["telegram"] == []


def test_email_to_is_not_reported_missing_when_it_merely_defaults(monkeypatch):
    """EMAIL_TO falls back to the sender, so naming it alongside GMAIL_ADDRESS
    turns a two-secret setup into an apparently three-secret one."""
    from bot.channels import email_smtp

    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    assert email_smtp.missing_settings() == ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"]

    monkeypatch.setenv("GMAIL_ADDRESS", "someone@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    assert email_smtp.missing_settings() == []
    assert email_smtp.recipients() == ["someone@example.test"]


def test_a_malformed_email_to_is_still_caught(monkeypatch):
    from bot.channels import email_smtp

    monkeypatch.setenv("GMAIL_ADDRESS", "someone@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.setenv("EMAIL_TO", " , ; ")
    assert email_smtp.missing_settings() == ["EMAIL_TO"]
