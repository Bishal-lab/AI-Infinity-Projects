"""Command line entry point.

    python -m radar run              scan, then send the digest
    python -m radar run --dry-run    scan, write the digest to out/, send nothing
    python -m radar preview          print the digest to the terminal
    python -m radar check-sources    fetch every source and report on it
    python -m radar test-delivery    verify the Gmail credentials
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
from .normalize import truncate
from .render import (
    render_email_html,
    render_email_text,
    render_markdown,
    subject_line,
)
from .sources import fetch_all
from .state import SeenStore

EXIT_OK = 0
EXIT_DELIVERY_FAILED = 1
EXIT_CONFIG_ERROR = 2

log = logging.getLogger("radar")


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
        f"{stats.postings_fetched} postings → {stats.in_window} in window → "
        f"{stats.accepted} match the profile → {stats.after_dedupe} unique → "
        f"{stats.unseen} unseen → {stats.published} published"
    )


def should_send(digest: Digest, config: Config) -> tuple[bool, str]:
    """Whether an empty digest still goes out, and why.

    A job search does not need a daily "nothing today" note. It does need to
    know the radar is alive, which is what the weekly proof-of-life run is for:
    without it, a silently broken scan and a quiet week look identical.
    """
    if not digest.is_empty:
        return True, ""
    if config.radar.send_when_empty:
        return True, "send_when_empty is on"

    day = config.radar.proof_of_life_weekday
    if day:
        zone = local_zone(config.radar.timezone)
        today = digest.generated_at.astimezone(zone).strftime("%A").lower()
        if today == day:
            return True, f"empty, but {day} is the proof-of-life day"
    return False, "nothing new, and today is not the proof-of-life day"


def _write_digest_file(digest: Digest, config: Config, override: str | None) -> Path | None:
    """The Markdown copy the scheduled Claude session reads.

    Written on every run, including empty ones: the file is the state of record
    for that lane, and a stale file would have it announce yesterday's roles.
    """
    if override:
        path = Path(override)
    elif config.digest_file.enabled:
        path = config.digest_file.path
    else:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(digest, config), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args)

    store: SeenStore | None = None
    if not args.no_state:
        store = SeenStore(config.state.path, config.state.retention_days).load()
        removed = store.prune()
        if removed:
            log.debug("pruned %d expired seen-store entries", removed)

    digest = build_digest(config, store=store)
    log.info(_summarise(digest))
    for name, why in digest.skipped:
        log.info("source off: %s — %s", name, why)
    if digest.failures:
        for name, error in digest.failures:
            log.warning("unavailable source: %s — %s", name, error)
        if digest.failure_ratio() > config.fetch.degraded_failure_ratio:
            log.error(
                "%d of %d runnable sources failed — the brief may be incomplete",
                len(digest.failures), digest.sources_total - len(digest.skipped),
            )

    subject = subject_line(digest, config)
    html_body = render_email_html(digest, config)
    text_body = render_email_text(digest, config)

    if args.dry_run:
        out = Path(args.out or PROJECT_ROOT / "out")
        out.mkdir(parents=True, exist_ok=True)
        stamp = digest.generated_at.strftime("%Y%m%d-%H%M")
        (out / f"digest-{stamp}.html").write_text(html_body, encoding="utf-8")
        (out / f"digest-{stamp}.txt").write_text(text_body, encoding="utf-8")
        (out / f"digest-{stamp}.md").write_text(render_markdown(digest, config), encoding="utf-8")
        print(f"Dry run — nothing sent. Subject would be: {subject}")
        print(f"Wrote 3 files to {out}")
        print(_summarise(digest))
        return EXIT_OK

    written = _write_digest_file(digest, config, args.write_digest)
    if written:
        log.info("digest written to %s", written)

    send, why = should_send(digest, config)
    if not send:
        log.info("not sending: %s", why)
        return EXIT_OK
    if why:
        log.info("sending: %s", why)

    if not config.email.enabled:
        log.warning("email delivery is disabled in settings.yaml — nothing was sent")
        return EXIT_DELIVERY_FAILED

    from .channels import email_smtp

    result = email_smtp.send(subject, text_body, html_body, config)
    (log.info if result.ok else log.error)("%s", result)

    if store is not None and result.ok:
        # Marked only after something actually went out, so a delivery failure
        # does not silently swallow a morning's openings.
        store.mark(digest.openings)
        store.save()
        log.debug("seen-store now holds %d keys", len(store))

    return EXIT_OK if result.ok else EXIT_DELIVERY_FAILED


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
        for opening, verdict in digest.rejected[: args.explain_limit]:
            print(f"  [{verdict.fit:5.1f}] {truncate(opening.title, 70)}")
            print(f"          {verdict.reason}")

    if args.html:
        target = Path(args.html)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_email_html(digest, config), encoding="utf-8")
        print(f"\nHTML written to {target}")
    if args.markdown:
        target = Path(args.markdown)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(digest, config), encoding="utf-8")
        print(f"Markdown written to {target}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# check-sources
# --------------------------------------------------------------------------- #

def cmd_check_sources(args: argparse.Namespace) -> int:
    """Fetch every source and report what came back.

    This is the command that matters most on a fresh checkout. The source list
    ships with several unverified entries — the sandbox this was written in
    could not reach a single job board — so this is how they get confirmed or
    corrected. Run it on a machine with open outbound access.
    """
    config = _load(args)
    sources = config.sources if args.all else config.enabled_sources
    print(f"Checking {len(sources)} source(s)…\n")

    results = fetch_all(sources, config.profile, config.fetch)
    now = datetime.now(timezone.utc)
    zone = local_zone(config.radar.timezone)
    failed = 0
    failed_in_service = 0
    off = 0

    print(f"{'':2} {'source':<24} {'kind':<11} {'jobs':>5} {'newest':>14}  detail")
    print("-" * 92)
    for result in results:
        source = result.source
        if result.skipped:
            off += 1
            print(f"{'—':2} {source.id:<24} {source.kind:<11} {'—':>5} {'—':>14}  off: {result.skipped}")
            continue
        if not result.ok:
            failed += 1
            # A source that is switched off in config is a diagnostic line, not
            # a fault: `--all` exists to show them, and a known-broken employer
            # left disabled on purpose must not hold this command red forever.
            # Red here has to keep meaning "something needs fixing".
            if source.enabled:
                failed_in_service += 1
            note = truncate(result.error or "", 46)
            if not source.enabled:
                note += " · disabled in config"
            print(f"{'✗':2} {source.id:<24} {source.kind:<11} {'—':>5} {'—':>14}  {note}")
            continue

        from .normalize import parse_date

        dates = [d for d in (parse_date(p.posted_raw, now=now) for p in result.postings) if d]
        newest = max(dates) if dates else None
        stamp = newest.astimezone(zone).strftime("%d %b %H:%M") if newest else "undated"
        mark = "✓"
        note = f"{result.elapsed_seconds:.1f}s"
        if not result.postings:
            mark = "!"
            note = "reachable, but returned nothing — check the query"
        print(
            f"{mark:2} {source.id:<24} {source.kind:<11} {len(result.postings):>5} "
            f"{stamp:>14}  {note}"
        )

    print("-" * 92)
    summary = (
        f"{len(results) - failed - off}/{len(results)} sources healthy, "
        f"{off} off for want of a key"
    )
    if failed > failed_in_service:
        summary += f", {failed - failed_in_service} failing but disabled in config"
    print(summary)
    if failed_in_service:
        print("\nA failing source is usually a wrong URL. For Workday, open the employer's")
        print("careers site in a browser, copy the address, and paste it into sources.yaml")
        print("as-is — the adapter works the rest out. Set `enabled: false` to mute one.")
    return EXIT_OK if failed_in_service == 0 else EXIT_DELIVERY_FAILED


# --------------------------------------------------------------------------- #
# test-delivery
# --------------------------------------------------------------------------- #

def cmd_test_delivery(args: argparse.Namespace) -> int:
    config = _load(args)
    from .channels import email_smtp

    problems = 0
    print("Email")
    if not email_smtp.configured():
        print("  ✗ not configured: " + ", ".join(email_smtp.missing_settings()))
        problems += 1
    else:
        try:
            print(f"  ✓ {email_smtp.verify(config)}")
            if args.send:
                result = email_smtp.send(
                    "VP Role Radar — test",
                    "Test message. Delivery is working.",
                    "<p><b>Test message.</b> Delivery is working.</p>",
                    config,
                )
                print(f"  {'✓' if result.ok else '✗'} {result}")
                problems += 0 if result.ok else 1
        except email_smtp.EmailError as exc:
            print(f"  ✗ {exc}")
            problems += 1

    if problems:
        print("\nDelivery needs attention — see README §Setup.")
    else:
        print("\nGmail delivery is ready.")
    return EXIT_OK if problems == 0 else EXIT_DELIVERY_FAILED


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m radar",
        description="A standing watch for senior key-account openings in life insurance.",
    )
    parser.add_argument("--config", help="config directory (default: ./config)")
    parser.add_argument("--env", help="path to a .env file (default: ./.env)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="scan and send the digest")
    run.add_argument("--dry-run", action="store_true", help="render to out/ instead of sending")
    run.add_argument("--out", help="directory for --dry-run output")
    run.add_argument(
        "--write-digest", help="where to write the Markdown copy (default: from settings.yaml)"
    )
    run.add_argument(
        "--no-state", action="store_true",
        help="ignore the seen-store: re-send openings already delivered",
    )
    run.set_defaults(func=cmd_run)

    preview = subparsers.add_parser("preview", help="print the digest without sending")
    preview.add_argument("--html", help="also write the HTML email to this path")
    preview.add_argument("--markdown", help="also write the Markdown digest to this path")
    preview.add_argument(
        "--explain", action="store_true", help="list the openings that were passed over, and why"
    )
    preview.add_argument("--explain-limit", type=int, default=25)
    preview.set_defaults(func=cmd_preview)

    check = subparsers.add_parser("check-sources", help="fetch every source and report on it")
    check.add_argument("--all", action="store_true", help="include disabled sources")
    check.set_defaults(func=cmd_check_sources)

    test = subparsers.add_parser("test-delivery", help="verify the Gmail credentials")
    test.add_argument("--send", action="store_true", help="also send a real test message")
    test.set_defaults(func=cmd_test_delivery)
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
