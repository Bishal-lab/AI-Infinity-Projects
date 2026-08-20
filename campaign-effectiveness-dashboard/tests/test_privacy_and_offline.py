"""Disclosure control, pseudonymisation, and the no-network guarantee.

``test_the_whole_pipeline_runs_with_no_network`` is the one that protects the
project's central promise. Everything else in the README can be re-read; this
one has to be executable.
"""

from __future__ import annotations

import socket

import pandas as pd
import pytest

from comms_dashboard.ingest.identity import (
    canonicalise,
    hash_identifier,
    make_key,
    to_e164,
)
from comms_dashboard.metrics import FilterState
from comms_dashboard.metrics.coverage import apply_suppression, coverage_by_segment


class TestIdentityCanonicalisation:
    @pytest.mark.parametrize(
        "raw",
        ["+447724829869", "447724829869", "07724 829869", "00447724829869",
         "447724829869.0", "+44 7724 829869"],
    )
    def test_every_way_of_writing_a_number_lands_on_one_value(self, raw):
        """Excel strips the +, systems disagree about the trunk prefix."""
        assert to_e164(raw, "+44") == "+447724829869"

    def test_email_case_and_padding_are_folded(self):
        assert canonicalise("  Ada.Lovelace@Example.COM ", ("strip", "lower")) == \
            "ada.lovelace@example.com"

    def test_the_same_person_hashes_the_same_from_either_system(self):
        salt = b"salt"
        from_export = make_key(" ADA@example.com ", ("strip", "lower"), salt=salt)
        from_roster = make_key("ada@example.com", ("strip", "lower"), salt=salt)
        assert from_export == from_roster

    def test_hashing_is_keyed_so_a_different_install_cannot_correlate(self):
        assert hash_identifier("ada@example.com", b"a") != \
            hash_identifier("ada@example.com", b"b")

    def test_blank_identifiers_produce_no_key(self):
        assert make_key("", ("strip", "lower"), salt=b"s") is None
        assert make_key(None, ("strip", "lower"), salt=b"s") is None


class TestStoredData:
    def test_no_direct_identifier_survives_ingestion(self, loaded, settings):
        """The warehouse must not hold an email address or a phone number."""
        assert settings.hash_identifiers
        keys = loaded.execute(
            "SELECT recipient_key FROM fact_recipient_stage LIMIT 500"
        ).df()["recipient_key"]
        assert not keys.str.contains("@", regex=False).any()
        assert not keys.str.startswith("+").any()
        assert keys.str.fullmatch(r"[0-9a-f]{32}").all()


class TestSuppression:
    def test_groups_below_the_threshold_are_blanked(self, settings):
        frame = pd.DataFrame(
            {
                "segment": ["Big", "Medium", "Tiny", "Small"],
                "headcount": [500, 200, 2, 3],
                "reached": [400, 150, 2, 3],
                "engaged": [100, 50, 1, 2],
                "coverage_rate": [0.8, 0.75, 1.0, 1.0],
                "never_reached": [100, 50, 0, 0],
            }
        )
        result = apply_suppression(frame, settings)
        hidden = result[result["suppressed"]]
        assert set(hidden["segment"]) == {"Tiny", "Small"}
        assert hidden["reached"].isna().all()
        # The row itself stays, so a reader can see a group was withheld rather
        # than assuming it does not exist.
        assert len(result) == 4

    def test_one_hidden_group_drags_the_next_smallest_with_it(self, settings):
        """Otherwise the hidden value is recoverable by subtraction."""
        assert settings.complementary_suppression
        frame = pd.DataFrame(
            {
                "segment": ["Big", "Medium", "Tiny"],
                "headcount": [500, 200, 2],
                "reached": [400, 150, 2],
                "coverage_rate": [0.8, 0.75, 1.0],
                "never_reached": [100, 50, 0],
            }
        )
        result = apply_suppression(frame, settings)
        assert result["suppressed"].sum() == 2
        assert result.loc[result["segment"] == "Medium", "suppressed"].iloc[0]

    def test_real_coverage_output_carries_the_suppression_flag(self, loaded, settings):
        frame = coverage_by_segment(loaded, FilterState(), "department", settings)
        assert "suppressed" in frame.columns
        assert frame["headcount"].notna().all()


class TestOffline:
    def test_the_whole_pipeline_runs_with_no_network(
        self, tmp_path, monkeypatch, settings, ladder
    ):
        """Ingest, query and export with every socket forbidden.

        On-prem is the entire premise of this project, so it is asserted rather
        than documented. If a dependency ever starts phoning home — DuckDB
        autoloading an extension, a chart library fetching an asset — this test
        fails instead of a customer's air-gapped server failing.
        """
        import shutil

        from comms_dashboard import db
        from comms_dashboard.export.excel import build_workbook
        from comms_dashboard.ingest.loader import ingest_file
        from comms_dashboard.metrics.kpis import headline_kpis
        from tests.conftest import SAMPLES

        inbox = tmp_path / "inbox"
        inbox.mkdir()
        for name in ("employee_roster.csv", "campaign_registry.csv",
                     "email_campaigns_2026-01.csv", "lms_enrolments_2026-02.csv",
                     "viva_engage_posts_2026-05.csv"):
            shutil.copy2(SAMPLES / name, inbox / name)

        def no_sockets(*args, **kwargs):
            raise AssertionError(
                "the pipeline attempted a network connection — this breaks the "
                "air-gapped deployment promise"
            )

        monkeypatch.setattr(socket, "socket", no_sockets)
        monkeypatch.setattr(socket, "create_connection", no_sockets)

        con = db.connect(tmp_path / "offline.duckdb")
        db.init_schema(con, ladder)
        for path in sorted(inbox.iterdir()):
            report = ingest_file(con, path, settings=settings, ladder=ladder,
                                 archive=False)
            assert report.status.startswith("loaded"), (path.name, report.issues)

        kpis = headline_kpis(con, FilterState(), settings, ladder)
        assert any(k.is_renderable for k in kpis.values())

        payload, filename = build_workbook(con, FilterState(), 1, settings)
        assert payload[:2] == b"PK" and filename.endswith(".xlsx")
        con.close()

    def test_duckdb_extension_autoloading_is_disabled(self, con):
        """Autoload fetches the extension over the internet on first use."""
        for setting in ("autoinstall_known_extensions", "autoload_known_extensions"):
            value = con.execute(
                "SELECT value FROM duckdb_settings() WHERE name = ?", [setting]
            ).fetchone()[0]
            assert str(value).lower() == "false", setting

    def test_streamlit_telemetry_is_switched_off(self):
        """Streamlit posts usage stats to an external endpoint by default."""
        from comms_dashboard.config import PROJECT_ROOT

        config = (PROJECT_ROOT / ".streamlit" / "config.toml").read_text()
        assert "gatherUsageStats = false" in config
