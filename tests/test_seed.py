from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.seed import seed


def test_seed_loads_sample_data_once(tmp_path):
    db = Database(str(tmp_path / "t.db"))

    assert seed(db) == 8
    assert seed(db) == 0  # second run is a no-op
    assert db.search_items()[1] == 8


def test_seeded_demo_token_can_manage_its_item(tmp_path):
    path = str(tmp_path / "t.db")
    seed(Database(path))

    with TestClient(create_app(path)) as client:
        charger = client.get("/api/items", params={"q": "charger"}).json()["items"][0]
        assert charger["pending_claims"] == 1
        claims = client.get(
            f"/api/items/{charger['id']}/claims", headers={"X-Manage-Token": "demo-token-1"}
        )
        assert claims.status_code == 200
        assert claims.json()[0]["contact"].endswith("@example.com")
