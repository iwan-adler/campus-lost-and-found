"""Fixed choices shared by validation, the database and the web UI."""

CATEGORIES = [
    "electronics",
    "id-card",
    "keys",
    "wallet",
    "bag",
    "books",
    "clothing",
    "bottle",
    "jewellery",
    "other",
]

LOCATIONS = [
    "Library",
    "Academic Block",
    "Cafeteria",
    "Hostel",
    "Sports Complex",
    "Auditorium",
    "Labs",
    "Parking",
    "Bus Stop",
    "Other",
]

# Allowed status changes. "claimed" means a claim was approved and handover is pending.
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "open": {"claimed", "returned"},
    "claimed": {"open", "returned"},  # back to open if the handover falls through
    "returned": set(),  # final
}
