"""Loading the same data twice must not change any number.

This is the test that protects the project's most consequential claim. A
dashboard that double-counts on a re-drop is worse than no dashboard, because
the numbers stay plausible while being wrong.
"""

from __future__ import annotations

import pandas as pd

from comms_dashboard.ingest.loader import ingest_file

from .conftest import EDGE_CASES


def _counts(con) -> dict[str, int]:
    return {
        "recipients": con.execute("SELECT COUNT(*) FROM fact_recipient_stage").fetchone()[0],
        "metrics": con.execute("SELECT COUNT(*) FROM fact_stage_metric").fetchone()[0],
    }


def _funnel(con) -> pd.DataFrame:
    # campaign_id is part of the grain — without it the frame has one row per
    # campaign per stage and any join on (channel, stage) fans out.
    return con.execute(
        "SELECT campaign_id, channel, stage, metric_value FROM v_stage_totals "
        "ORDER BY campaign_id, channel, stage_ordinal"
    ).df()


class TestFileLedger:
    def test_byte_identical_redrop_is_skipped(self, con, staged, settings, ladder):
        first = ingest_file(con, staged("email_campaigns_2026-01.csv"),
                            settings=settings, ladder=ladder, archive=False)
        assert first.status.startswith("loaded")
        before = _counts(con)

        second = ingest_file(con, staged("email_campaigns_2026-01.csv"),
                             settings=settings, ladder=ladder, archive=False)
        assert second.status == "duplicate"
        assert [i.code for i in second.issues] == ["DUPLICATE_FILE"]
        assert _counts(con) == before

    def test_a_renamed_copy_is_still_recognised_as_the_same_file(
        self, con, staged, settings, ladder, inbox
    ):
        """The ledger keys on content, so renaming an export does not defeat it."""
        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        before = _counts(con)

        copy = staged("email_exact_duplicate.csv", EDGE_CASES)
        renamed = inbox / "totally_different_name.csv"
        copy.rename(renamed)
        report = ingest_file(con, renamed, settings=settings, ladder=ladder,
                             archive=False)
        assert report.status == "duplicate"
        assert _counts(con) == before


class TestNaturalKeyMerge:
    def test_a_superset_reexport_does_not_double_count(
        self, con, staged, settings, ladder
    ):
        """The case a hash ledger alone misses: same period, extra rows.

        The file's bytes differ, so the ledger lets it through. Only the
        natural-key upsert stops the original rows being counted twice.
        """
        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        original = _funnel(con)
        original_recipients = _counts(con)["recipients"]

        report = ingest_file(con, staged("email_superset_reexport.csv", EDGE_CASES),
                             settings=settings, ladder=ladder, archive=False)
        assert report.status.startswith("loaded")

        after = _funnel(con)
        merged = original.merge(
            after, on=["campaign_id", "channel", "stage"], suffixes=("_a", "_b")
        )
        # Compare only where both sides are real numbers: a stage the channel
        # cannot measure is NaN, and NaN >= NaN is False — which is exactly the
        # n/a-is-not-a-number behaviour the rest of the suite relies on.
        comparable = merged.dropna(subset=["metric_value_a", "metric_value_b"])
        assert not comparable.empty
        assert (comparable["metric_value_b"] >= comparable["metric_value_a"]).all()

        # The 150 genuinely new recipients raise the counts by at most that
        # much — the 5,440 rows shared with the first file are not counted twice.
        growth = _counts(con)["recipients"] - original_recipients
        assert 0 < growth <= 150
        grew = comparable[comparable["metric_value_b"] > comparable["metric_value_a"]]
        assert (grew["metric_value_b"] - grew["metric_value_a"] <= 150).all()

    def test_a_known_positive_never_regresses_to_unknown(
        self, con, staged, settings, ladder, inbox
    ):
        """A later, thinner export must not erase engagement already recorded."""
        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        opened_before = con.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage WHERE opened IS TRUE"
        ).fetchone()[0]
        assert opened_before > 0

        # Re-export the same campaign with the open tracking column stripped out.
        thin = pd.read_csv(EDGE_CASES / "email_exact_duplicate.csv")
        thin = thin.drop(columns=["Unique Opens", "First Open"])
        path = inbox / "email_no_open_tracking.csv"
        thin.to_csv(path, index=False)

        report = ingest_file(con, path, settings=settings, ladder=ladder, archive=False)
        assert report.status.startswith("loaded")

        opened_after = con.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage WHERE opened IS TRUE"
        ).fetchone()[0]
        assert opened_after == opened_before


class TestRollups:
    def test_rollups_are_recomputed_not_accumulated(self, con, staged, settings, ladder):
        """Derived numbers that accumulate drift; recomputed ones cannot."""
        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        first = _funnel(con)

        ingest_file(con, staged("email_exact_duplicate.csv", EDGE_CASES),
                    settings=settings, ladder=ladder, archive=False, force=True)
        second = _funnel(con)
        pd.testing.assert_frame_equal(first, second)

    def test_rollups_agree_with_the_recipient_rows_they_derive_from(self, loaded):
        for channel, column in [("email", "opened"), ("lms", "completed"),
                                ("teams_webinar", "engaged")]:
            direct = loaded.execute(
                f"SELECT COUNT(*) FROM fact_recipient_stage "
                f"WHERE channel = ? AND {column} IS TRUE",
                [channel],
            ).fetchone()[0]
            stage = {"opened": "OPENED", "completed": "COMPLETED",
                     "engaged": "ENGAGED"}[column]
            rolled = loaded.execute(
                "SELECT SUM(metric_value) FROM v_stage_totals "
                "WHERE channel = ? AND stage = ?",
                [channel, stage],
            ).fetchone()[0]
            assert rolled == direct, channel


class TestOrdering:
    def test_a_roster_loaded_last_still_resolves_earlier_history(
        self, con, staged, settings, ladder
    ):
        """Identity is joined at query time, so ordering does not matter."""
        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        unresolved = con.execute(
            "SELECT resolved FROM v_identity_resolution WHERE channel = 'email'"
        ).fetchone()[0]
        assert unresolved == 0

        ingest_file(con, staged("employee_roster.csv"),
                    settings=settings, ladder=ladder, archive=False)
        resolved = con.execute(
            "SELECT resolved FROM v_identity_resolution WHERE channel = 'email'"
        ).fetchone()[0]
        assert resolved > 0
