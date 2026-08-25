"""Command line entry point.

    python -m bot run              build the brief and send it
    python -m bot run --dry-run    build it, write it to out/, send nothing
    python -m bot preview          print the brief to the terminal
    python -m bot check-sources    fetch every feed and report on it
    python -m bot test-delivery    verify the Telegram and Gmail credentials
    python -m bot telegram-chats   find your TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, ConfigError, PROJECT_ROOT, load_config
from .digest import Digest, build_digest, local_zone
from .envfile import load_env_file
from .feeds import fetch_all
from .normalize import truncate
from .render import (
    render_email_html,
    render_email_text,
    render_telegram,
    subject_line,
)
from .state import SeenStore

EXIT_OK = 0
EXIT_DELIVERY_FAILED = 1
EXIT_CONFIG_ERROR = 2

log = logging.getLogger("bot")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load(args: argparse.Namespace) -> Config:
    load_env_file(Path(args.env) if args.env else PROJECT_ROOT / ".env")
    return load_config(args.config)


def _summarise(digest: Digest) -> str:
    stats = digest.stats
    return (
        f"{stats.entries_fetched} entries → {stats.in_window} in window → "
        f"{stats.accepted} relevant → {stats.after_dedupe} unique → "
        f"{stats.unseen} new → {stats.published} published"
    )


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args)
    channels = set(args.only) if args.only else {"telegram", "email"}

    store: SeenStore | None = None
    if not args.no_state:
        store = SeenStore(config.state.path, config.state.retention_days).load()
        removed = store.prune()
        if removed:
            log.debug("pruned %d expired seen-store entries", removed)

    digest = build_digest(config, store=store)
    log.info(_summarise(digest))
    if digest.failures:
        for name, error in digest.failures:
            log.warning("unavailable source: %s — %s", name, error)
        if digest.failure_ratio() > config.fetch.degraded_failure_ratio:
            log.error(
                "%d of %d sources failed — the brief may be incomplete",
                len(digest.failures), digest.sources_total,
            )

    if digest.is_empty and not config.digest.send_when_empty:
        log.info("nothing to report and send_when_empty is off; stopping")
        return EXIT_OK

    messages = render_telegram(digest, config)
    subject = subject_line(digest, config)
    html_body = render_email_html(digest, config)
    text_body = render_email_text(digest, config)

    if args.dry_run:
        out = Path(args.out or PROJECT_ROOT / "out")
        out.mkdir(parents=True, exist_ok=True)
        stamp = digest.generated_at.strftime("%Y%m%d-%H%M")
        (out / f"digest-{stamp}.html").write_text(html_body, encoding="utf-8")
        (out / f"digest-{stamp}.txt").write_text(text_body, encoding="utf-8")
        (out / f"telegram-{stamp}.txt").write_text(
            "\n\n----- message break -----\n\n".join(messages), encoding="utf-8"
        )
        print(f"Dry run — nothing sent. Subject would be: {subject}")
        print(f"Wrote 3 files to {out}")
        print(_summarise(digest))
        return EXIT_OK

    results = []
    if "telegram" in channels and config.telegram.enabled:
        from .channels import telegram

        results.append(telegram.send(messages, config))
    if "email" in channels and config.email.enabled:
        from .channels import email_smtp

        results.append(email_smtp.send(subject, text_body, html_body, config))

    for result in results:
        (log.info if result.ok else log.error)("%s", result)

    delivered = any(result.ok for result in results)
    if store is not None and delivered:
        # Mark only after something actually went out, so a total delivery
        # failure does not silently swallow a day's news.
        store.mark(digest.articles)
        store.save()
        log.debug("seen-store now holds %d keys", len(store))

    if not results:
        log.warning("no channels enabled — nothing was sent")
        return EXIT_DELIVERY_FAILED
    return EXIT_OK if all(result.ok for result in results) else EXIT_DELIVERY_FAILED


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #

def cmd_preview(args: argparse.Namespace) -> int:
    config = _load(args)
    digest = build_digest(config, store=None, keep_rejected=args.explain)
    print(render_email_text(digest, config))
    print(_summarise(digest))

    if args.explain:
        print("\nPassed over:")
        for article, verdict in sorted(
            digest.rejected, key=lambda pair: pair[1].score, reverse=True
        )[: args.explain_limit]:
            print(f"  [{verdict.score:5.2f}] {truncate(article.title, 76)}")
            print(f"          {verdict.reason}")

    if args.html:
        target = Path(args.html)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_email_html(digest, config), encoding="utf-8")
        print(f"\nHTML written to {target}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# check-sources
# --------------------------------------------------------------------------- #

def cmd_check_sources(args: argparse.Namespace) -> int:
    config = _load(args)
    sources = config.sources if args.all else config.enabled_sources
    print(f"Checking {len(sources)} feed(s)…\n")

    results = fetch_all(sources, config.fetch)
    now = datetime.now(timezone.utc)
    zone = local_zone(config.digest.timezone)
    failed = 0

    print(f"{'':2} {'source':<32} {'items':>5} {'newest':>18}  detail")
    print("-" * 84)
    for result in results:
        if not result.ok:
            failed += 1
            print(f"{'✗':2} {result.source.id:<32} {'—':>5} {'—':>18}  {result.error}")
            continue
        dates = []
        for entry in result.entries:
            from .normalize import parse_date

            parsed = parse_date(entry.published_raw)
            if parsed:
                dates.append(parsed)
        newest = max(dates) if dates else None
        age = f"{(now - newest).total_seconds() / 3600:.1f}h ago" if newest else "undated"
        stamp = newest.astimezone(zone).strftime("%d %b %H:%M") if newest else "—"
        mark = "✓"
        if newest and (now - newest).total_seconds() > 72 * 3600:
            mark = "!"  # parses fine, but nothing new in three days
        print(
            f"{mark:2} {result.source.id:<32} {len(result.entries):>5} {stamp:>18}  "
            f"{age}, {result.elapsed_seconds:.1f}s"
        )

    print("-" * 84)
    print(f"{len(results) - failed}/{len(results)} feeds healthy")
    if failed:
        print("\nA failing feed is usually a moved URL. Open it in a browser, find the")
        print("publisher's current RSS link, and update config/sources.yaml.")
    return EXIT_OK if failed == 0 else EXIT_DELIVERY_FAILED


# --------------------------------------------------------------------------- #
# test-delivery
# --------------------------------------------------------------------------- #

def cmd_test_delivery(args: argparse.Namespace) -> int:
    config = _load(args)
    from .channels import email_smtp, telegram

    problems = 0
    checked = 0

    print("Telegram")
    if not config.telegram.enabled:
        # A channel switched off on purpose is not a fault. Reporting it as one
        # would mean this command can never come back clean on an e-mail-only
        # setup, which is exactly when you most want to trust its verdict.
        print("  – off in settings.yaml (delivery.telegram.enabled)")
    elif not telegram.configured():
        print("  ✗ not configured: " + ", ".join(telegram.missing_settings()))
        problems += 1
        checked += 1
    else:
        checked += 1
        try:
            print(f"  ✓ {telegram.verify()}")
            if args.send:
                result = telegram.send(
                    ["✅ <b>BFSI digest bot</b>\nTest message — delivery is working."], config
                )
                print(f"  {'✓' if result.ok else '✗'} {result}")
                problems += 0 if result.ok else 1
        except telegram.TelegramError as exc:
            print(f"  ✗ {exc}")
            problems += 1

    print("\nEmail")
    if not config.email.enabled:
        print("  – off in settings.yaml (delivery.email.enabled)")
    elif not email_smtp.configured():
        print("  ✗ not configured: " + ", ".join(email_smtp.missing_settings()))
        problems += 1
        checked += 1
    else:
        checked += 1
        try:
            print(f"  ✓ {email_smtp.verify(config)}")
            if args.send:
                result = email_smtp.send(
                    "BFSI digest bot — test",
                    "Test message. Delivery is working.",
                    "<p><b>Test message.</b> Delivery is working.</p>",
                    config,
                )
                print(f"  {'✓' if result.ok else '✗'} {result}")
                problems += 0 if result.ok else 1
        except email_smtp.EmailError as exc:
            print(f"  ✗ {exc}")
            problems += 1

    if not checked:
        print("\nEvery channel is off — the brief has nowhere to go.")
        return EXIT_DELIVERY_FAILED
    if problems:
        print(f"\n{problems} channel(s) need attention — see README §Setup.")
        return EXIT_DELIVERY_FAILED
    print(f"\n{checked} channel(s) ready.")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# telegram-chats
# --------------------------------------------------------------------------- #

def cmd_telegram_chats(args: argparse.Namespace) -> int:
    """Print the chat ids this bot can see, so you can set TELEGRAM_CHAT_ID.

    Deliberately needs only the token. It is the one setup step that cannot be
    done from a config file — a bot may not open a conversation, so the id does
    not exist until you message the bot, and then only its inbox knows it.
    """
    _load(args)
    from .channels import telegram

    if not telegram.token():
        print("✗ TELEGRAM_BOT_TOKEN is not set.")
        print("  Get a token from @BotFather (/newbot), then set it and try again.")
        return EXIT_DELIVERY_FAILED

    try:
        me = telegram._call("getMe", {}, 20.0)
        chats = telegram.recent_chats()
    except telegram.TelegramError as exc:
        print(f"✗ {exc}")
        return EXIT_DELIVERY_FAILED

    print(f"Bot @{me.get('username', '?')} ({me.get('first_name', '')})\n")

    if not chats:
        print("No chats yet — which is expected until you talk to the bot first.")
        print(f"\n  1. Open https://t.me/{me.get('username', '')}")
        print("  2. Press Start, or send it any message")
        print("  3. Run this again\n")
        print("If you have already done that and still see nothing: Telegram drops")
        print("updates after 24 hours, and returns none at all while a webhook is")
        print("set. Sending a fresh message fixes the first.")
        return EXIT_DELIVERY_FAILED

    print(f"{'chat id':>16}  {'type':<10} who")
    print("-" * 56)
    for chat in chats:
        who = chat["label"]
        if chat["username"]:
            who += f" (@{chat['username']})"
        print(f"{chat['id']:>16}  {chat['type']:<10} {who}")
    print("-" * 56)

    print(f"\nSet TELEGRAM_CHAT_ID to {chats[0]['id']}"
          + (" (several ids: separate them with commas)" if len(chats) > 1 else ""))
    print("Then `test-delivery --send` to confirm a message actually arrives.")
    return EXIT_OK


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="Daily BFSI & life insurance brief for Telegram and e-mail.",
    )
    parser.add_argument("--config", help="config directory (default: ./config)")
    parser.add_argument("--env", help="path to a .env file (default: ./.env)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="build the brief and send it")
    run.add_argument("--dry-run", action="store_true", help="render to out/ instead of sending")
    run.add_argument("--out", help="directory for --dry-run output")
    run.add_argument(
        "--only", action="append", choices=["telegram", "email"],
        help="restrict delivery to one channel (repeatable)",
    )
    run.add_argument(
        "--no-state", action="store_true",
        help="ignore the seen-store: re-send stories already delivered",
    )
    run.set_defaults(func=cmd_run)

    preview = subparsers.add_parser("preview", help="print the brief without sending")
    preview.add_argument("--html", help="also write the HTML email to this path")
    preview.add_argument(
        "--explain", action="store_true", help="list the stories that were passed over, and why"
    )
    preview.add_argument("--explain-limit", type=int, default=25)
    preview.set_defaults(func=cmd_preview)

    check = subparsers.add_parser("check-sources", help="fetch every feed and report on it")
    check.add_argument("--all", action="store_true", help="include disabled sources")
    check.set_defaults(func=cmd_check_sources)

    test = subparsers.add_parser("test-delivery", help="verify Telegram and Gmail credentials")
    test.add_argument("--send", action="store_true", help="also send a real test message")
    test.set_defaults(func=cmd_test_delivery)

    chats = subparsers.add_parser(
        "telegram-chats", help="list the chats this bot can see, to find TELEGRAM_CHAT_ID"
    )
    chats.set_defaults(func=cmd_telegram_chats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except ConfigError as exc:
        log.error("configuration problem — %s", exc)
        return EXIT_CONFIG_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
