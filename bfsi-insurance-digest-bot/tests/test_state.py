"""The seen-store: what keeps an overlapping window from repeating itself."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bot.model import Article
from bot.state import SeenStore

NOW = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)


def article(title: str, url: str) -> Article:
    return Article(source_id="s", source_name="S", title=title, url=url)


def test_a_marked_article_is_remembered_across_runs(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path).load()
    item = article("IRDAI eases norms", "https://a.test/1")
    assert not store.is_seen(item)

    store.mark([item])
    store.save()

    assert SeenStore(path).load().is_seen(item)


def test_the_same_headline_under_a_new_url_is_still_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    store.mark([article("IRDAI eases norms", "https://a.test/1")])
    assert store.is_seen(article("IRDAI eases norms", "https://elsewhere.test/9"))


def test_filter_new_also_removes_duplicates_inside_one_batch(tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    fresh = store.filter_new([
        article("One", "https://a.test/1"),
        article("One", "https://a.test/1"),
        article("Two", "https://b.test/2"),
    ])
    assert [a.title for a in fresh] == ["One", "Two"]


def test_prune_forgets_entries_past_the_retention_window(tmp_path):
    store = SeenStore(tmp_path / "seen.json", retention_days=7).load()
    store.mark([article("Old", "https://a.test/1")], when=NOW - timedelta(days=30))
    store.mark([article("Recent", "https://b.test/2")], when=NOW - timedelta(days=1))
    assert store.prune(now=NOW) == 2  # both keys of the old article
    assert store.is_seen(article("Recent", "https://b.test/2"))
    assert not store.is_seen(article("Old", "https://a.test/1"))


def test_a_corrupt_store_starts_fresh_instead_of_failing(tmp_path):
    """A damaged file should cost one noisier digest, never a missed run."""
    path = tmp_path / "seen.json"
    path.write_text("{not json at all", encoding="utf-8")
    store = SeenStore(path).load()
    assert len(store) == 0
    store.mark([article("A", "https://a.test/1")])
    store.save()
    assert json.loads(path.read_text())["entries"]


def test_save_creates_the_directory_and_writes_atomically(tmp_path):
    path = tmp_path / "nested" / "dir" / "seen.json"
    store = SeenStore(path).load()
    store.mark([article("A", "https://a.test/1")])
    store.save()
    assert path.is_file()
    # No temporary files left behind.
    assert [p.name for p in path.parent.iterdir()] == ["seen.json"]
