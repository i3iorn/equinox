"""HAR (HTTP Archive) importer — creates a collection from a .har file."""

import json
from equinox.storage.utils import safe_json_loads
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlencode

from equinox.storage.collections import CollectionManager
from equinox.core.request import Request

logger = logging.getLogger(__name__)

# Content-types that indicate binary / non-text bodies to skip
_BINARY_CONTENT_TYPES = {
    "image/", "audio/", "video/", "application/octet-stream",
    "application/zip", "application/gzip", "application/pdf",
    "font/",
}


def _is_binary(content_type: str) -> bool:
    ct = content_type.lower().split(";")[0].strip()
    return any(ct.startswith(p) for p in _BINARY_CONTENT_TYPES)


def _is_data_uri(url: str) -> bool:
    return url.lower().startswith("data:")


class HARImporter:
    """Import HTTP requests from a HAR (HTTP Archive) file into a collection."""

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_ENTRIES = 5000

    def __init__(self, collection_manager: CollectionManager) -> None:
        self.mgr = collection_manager

    # ── Public API ────────────────────────────────────────────────────

    def import_file(self, path: Path) -> int:
        """Parse *path* and create a new collection from the HAR entries.

        Args:
            path: Path to the ``.har`` file.

        Returns:
            The ID of the newly created collection.

        Raises:
            ValueError: If the file cannot be parsed or is not valid HAR.
        """
        path = Path(path)
        if path.exists():
            size = path.stat().st_size
            if size > self.MAX_FILE_SIZE:
                raise ValueError(
                    f"HAR file too large: {size} bytes (max {self.MAX_FILE_SIZE})"
                )

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Cannot read HAR file: {exc}") from exc

        try:
            # Use strict json.loads here so that invalid JSON raises a ValueError
            har = json.loads(text)
        except ValueError as exc:
            raise ValueError("Invalid JSON in HAR file") from exc
        if not isinstance(har, dict):
            raise ValueError("Invalid JSON in HAR file: top-level object is not a mapping")

        try:
            log = har["log"]
        except (KeyError, TypeError):
            raise ValueError("Not a valid HAR file — missing 'log' key")

        # Collection name: from log title or filename
        title = (log.get("title") or "").strip() or Path(path).stem
        collection_id = self.mgr.create_collection(title)
        logger.info("Created collection %r (ID %d) from HAR", title, collection_id)

        entries = log.get("entries") or []
        if len(entries) > self.MAX_ENTRIES:
            raise ValueError(
                f"Too many HAR entries: {len(entries)} (max {self.MAX_ENTRIES})"
            )

        imported = 0
        for idx, entry in enumerate(entries):
            try:
                req_data = entry.get("request") or {}
                if not req_data:
                    continue

                url = req_data.get("url", "")
                if _is_data_uri(url):
                    logger.debug("Skipping data URI entry %d", idx)
                    continue

                # Check body content-type for binary
                post_data = req_data.get("postData") or {}
                body_ct = post_data.get("mimeType", "")
                if body_ct and _is_binary(body_ct):
                    logger.debug("Skipping binary content-type %r at entry %d", body_ct, idx)
                    continue

                req = self._entry_to_request(req_data, collection_id)
                name = f"{req.method} {_short_url(url)}"
                self.mgr.save_request(req, collection_id=collection_id, name=name)
                imported += 1

            except Exception as exc:
                logger.warning("Skipping HAR entry %d: %s", idx, exc)

        logger.info("Imported %d request(s) into collection %d", imported, collection_id)
        return collection_id

    # ── Internal helpers ──────────────────────────────────────────────

    def _entry_to_request(self, req_data: dict, collection_id: int) -> Request:
        """Convert a HAR request dict to a :class:`Request` object."""
        method = (req_data.get("method") or "GET").upper()
        url = req_data.get("url") or ""

        # Headers: list of {name, value}
        headers = {}
        for h in req_data.get("headers") or []:
            name = h.get("name", "")
            value = h.get("value", "")
            # Skip HTTP/2 pseudo-headers
            if name and not name.startswith(":"):
                headers[name] = value

        # Query string: list of {name, value}
        params = {}
        for q in req_data.get("queryString") or []:
            k = q.get("name", "")
            v = q.get("value", "")
            if k:
                params[k] = v

        # Body
        body: Optional[str] = None
        post_data = req_data.get("postData") or {}
        if post_data:
            text = post_data.get("text")
            if text:
                body = text

        return Request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            body=body,
            collection_id=collection_id,
        )


def _short_url(url: str, max_len: int = 60) -> str:
    """Return a display-friendly shortened URL for use as a request name."""
    try:
        parsed = urlparse(url)
        short = parsed.path or "/"
        if parsed.query:
            short = f"{short}?{parsed.query[:20]}…"
        return short[:max_len]
    except Exception:
        return url[:max_len]
