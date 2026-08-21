"""Regression tests for the findings of the post-merge security review.

Every test here fails against the code as it was before the corresponding fix.
They are grouped by finding so that a failure names the thing that broke rather
than the assertion that noticed.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from comms_dashboard.config import Settings  # noqa: E402
from comms_dashboard.models import LoadIssue, redact_example  # noqa: E402


# --------------------------------------------------------------------------- #
# Finding 1 — path traversal in the admin uploader
# --------------------------------------------------------------------------- #


class TestUploadPathTraversal:
    """``UploadedFile.name`` is attacker-controlled.

    Tornado passes the multipart ``Content-Disposition`` filename through
    verbatim and Streamlit stores it unmodified, so the browser chooses this
    string. Joining it onto the inbox let it escape.
    """

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "data" / "inbox").mkdir(parents=True)
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "secret_salt").write_text("REAL-SALT")
        return tmp_path

    @pytest.mark.parametrize(
        "name",
        [
            "../../config/secret_salt",
            "../../config/settings.yaml",
            "../escape.csv",
            "..\\..\\config\\secret_salt",  # Windows separators, on any host
            "subdir/nested.csv",
        ],
    )
    def test_it_refuses_to_write_outside_the_inbox(self, tree: Path, name: str) -> None:
        from views.admin import safe_inbox_target

        inbox = tree / "data" / "inbox"
        try:
            target = safe_inbox_target(inbox, name)
        except ValueError:
            return  # refused outright, which is also a correct answer
        assert target.resolve().parent == inbox.resolve(), (
            f"{name!r} resolved to {target.resolve()}, outside the inbox"
        )

    def test_an_absolute_name_does_not_replace_the_inbox(self, tree: Path) -> None:
        """``Path('/a/b') / '/etc/passwd'`` is ``/etc/passwd`` — the base is gone."""
        from views.admin import safe_inbox_target

        inbox = tree / "data" / "inbox"
        victim = tree / "config" / "secret_salt"
        try:
            target = safe_inbox_target(inbox, str(victim))
        except ValueError:
            return  # refused outright, which is also a correct answer
        assert target.resolve().parent == inbox.resolve()

    def test_the_salt_survives_a_traversal_attempt(self, tree: Path) -> None:
        """The end-to-end consequence, not just the path arithmetic.

        Overwriting the salt with a known value is worse than losing the file:
        every identifier hashed afterwards becomes reversible by whoever chose
        it.
        """
        from views.admin import safe_inbox_target

        inbox = tree / "data" / "inbox"
        salt = tree / "config" / "secret_salt"
        try:
            target = safe_inbox_target(inbox, "../../config/secret_salt")
            target.write_bytes(b"ATTACKER-CHOSEN-SALT")
        except ValueError:
            pass
        assert salt.read_text() == "REAL-SALT"

    @pytest.mark.parametrize("name", ["evil.sh", "settings.yaml", "x.py", "noext"])
    def test_it_refuses_extensions_the_inbox_does_not_handle(
        self, tree: Path, name: str
    ) -> None:
        """``type=`` on ``st.file_uploader`` only filters the browser's picker."""
        from views.admin import safe_inbox_target

        with pytest.raises(ValueError):
            safe_inbox_target(tree / "data" / "inbox", name)

    @pytest.mark.parametrize("name", ["report.csv", "Export 2026.XLSX", "a.tsv"])
    def test_it_still_accepts_ordinary_exports(self, tree: Path, name: str) -> None:
        from views.admin import safe_inbox_target

        inbox = tree / "data" / "inbox"
        assert safe_inbox_target(inbox, name).parent == inbox


# --------------------------------------------------------------------------- #
# Finding 2 — suppression checked the population but not the measure
# --------------------------------------------------------------------------- #


class TestSmallCellSuppression:
    def _settings(self, tmp_path: Path, minimum: int = 5) -> Settings:
        return Settings(
            raw={"privacy": {"min_group_size": minimum, "complementary_suppression": True}},
            root=tmp_path,
        )

    def test_one_person_in_a_large_department_is_not_published(
        self, tmp_path: Path
    ) -> None:
        """The shape a management pack actively looks for.

        "1 of the 40 people in Legal completed the anti-bribery module" names an
        individual as surely as a three-person department does, and the old rule
        — population below the threshold — let it straight through.
        """
        import pandas as pd

        from comms_dashboard.metrics.coverage import apply_suppression

        frame = pd.DataFrame(
            [
                {"segment": "Legal", "headcount": 40, "targeted": 40,
                 "reached": 38, "engaged": 1, "completed": 1},
                {"segment": "Operations", "headcount": 900, "targeted": 900,
                 "reached": 850, "engaged": 600, "completed": 400},
                {"segment": "Finance", "headcount": 120, "targeted": 120,
                 "reached": 115, "engaged": 90, "completed": 88},
            ]
        )
        out = apply_suppression(frame, self._settings(tmp_path))
        legal = out[out["segment"] == "Legal"].iloc[0]
        assert legal["suppressed"]
        assert pd.isna(legal["engaged"])
        assert pd.isna(legal["completed"])
        # The large, unremarkable group is untouched.
        assert not out[out["segment"] == "Operations"].iloc[0]["suppressed"]

    def test_a_count_of_zero_stays_visible(self, tmp_path: Path) -> None:
        """Zero identifies nobody, and hiding it would bury the finding.

        "Nobody in Legal completed it" is the single most useful sentence a
        coverage report can produce. Suppressing small counts must not swallow
        it.
        """
        import pandas as pd

        from comms_dashboard.metrics.coverage import apply_suppression

        frame = pd.DataFrame(
            [
                {"segment": "Legal", "headcount": 40, "targeted": 40,
                 "reached": 40, "engaged": 30, "completed": 0},
                {"segment": "Ops", "headcount": 900, "targeted": 900,
                 "reached": 850, "engaged": 600, "completed": 400},
            ]
        )
        out = apply_suppression(frame, self._settings(tmp_path))
        legal = out[out["segment"] == "Legal"].iloc[0]
        assert not legal["suppressed"]
        assert legal["completed"] == 0

    def test_never_reached_is_covered_too(self, tmp_path: Path) -> None:
        """The complement singles people out just as well as the count does."""
        import pandas as pd

        from comms_dashboard.metrics.coverage import apply_suppression

        frame = pd.DataFrame(
            [
                {"segment": "Legal", "headcount": 40, "targeted": 40,
                 "reached": 38, "engaged": 30, "completed": 30,
                 "never_reached": 2},
                {"segment": "Ops", "headcount": 900, "targeted": 900,
                 "reached": 850, "engaged": 600, "completed": 400,
                 "never_reached": 50},
            ]
        )
        out = apply_suppression(frame, self._settings(tmp_path))
        assert out[out["segment"] == "Legal"].iloc[0]["suppressed"]


# --------------------------------------------------------------------------- #
# Finding 3 — raw identifiers reached load_issue.example_values
# --------------------------------------------------------------------------- #


class TestIssueLogRedaction:
    @pytest.mark.parametrize(
        "value",
        [
            "550 5.1.1 <grace.hopper@acme.example>: Recipient address rejected",
            "552 mailbox full for alan.turing@acme.example",
            "bounced: ada@sub.acme.co.uk",
        ],
    )
    def test_an_email_never_survives_into_an_issue(self, value: str) -> None:
        issue = LoadIssue(
            severity="warning", code="COERCION_FAILED",
            message="could not read", example_values=(value,),
        )
        assert "@" not in issue.example_values[0]
        assert "<email>" in issue.example_values[0]

    @pytest.mark.parametrize(
        "value", ["+44 7724 829869", "07724829869", "00447724829869"]
    )
    def test_a_phone_number_never_survives_into_an_issue(self, value: str) -> None:
        issue = LoadIssue(
            severity="warning", code="COERCION_FAILED",
            message="could not read", example_values=(value,),
        )
        assert issue.example_values == ("<number>",)

    @pytest.mark.parametrize(
        "value",
        [
            "2026-01-05",       # an ISO date: eight digits, must stay readable
            "05/01/2026",
            "31 Feb 2026",
            "85%",
            "1,234.56",
            "not a date",
        ],
    )
    def test_it_keeps_the_diagnostic_the_operator_needs(self, value: str) -> None:
        """Redaction that eats the evidence is its own kind of failure.

        The whole point of quoting the value back is that the operator can see
        *why* the column would not parse. A date that reads ``<number>`` tells
        them nothing.
        """
        assert redact_example(value) == value

    def test_the_smtp_code_survives_even_though_the_address_does_not(self) -> None:
        out = redact_example("550 5.1.1 <ada@acme.example>: rejected")
        assert out.startswith("550 5.1.1")
        assert "ada" not in out

    def test_a_very_long_value_is_truncated(self) -> None:
        assert len(redact_example("x" * 5000)) < 200

    def test_an_ordinary_export_leaves_no_address_in_the_warehouse(
        self, tmp_path: Path
    ) -> None:
        """End to end: an ESP that writes the SMTP diagnostic into its status
        column is ordinary output, not a crafted file."""
        import comms_dashboard.db as db
        from comms_dashboard.config import get_settings
        from comms_dashboard.ingest.loader import ingest_file

        csv = tmp_path / "email_onboarding_2026-01.csv"
        csv.write_text(
            "Campaign ID,Recipient Email,Delivery Status,Unique Opens,Send Time\n"
            "onboarding,ada.lovelace@acme.example,delivered,1,2026-01-05 09:00\n"
            "onboarding,grace.hopper@acme.example,"
            '"550 5.1.1 <grace.hopper@acme.example>: rejected",0,2026-01-05 09:00\n',
            encoding="utf-8",
        )
        con = db.connect(":memory:")
        db.init_schema(con)
        ingest_file(con, csv, settings=get_settings(), archive=False)

        stored = con.execute(
            "SELECT COALESCE(example_values, '') || ' ' || COALESCE(message, '') "
            "FROM load_issue"
        ).fetchall()
        blob = " ".join(row[0] for row in stored)
        assert "@" not in blob, f"an address reached load_issue: {blob!r}"

        # And the same for the full report JSON on load_batch, which is a second
        # copy of the same issues.
        report = con.execute("SELECT CAST(report AS VARCHAR) FROM load_batch").fetchone()[0]
        assert "acme.example" not in report


# --------------------------------------------------------------------------- #
# Finding 4 — the salt could be baked into the container image
# --------------------------------------------------------------------------- #


class TestSaltStaysOutOfTheImage:
    @property
    def _root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def test_a_dockerignore_exists_and_excludes_the_salt(self) -> None:
        """``COPY config/ ./config/`` would otherwise ship the live salt.

        The image travels to the air-gapped host as a ``docker save`` tarball,
        so the salt would travel with it — and leaking it makes every hashed
        identifier reversible.
        """
        ignore = self._root / ".dockerignore"
        assert ignore.exists(), "no .dockerignore: the build context includes config/"
        entries = {
            line.strip()
            for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        assert "config/secret_salt" in entries

    def test_the_build_refuses_if_the_salt_gets_in_anyway(self) -> None:
        dockerfile = (self._root / "docker" / "Dockerfile").read_text(encoding="utf-8")
        assert "config/secret_salt" in dockerfile
        assert "BUILD REFUSED" in dockerfile

    def test_real_data_directories_are_excluded_too(self) -> None:
        entries = {
            line.strip()
            for line in (self._root / ".dockerignore")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        for directory in ("data/inbox/", "data/archive/", "data/warehouse/"):
            assert directory in entries
        # ...but the invented sample data is still shipped, so a fresh install
        # has something to demonstrate with.
        assert not any(entry.startswith("data/samples") for entry in entries)


# --------------------------------------------------------------------------- #
# Finding 5 — salt permissions were set once and never checked again
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
class TestSaltPermissions:
    def test_a_new_salt_is_owner_only(self, tmp_path: Path) -> None:
        settings = Settings(
            raw={"privacy": {"salt_file": "config/secret_salt"}}, root=tmp_path
        )
        settings.salt()
        mode = stat.S_IMODE((tmp_path / "config" / "secret_salt").stat().st_mode)
        assert mode == 0o600

    def test_a_salt_restored_from_backup_is_narrowed_on_read(
        self, tmp_path: Path
    ) -> None:
        """The documented backup procedure is how this happens in practice.

        ``docs/operations.md`` tells the operator to copy the salt away and
        restore it. A restored copy carries the umask of whatever did the
        restoring, and nothing re-checked it.
        """
        salt = tmp_path / "config" / "secret_salt"
        salt.parent.mkdir(parents=True)
        salt.write_text("a" * 64)
        os.chmod(salt, 0o644)

        settings = Settings(
            raw={"privacy": {"salt_file": "config/secret_salt"}}, root=tmp_path
        )
        settings.salt()
        assert stat.S_IMODE(salt.stat().st_mode) == 0o600

    def test_the_salt_value_is_unchanged_by_the_permission_fix(
        self, tmp_path: Path
    ) -> None:
        """Narrowing the mode must never rewrite the file: a changed salt
        silently breaks every identity join already in the warehouse."""
        salt = tmp_path / "config" / "secret_salt"
        salt.parent.mkdir(parents=True)
        salt.write_text("b" * 64)
        os.chmod(salt, 0o666)

        settings = Settings(
            raw={"privacy": {"salt_file": "config/secret_salt"}}, root=tmp_path
        )
        assert settings.salt() == b"b" * 64


# --------------------------------------------------------------------------- #
# Finding 6 — KPI tiles interpolated into raw HTML
# --------------------------------------------------------------------------- #


class TestKpiTileEscaping:
    def test_a_label_carrying_markup_is_escaped(self, monkeypatch) -> None:
        """Latent today, live the moment a tile is labelled with a campaign name.

        Campaign names come from uploaded CSVs, so the escape is what stops the
        next person who adds such a tile from shipping stored XSS.
        """
        import components.kpi_cards as kpi_cards
        from comms_dashboard.models import KpiValue, SupportStatus

        captured: list[str] = []
        monkeypatch.setattr(
            kpi_cards.st, "markdown", lambda body, **kw: captured.append(body)
        )
        monkeypatch.setattr(kpi_cards.st, "caption", lambda *a, **kw: None)

        kpi = KpiValue(
            key="k",
            label="<img src=x onerror=alert(1)>",
            value=0.5,
            status=SupportStatus.MEASURED,
            numerator=1,
            denominator=2,
        )
        kpi_cards.kpi_card(kpi)

        body = captured[0]
        assert "<img src=x" not in body
        assert "&lt;img src=x" in body
