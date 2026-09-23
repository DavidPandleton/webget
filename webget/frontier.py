"""Small SQLite-backed frontier for resumable local crawls.

The frontier deliberately does not fetch pages. It owns durable URL state,
deduplication, bounded depth/page/domain policy, and retryable outcomes so a
caller can stop and resume without a service or external database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.parse import urldefrag, urlsplit, urlunsplit


@dataclass(frozen=True)
class FrontierItem:
    id: int
    url: str
    depth: int
    parent_url: str | None
    attempts: int


def normalize_url(url: str) -> str:
    """Normalize only identity noise; preserve path and query semantics."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be non-empty text")
    raw, _fragment = urldefrag(url.strip())
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"unsupported URL: {url!r}")
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid URL port: {url!r}") from exc
    if (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443):
        port = None
    host = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


class CrawlFrontier:
    """Durable, single-process crawl queue backed by SQLite."""

    def __init__(
        self,
        path: str | Path,
        *,
        allowed_domains: set[str] | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
    ):
        if max_depth is not None and (not isinstance(max_depth, int) or max_depth < 0):
            raise ValueError("max_depth must be a non-negative integer or None")
        if max_pages is not None and (not isinstance(max_pages, int) or max_pages < 1):
            raise ValueError("max_pages must be a positive integer or None")
        self.path = str(path)
        self.allowed_domains = {
            self._domain_name(domain) for domain in (allowed_domains or set())
        }
        self.max_depth = max_depth
        self.max_pages = max_pages
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS frontier (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                depth INTEGER NOT NULL,
                parent_url TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','in_progress','done','failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
                updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE INDEX IF NOT EXISTS frontier_claim_idx
                ON frontier(status, depth, id);
            """
        )
        # Recover claims left behind by a crashed process so resume does not
        # strand URLs permanently in ``in_progress``.
        self._db.execute(
            "UPDATE frontier SET status='pending', updated_at=unixepoch('subsec') "
            "WHERE status='in_progress'"
        )

    @staticmethod
    def _domain_name(value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if "://" in value:
            value = urlsplit(value).hostname or ""
        return value

    def _allowed(self, url: str) -> bool:
        if not self.allowed_domains:
            return True
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        return host in self.allowed_domains

    def enqueue(self, url: str, *, depth: int = 0, parent_url: str | None = None) -> bool:
        """Insert a new pending URL; return False for duplicates/policy rejects."""
        normalized = normalize_url(url)
        if not isinstance(depth, int) or depth < 0:
            raise ValueError("depth must be a non-negative integer")
        if self.max_depth is not None and depth > self.max_depth:
            return False
        if not self._allowed(normalized):
            return False
        parent = normalize_url(parent_url) if parent_url else None
        with self._db:
            cur = self._db.execute(
                """
                INSERT OR IGNORE INTO frontier(url, depth, parent_url, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (normalized, depth, parent),
            )
        return cur.rowcount == 1

    def claim(self, limit: int = 1) -> list[FrontierItem]:
        """Atomically claim the next pending URLs for the current worker."""
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._db:
            rows = self._db.execute(
                "SELECT id FROM frontier WHERE status='pending' ORDER BY depth, id LIMIT ?",
                (limit,),
            ).fetchall()
            if self.max_pages is not None:
                used = self._db.execute(
                    "SELECT COUNT(*) FROM frontier WHERE status IN ('in_progress','done')"
                ).fetchone()[0]
                rows = rows[: max(0, self.max_pages - used)]
            ids = [row[0] for row in rows]
            if not ids:
                return []
            marks = ",".join("?" for _ in ids)
            self._db.execute(
                f"UPDATE frontier SET status='in_progress', attempts=attempts+1, "
                f"updated_at=unixepoch('subsec') WHERE id IN ({marks})",
                ids,
            )
            claimed = self._db.execute(
                f"SELECT id,url,depth,parent_url,attempts FROM frontier WHERE id IN ({marks}) ORDER BY depth,id",
                ids,
            ).fetchall()
        return [FrontierItem(**dict(row)) for row in claimed]

    def complete(self, url: str) -> None:
        self._set_status(url, "done", None)

    def fail(self, url: str, error: str) -> None:
        self._set_status(url, "failed", str(error))

    def retry(self, url: str, error: str | None = None) -> None:
        self._set_status(url, "pending", error)

    def _set_status(self, url: str, status: str, error: str | None) -> None:
        normalized = normalize_url(url)
        with self._db:
            cur = self._db.execute(
                "UPDATE frontier SET status=?, error=?, updated_at=unixepoch('subsec') WHERE url=?",
                (status, error, normalized),
            )
        if cur.rowcount != 1:
            raise KeyError(normalized)

    def stats(self) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT status, COUNT(*) AS count FROM frontier GROUP BY status"
        ).fetchall()
        out = {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}
        out.update({row["status"]: row["count"] for row in rows})
        out["total"] = sum(out.values())
        return out

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
