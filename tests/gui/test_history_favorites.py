"""History favorites — star/unstar, sort-to-top, and persistence."""

from __future__ import annotations

from equinox.gui.history_panel import HistoryPanel


class TestHistoryFavorites:
    """Favorited history IDs are toggled, persisted, and sort to the top."""

    def test_toggle_and_is_favorite(self, db):
        panel = HistoryPanel(db)
        entry_id = 42
        assert panel._is_favorite(entry_id) is False

        panel._toggle_favorite(entry_id)
        assert panel._is_favorite(entry_id) is True
        assert entry_id in panel._favorited_ids

        panel._toggle_favorite(entry_id)
        assert panel._is_favorite(entry_id) is False
        assert entry_id not in panel._favorited_ids

    def test_persisted_across_instances(self, db):
        entry_id = 7
        panel = HistoryPanel(db)
        panel._toggle_favorite(entry_id)

        # A new panel backed by the same settings store reloads the favorites.
        panel2 = HistoryPanel(db)
        assert panel2._is_favorite(entry_id) is True

    def test_favorites_sort_to_top(self, db):
        panel = HistoryPanel(db)
        entries = [
            {"id": 1, "method": "GET", "url": "https://a.example/1"},
            {"id": 2, "method": "POST", "url": "https://a.example/2"},
            {"id": 3, "method": "GET", "url": "https://a.example/3"},
        ]
        panel._toggle_favorite(2)
        panel._toggle_favorite(3)

        ordered = list(entries)
        ordered.sort(key=lambda e: (not panel._is_favorite(int(e["id"])),))
        assert [e["id"] for e in ordered] == [2, 3, 1]
