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
    """Point the CLI at fixture feeds, a temporary seen-store, and fake channels."""
    state_path = tmp_path / "seen.json"
    patched = replace(
        config,
        sources=fixture_sources,
        state=replace(config.state, path=state_path),
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
