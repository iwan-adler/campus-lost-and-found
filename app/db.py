"""SQLite persistence using the standard-library sqlite3 module."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT NOT NULL CHECK (kind IN ('lost', 'found')),
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    category          TEXT NOT NULL,
    location          TEXT NOT NULL,
    event_date        TEXT NOT NULL,          -- when it was lost / found (YYYY-MM-DD)
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'claimed', 'returned')),
    manage_token_hash TEXT NOT NULL,          -- sha256 of the reporter's secret token
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    claimant_name  TEXT NOT NULL,
    contact        TEXT NOT NULL,             -- only ever shown to the item's manager
    details        TEXT NOT NULL,             -- proof of ownership
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_filters ON items (status, kind, category, location);
CREATE INDEX IF NOT EXISTS idx_claims_item ON claims (item_id);
"""

# Columns safe to show to anyone (no token hash).
PUBLIC_ITEM_COLUMNS = """
    i.id, i.kind, i.title, i.description, i.category, i.location, i.event_date,
    i.status, i.created_at, i.updated_at,
    (SELECT COUNT(*) FROM claims c WHERE c.item_id = i.id AND c.status = 'pending')
        AS pending_claims
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def escape_like(text: str) -> str:
    """Make % and _ in user input match literally in a LIKE pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Short-lived connection: commit on success, roll back on any error."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # --- items -----------------------------------------------------------

    def create_item(self, fields: dict[str, Any], manage_token_hash: str) -> dict[str, Any]:
        timestamp = now()
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO items (kind, title, description, category, location,
                                      event_date, manage_token_hash, created_at, updated_at)
                   VALUES (:kind, :title, :description, :category, :location,
                           :event_date, :hash, :ts, :ts)""",
                {**fields, "hash": manage_token_hash, "ts": timestamp},
            )
            item_id = cursor.lastrowid
        return self.get_item(item_id)  # type: ignore[arg-type,return-value]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {PUBLIC_ITEM_COLUMNS} FROM items i WHERE i.id = ?", (item_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_token_hash(self, item_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT manage_token_hash FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        return row["manage_token_hash"] if row else None

    def search_items(
        self,
        q: str | None = None,
        kind: str | None = None,
        category: str | None = None,
        location: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (one page of items newest first, total matching count)."""
        conditions, params = [], []
        if q:
            # Every word must appear in the title or description (case-insensitive for ASCII).
            for word in q.split():
                conditions.append(
                    "(i.title LIKE ? ESCAPE '\\' OR i.description LIKE ? ESCAPE '\\')"
                )
                pattern = f"%{escape_like(word)}%"
                params += [pattern, pattern]
        for column, value in (
            ("kind", kind),
            ("category", category),
            ("location", location),
            ("status", status),
        ):
            if value:
                conditions.append(f"i.{column} = ?")  # column names are fixed, never user input
                params.append(value)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM items i {where}", params).fetchone()[0]
            rows = conn.execute(
                f"""SELECT {PUBLIC_ITEM_COLUMNS} FROM items i {where}
                    ORDER BY i.created_at DESC, i.id DESC LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def set_item_status(self, item_id: int, status: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, now(), item_id),
            )
        return self.get_item(item_id)  # type: ignore[return-value]

    # --- claims ----------------------------------------------------------

    def create_claim(self, item_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO claims (item_id, claimant_name, contact, details, created_at)
                   VALUES (:item_id, :claimant_name, :contact, :details, :ts)""",
                {**fields, "item_id": item_id, "ts": now()},
            )
            claim_id = cursor.lastrowid
        return self.get_claim(claim_id)  # type: ignore[arg-type,return-value]

    def get_claim(self, claim_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return dict(row) if row else None

    def list_claims(self, item_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM claims WHERE item_id = ? ORDER BY id", (item_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def approve_claim(self, item_id: int, claim_id: int) -> None:
        """Approve one claim, reject the other pending ones, mark the item claimed.

        All three updates happen in one transaction, so they succeed or fail together.
        """
        timestamp = now()
        with self.connect() as conn:
            conn.execute("UPDATE claims SET status = 'approved' WHERE id = ?", (claim_id,))
            conn.execute(
                """UPDATE claims SET status = 'rejected'
                   WHERE item_id = ? AND id != ? AND status = 'pending'""",
                (item_id, claim_id),
            )
            conn.execute(
                "UPDATE items SET status = 'claimed', updated_at = ? WHERE id = ?",
                (timestamp, item_id),
            )

    def reject_claim(self, claim_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE claims SET status = 'rejected' WHERE id = ?", (claim_id,))
