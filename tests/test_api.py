from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TODAY = date.today().isoformat()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path)) as test_client:
        yield test_client


def item_payload(**overrides) -> dict:
    return {
        "kind": "found",
        "title": "Blue water bottle",
        "description": "Steel bottle with a sticker of a cat on it",
        "category": "bottle",
        "location": "Library",
        "event_date": TODAY,
        **overrides,
    }


def claim_payload(**overrides) -> dict:
    return {
        "claimant_name": "Asha",
        "contact": "asha@example.com",
        "details": "It has a cat sticker and a dent near the cap",
        **overrides,
    }


def report(client, **overrides) -> dict:
    response = client.post("/api/items", json=item_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth(item: dict) -> dict:
    return {"X-Manage-Token": item["manage_token"]}


def claim(client, item: dict, **overrides) -> dict:
    response = client.post(f"/api/items/{item['id']}/claims", json=claim_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --- reporting -----------------------------------------------------------------


def test_report_item_returns_listing_and_one_time_token(client):
    item = report(client)

    assert item["id"] == 1
    assert item["status"] == "open"
    assert item["pending_claims"] == 0
    assert len(item["manage_token"]) >= 30

    fetched = client.get(f"/api/items/{item['id']}").json()
    assert "manage_token" not in fetched  # never shown again
    assert "manage_token_hash" not in fetched


def test_tokens_are_unique(client):
    assert report(client)["manage_token"] != report(client)["manage_token"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "stolen"},
        {"title": "ab"},
        {"title": "   ab   "},  # too short after stripping
        {"title": "x" * 101},
        {"description": "short"},
        {"category": "spaceship"},
        {"location": "Mars"},
        {"event_date": "not-a-date"},
        {"event_date": (date.today() + timedelta(days=1)).isoformat()},
    ],
)
def test_invalid_item_is_rejected(client, overrides):
    response = client.post("/api/items", json=item_payload(**overrides))
    assert response.status_code == 422
    assert client.get("/api/items").json()["total"] == 0


def test_missing_field_is_rejected(client):
    payload = item_payload()
    del payload["location"]
    response = client.post("/api/items", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "location"]


def test_options_lists_categories_and_locations(client):
    options = client.get("/api/options").json()
    assert "electronics" in options["categories"]
    assert "Library" in options["locations"]


def test_unknown_item_is_404(client):
    assert client.get("/api/items/999").status_code == 404
    assert client.post("/api/items/999/claims", json=claim_payload()).status_code == 404


# --- search and filters ------------------------------------------------------


@pytest.fixture
def catalogue(client):
    report(client, title="Black HP laptop charger", category="electronics", location="Labs")
    report(client, kind="lost", title="Student ID card", category="id-card", location="Cafeteria",
           description="ID card in a red holder, name starts with R")
    report(client, title="Red umbrella", category="other", location="Library",
           description="Foldable umbrella, red with white dots")
    return client


def titles(response) -> list[str]:
    return [item["title"] for item in response.json()["items"]]


def test_list_all_newest_first(catalogue):
    response = catalogue.get("/api/items")
    assert response.json()["total"] == 3
    assert titles(response) == ["Red umbrella", "Student ID card", "Black HP laptop charger"]


def test_keyword_search_matches_title_and_description_case_insensitive(catalogue):
    assert titles(catalogue.get("/api/items", params={"q": "CHARGER"})) == ["Black HP laptop charger"]
    assert titles(catalogue.get("/api/items", params={"q": "holder"})) == ["Student ID card"]
    # "red" appears in the umbrella title and in the ID card description
    assert set(titles(catalogue.get("/api/items", params={"q": "red"}))) == {
        "Red umbrella",
        "Student ID card",
    }


def test_every_keyword_must_match(catalogue):
    assert titles(catalogue.get("/api/items", params={"q": "red umbrella"})) == ["Red umbrella"]
    assert titles(catalogue.get("/api/items", params={"q": "red charger"})) == []


def test_like_wildcards_are_literal(catalogue):
    assert catalogue.get("/api/items", params={"q": "%"}).json()["total"] == 0
    assert catalogue.get("/api/items", params={"q": "_"}).json()["total"] == 0


def test_blank_query_returns_everything(catalogue):
    assert catalogue.get("/api/items", params={"q": "   "}).json()["total"] == 3


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"kind": "lost"}, ["Student ID card"]),
        ({"category": "electronics"}, ["Black HP laptop charger"]),
        ({"location": "Library"}, ["Red umbrella"]),
        ({"kind": "found", "location": "Labs"}, ["Black HP laptop charger"]),
        ({"kind": "lost", "location": "Labs"}, []),
        ({"q": "red", "kind": "found"}, ["Red umbrella"]),
    ],
)
def test_filters(catalogue, params, expected):
    assert titles(catalogue.get("/api/items", params=params)) == expected


def test_status_filter(catalogue):
    item = report(catalogue, title="Lost keys found")
    catalogue.patch(f"/api/items/{item['id']}/status", json={"status": "returned"}, headers=auth(item))

    assert titles(catalogue.get("/api/items", params={"status": "returned"})) == ["Lost keys found"]
    assert catalogue.get("/api/items", params={"status": "open"}).json()["total"] == 3


def test_pagination(catalogue):
    page = catalogue.get("/api/items", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3
    assert [i["title"] for i in page["items"]] == ["Black HP laptop charger"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"category": "spaceship"},
        {"location": "Mars"},
        {"status": "lost"},
        {"q": "x" * 101},
    ],
)
def test_invalid_search_params_are_rejected(client, params):
    assert client.get("/api/items", params=params).status_code == 422


# --- status updates (manage token) -------------------------------------------


def test_status_update_requires_token(client):
    item = report(client)
    url = f"/api/items/{item['id']}/status"

    assert client.patch(url, json={"status": "returned"}).status_code == 401
    assert client.patch(url, json={"status": "returned"}, headers={"X-Manage-Token": "wrong"}).status_code == 403

    other = report(client)
    assert client.patch(url, json={"status": "returned"}, headers=auth(other)).status_code == 403
    assert client.get(f"/api/items/{item['id']}").json()["status"] == "open"


def test_status_transitions(client):
    item = report(client)
    url = f"/api/items/{item['id']}/status"

    assert client.patch(url, json={"status": "claimed"}, headers=auth(item)).json()["status"] == "claimed"
    assert client.patch(url, json={"status": "open"}, headers=auth(item)).json()["status"] == "open"
    assert client.patch(url, json={"status": "returned"}, headers=auth(item)).json()["status"] == "returned"

    # returned is final
    response = client.patch(url, json={"status": "open"}, headers=auth(item))
    assert response.status_code == 409
    assert "returned" in response.json()["detail"]


def test_invalid_status_value(client):
    item = report(client)
    response = client.patch(f"/api/items/{item['id']}/status", json={"status": "gone"}, headers=auth(item))
    assert response.status_code == 422


# --- claims ------------------------------------------------------------------


def test_submit_claim_hides_contact_from_claimant(client):
    item = report(client)
    receipt = claim(client, item)

    assert receipt["status"] == "pending"
    assert "contact" not in receipt and "details" not in receipt
    assert client.get(f"/api/items/{item['id']}").json()["pending_claims"] == 1


def test_claims_visible_only_with_token(client):
    item = report(client)
    claim(client, item)
    url = f"/api/items/{item['id']}/claims"

    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Manage-Token": "nope"}).status_code == 403

    claims = client.get(url, headers=auth(item)).json()
    assert claims[0]["contact"] == "asha@example.com"
    assert claims[0]["claimant_name"] == "Asha"


@pytest.mark.parametrize(
    "overrides",
    [{"claimant_name": "A"}, {"contact": "ab"}, {"details": "mine"}, {"details": "x" * 1001}],
)
def test_invalid_claim_is_rejected(client, overrides):
    item = report(client)
    response = client.post(f"/api/items/{item['id']}/claims", json=claim_payload(**overrides))
    assert response.status_code == 422


def test_cannot_claim_a_lost_item(client):
    item = report(client, kind="lost")
    response = client.post(f"/api/items/{item['id']}/claims", json=claim_payload())
    assert response.status_code == 400


def test_full_claim_workflow(client):
    """Found item -> two claims -> approve one -> others rejected -> mark returned."""
    item = report(client)
    real_owner = claim(client, item, claimant_name="Asha")
    impostor = claim(client, item, claimant_name="Ravi", contact="ravi@example.com")
    claims_url = f"/api/items/{item['id']}/claims"

    approved = client.patch(f"{claims_url}/{real_owner['id']}", json={"decision": "approve"}, headers=auth(item))
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    statuses = {c["claimant_name"]: c["status"] for c in client.get(claims_url, headers=auth(item)).json()}
    assert statuses == {"Asha": "approved", "Ravi": "rejected"}

    listing = client.get(f"/api/items/{item['id']}").json()
    assert listing["status"] == "claimed"
    assert listing["pending_claims"] == 0

    # no new claims once claimed
    assert client.post(claims_url, json=claim_payload()).status_code == 409
    # impostor's claim can't be revived
    assert client.patch(f"{claims_url}/{impostor['id']}", json={"decision": "approve"}, headers=auth(item)).status_code == 409

    returned = client.patch(f"/api/items/{item['id']}/status", json={"status": "returned"}, headers=auth(item))
    assert returned.json()["status"] == "returned"


def test_reject_claim_keeps_item_open(client):
    item = report(client)
    c = claim(client, item)
    response = client.patch(f"/api/items/{item['id']}/claims/{c['id']}", json={"decision": "reject"}, headers=auth(item))

    assert response.json()["status"] == "rejected"
    assert client.get(f"/api/items/{item['id']}").json()["status"] == "open"


def test_claim_decision_requires_matching_item_and_token(client):
    item_a, item_b = report(client), report(client)
    c = claim(client, item_a)

    # claim belongs to item A; using item B's URL and token must not work
    assert client.patch(f"/api/items/{item_b['id']}/claims/{c['id']}", json={"decision": "approve"}, headers=auth(item_b)).status_code == 404
    assert client.patch(f"/api/items/{item_a['id']}/claims/{c['id']}", json={"decision": "approve"}, headers=auth(item_b)).status_code == 403
    assert client.patch(f"/api/items/{item_a['id']}/claims/999", json={"decision": "approve"}, headers=auth(item_a)).status_code == 404
    assert client.patch(f"/api/items/{item_a['id']}/claims/{c['id']}", json={"decision": "maybe"}, headers=auth(item_a)).status_code == 422


# --- persistence --------------------------------------------------------------


def test_data_survives_restart(db_path):
    with TestClient(create_app(db_path)) as first:
        item = report(first)
        claim(first, item)

    with TestClient(create_app(db_path)) as second:
        assert second.get("/api/items").json()["total"] == 1
        claims = second.get(f"/api/items/{item['id']}/claims", headers=auth(item)).json()
        assert len(claims) == 1  # token still works after restart
