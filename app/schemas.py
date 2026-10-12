"""Request/response models. Pydantic rejects bad input with a 422 and a readable message."""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.constants import CATEGORIES, LOCATIONS

Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
Location = Literal[tuple(LOCATIONS)]  # type: ignore[valid-type]


class Kind(StrEnum):
    LOST = "lost"
    FOUND = "found"


class ItemStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RETURNED = "returned"


class ClaimStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Stripped(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ItemCreate(Stripped):
    kind: Kind
    title: str = Field(min_length=3, max_length=100, examples=["Black HP laptop charger"])
    description: str = Field(min_length=10, max_length=1000)
    category: Category  # type: ignore[valid-type]
    location: Location  # type: ignore[valid-type]
    event_date: date = Field(description="When the item was lost or found")

    @field_validator("event_date")
    @classmethod
    def not_in_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("event_date cannot be in the future")
        return value


class ItemOut(BaseModel):
    id: int
    kind: Kind
    title: str
    description: str
    category: str
    location: str
    event_date: str
    status: ItemStatus
    pending_claims: int
    created_at: str
    updated_at: str


class ItemCreated(ItemOut):
    manage_token: str = Field(
        description="Secret shown only once. Send it as X-Manage-Token to manage this listing."
    )


class ItemPage(BaseModel):
    items: list[ItemOut]
    total: int
    limit: int
    offset: int


class StatusUpdate(BaseModel):
    status: ItemStatus


class ClaimCreate(Stripped):
    claimant_name: str = Field(min_length=2, max_length=100)
    contact: str = Field(
        min_length=3,
        max_length=100,
        description="How the finder can reach you. Only visible to the listing's manager.",
    )
    details: str = Field(
        min_length=10,
        max_length=1000,
        description="Proof of ownership, e.g. something only the owner would know.",
    )


class ClaimReceipt(BaseModel):
    """What the claimant sees: no other claimants' data."""

    id: int
    item_id: int
    status: ClaimStatus
    created_at: str


class ClaimOut(ClaimReceipt):
    """What the listing's manager sees."""

    claimant_name: str
    contact: str
    details: str


class ClaimDecision(BaseModel):
    decision: Literal["approve", "reject"]


class Options(BaseModel):
    categories: list[str]
    locations: list[str]
