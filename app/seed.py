"""Load sample listings into an empty database.

Usage:  python -m app.seed [examples/sample_items.json]

Sample items carry fixed, publicly known demo tokens (see the JSON file),
so never seed a database that holds real listings.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from app.db import Database
from app.main import DEFAULT_DB_PATH, hash_token
from app.schemas import ClaimCreate, ItemCreate

DEFAULT_SAMPLE = Path(__file__).parent.parent / "examples" / "sample_items.json"


def seed(db: Database, sample_path: Path = DEFAULT_SAMPLE) -> int:
    """Insert sample items (validated like API input). Returns how many were added."""
    db.init()
    if db.search_items(limit=1)[1] > 0:
        return 0  # never mix sample data into an existing database

    entries = json.loads(sample_path.read_text(encoding="utf-8"))
    # Oldest first, so the newest sample ends up at the top of the list.
    for entry in sorted(entries, key=lambda e: -e["days_ago"]):
        item = ItemCreate.model_validate(
            {**entry, "event_date": date.today() - timedelta(days=entry["days_ago"])}
        )
        fields = item.model_dump()
        fields["event_date"] = item.event_date.isoformat()
        created = db.create_item(fields, hash_token(entry["manage_token"]))
        for claim in entry.get("claims", []):
            db.create_claim(created["id"], ClaimCreate.model_validate(claim).model_dump())
    return len(entries)


def main() -> int:
    db = Database(os.getenv("LOST_FOUND_DB", DEFAULT_DB_PATH))
    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE
    added = seed(db, sample)
    if added:
        print(f"Added {added} sample items to {db.path} (demo tokens are in {sample})")
    else:
        print(f"{db.path} already has items; nothing added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
