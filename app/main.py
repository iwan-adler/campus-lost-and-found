"""FastAPI application: JSON API under /api, static web UI at /."""

import hashlib
import hmac
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles

from app.constants import CATEGORIES, LOCATIONS, STATUS_TRANSITIONS
from app.db import Database
from app.schemas import (
    Category,
    ClaimCreate,
    ClaimDecision,
    ClaimOut,
    ClaimReceipt,
    ItemCreate,
    ItemCreated,
    ItemOut,
    ItemPage,
    ItemStatus,
    Kind,
    Location,
    Options,
    StatusUpdate,
)

DEFAULT_DB_PATH = "lost_and_found.db"
STATIC_DIR = Path(__file__).parent.parent / "static"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_app(db_path: str | None = None) -> FastAPI:
    db = Database(db_path or os.getenv("LOST_FOUND_DB", DEFAULT_DB_PATH))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.init()
        yield

    app = FastAPI(
        title="Campus Lost & Found",
        description="Report, search and claim lost or found items on campus.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db = db

    # --- shared helpers / dependencies -------------------------------------

    def item_or_404(item_id: int) -> dict[str, Any]:
        item = db.get_item(item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Item {item_id} not found")
        return item

    def managed_item(
        item_id: int,
        x_manage_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Only the person who reported the item (holder of its token) may manage it."""
        item = item_or_404(item_id)
        if not x_manage_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-Manage-Token header required")
        stored_hash = db.get_token_hash(item_id) or ""
        # compare_digest takes the same time whether or not the strings match,
        # so an attacker can't guess the hash one character at a time.
        if not hmac.compare_digest(hash_token(x_manage_token), stored_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid manage token for this item")
        return item

    ManagedItem = Annotated[dict[str, Any], Depends(managed_item)]

    # --- items -----------------------------------------------------------

    @app.get("/api/options", response_model=Options, tags=["items"])
    def options() -> dict[str, list[str]]:
        """Valid categories and locations (used to build the UI's dropdowns)."""
        return {"categories": CATEGORIES, "locations": LOCATIONS}

    @app.post(
        "/api/items",
        response_model=ItemCreated,
        status_code=status.HTTP_201_CREATED,
        tags=["items"],
    )
    def report_item(payload: ItemCreate) -> dict[str, Any]:
        """Report a lost or found item. The response contains a one-time manage token."""
        token = secrets.token_urlsafe(24)
        fields = payload.model_dump()
        fields["event_date"] = payload.event_date.isoformat()
        item = db.create_item(fields, hash_token(token))
        return {**item, "manage_token": token}

    @app.get("/api/items", response_model=ItemPage, tags=["items"])
    def search_items(
        q: Annotated[str | None, Query(max_length=100, description="Keywords")] = None,
        kind: Kind | None = None,
        category: Category | None = None,  # type: ignore[valid-type]
        location: Location | None = None,  # type: ignore[valid-type]
        status_: Annotated[ItemStatus | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Search by keyword and filter. Newest first."""
        items, total = db.search_items(
            q=q.strip() if q else None,
            kind=kind,
            category=category,
            location=location,
            status=status_,
            limit=limit,
            offset=offset,
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/items/{item_id}", response_model=ItemOut, tags=["items"])
    def get_item(item_id: int) -> dict[str, Any]:
        return item_or_404(item_id)

    @app.patch("/api/items/{item_id}/status", response_model=ItemOut, tags=["manage"])
    def update_status(payload: StatusUpdate, item: ManagedItem) -> dict[str, Any]:
        """Change a listing's status. Requires X-Manage-Token."""
        current, new = item["status"], payload.status.value
        if new != current and new not in STATUS_TRANSITIONS[current]:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Cannot change status from '{current}' to '{new}'"
            )
        return db.set_item_status(item["id"], new)

    # --- claims ----------------------------------------------------------

    @app.post(
        "/api/items/{item_id}/claims",
        response_model=ClaimReceipt,
        status_code=status.HTTP_201_CREATED,
        tags=["claims"],
    )
    def submit_claim(item_id: int, payload: ClaimCreate) -> dict[str, Any]:
        """Claim a found item as yours. Your contact details are shown only to the finder."""
        item = item_or_404(item_id)
        if item["kind"] != Kind.FOUND:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Claims can only be made on found items"
            )
        if item["status"] != ItemStatus.OPEN:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Item is '{item['status']}' and not accepting claims"
            )
        return db.create_claim(item_id, payload.model_dump())

    @app.get("/api/items/{item_id}/claims", response_model=list[ClaimOut], tags=["manage"])
    def list_claims(item: ManagedItem) -> list[dict[str, Any]]:
        """All claims on your listing, including contact details. Requires X-Manage-Token."""
        return db.list_claims(item["id"])

    @app.patch(
        "/api/items/{item_id}/claims/{claim_id}", response_model=ClaimOut, tags=["manage"]
    )
    def decide_claim(claim_id: int, payload: ClaimDecision, item: ManagedItem) -> dict[str, Any]:
        """Approve or reject a pending claim. Requires X-Manage-Token.

        Approving marks the item 'claimed' and rejects the other pending claims.
        """
        claim = db.get_claim(claim_id)
        if claim is None or claim["item_id"] != item["id"]:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Claim {claim_id} not found")
        if claim["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is already {claim['status']}")
        if payload.decision == "approve":
            if item["status"] != ItemStatus.OPEN:
                raise HTTPException(status.HTTP_409_CONFLICT, f"Item is already {item['status']}")
            db.approve_claim(item["id"], claim_id)
        else:
            db.reject_claim(claim_id)
        return db.get_claim(claim_id)  # type: ignore[return-value]

    # Web UI. Mounted last so /api routes take priority.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
