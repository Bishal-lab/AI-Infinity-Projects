"""The seen-store: mailed once, however long the posting stays live."""

from __future__ import annotations

from datetime import timedelta

from radar.state import SeenStore
from tests.conftest import NOW, make_opening


def test_an_opening_is_new_once(tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    opening = make_opening()
    assert store.filter_new([opening]) == [opening]

    store.mark([opening], when=NOW)
    assert store.filter_new([opening]) == []


def test_the_same_job_under_a_new_url_is_still_seen(tmp_path):
    """Aggregators re-list a job under a fresh link; employer plus title is
    what actually identifies it."""
    store = SeenStore(tmp_path / "seen.json").load()
    store.mark([make_opening(url="https://a.test/1")], when=NOW)
    assert store.filter_new([make_opening(url="https://b.test/2")]) == []


def test_duplicates_inside_one_batch_are_dropped(tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    assert len(store.filter_new([make_opening(), make_opening()])) == 1


def test_the_store_survives_a_round_trip(tmp_path):
    path = tmp_path / "nested" / "seen.json"
    store = SeenStore(path).load()
    store.mark([make_opening()], when=NOW)
    store.save()

    assert SeenStore(path).load().is_seen(make_opening())


def test_pruning_forgets_old_entries(tmp_path):
    store = SeenStore(tmp_path / "seen.json", retention_days=45).load()
    store.mark([make_opening()], when=NOW - timedelta(days=60))
    assert store.prune(now=NOW) == 2  # both keys for the one opening
    assert len(store) == 0


def test_a_damaged_store_starts_fresh_rather_than_failing(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(SeenStore(path).load()) == 0
