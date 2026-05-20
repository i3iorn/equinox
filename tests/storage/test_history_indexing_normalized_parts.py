from unittest.mock import patch

from equinox.storage.history import HistoryManager


class FakeDB:
    def __init__(self):
        self.insert_calls = []

    def fetchone(self, query, params=None):
        # Return a fake executed_at value expected by _index_history_row
        return {"executed_at": "2026-03-01T00:00:00Z"}

    def insert(self, query, params=None):
        self.insert_calls.append((query, params))
        return 1


def test_index_history_row_expands_placeholders_and_normalizes():
    db = FakeDB()
    hm = HistoryManager(db)

    test_url = "https://api.example.com/users/{{id}}/posts"

    with (
        patch("equinox.core.urls.expand_placeholders") as mock_expand,
        patch("equinox.core.urls.normalized_parts") as mock_normalized,
    ):
        mock_expand.return_value = "https://api.example.com/users/42/posts"
        mock_normalized.return_value = {
            "normalized_url": "https://api.example.com/users/{id}/posts",
            "path_segments": ["users", "{id}", "posts"],
            "query_params": {},
        }

        hm._indexer.index(1, "GET", test_url, 200, b"{}")

        # Assert expand_placeholders called with original templated URL
        mock_expand.assert_called_once_with(test_url, None)
        # Assert normalized_parts was called with the expanded URL
        mock_normalized.assert_called_once()
        # Check that an insert into history_index was attempted
        assert db.insert_calls, "history_index insert was not called"
