from fastapi.testclient import TestClient

from sarthi.api import app

client = TestClient(app)


def _planned():
    assert client.post("/api/scenario", json={"seed": 3}).status_code == 200
    r = client.post("/api/plan?time_limit=5")
    assert r.status_code == 200
    return r.json()


def test_plan_state_and_hazard():
    s = _planned()
    assert s["plan"]["assignments"] and s["kpis"]["missions_planned"] > 0
    assert set(s["envelopes"]) == set(s["world"]["threats"])
    h = client.get("/api/hazard").json()
    assert h["nlat"] > 10 and h["nlon"] > 10 and len(h["data"]) > 100


def test_propose_then_approve_commits():
    s = _planned()
    presets = client.get("/api/presets?at=120").json()
    fog = next(p for p in presets if p["id"] == "fog")
    prop = client.post("/api/retask/propose", json={"events": fog["events"], "time_limit": 5}).json()
    assert prop["diff"]["aircraft_changes"] <= prop["naive_diff"]["aircraft_changes"]
    # Proposal is not live until approved.
    assert client.get("/api/state").json()["version"] == s["version"]
    after = client.post(f"/api/retask/{prop['id']}/approve").json()
    assert after["version"] == s["version"] + 1
    assert after["world"]["now"] == 120
    assert after["history"][-1]["decision"] == "approved"


def test_reject_and_stale_proposals():
    _planned()
    tst = next(p for p in client.get("/api/presets?at=60").json() if p["id"] == "tst")
    prop = client.post("/api/retask/propose", json={"events": tst["events"], "time_limit": 5}).json()
    assert client.post(f"/api/retask/{prop['id']}/reject").status_code == 200
    assert client.post(f"/api/retask/{prop['id']}/approve").status_code == 404
    prop2 = client.post("/api/retask/propose", json={"events": tst["events"], "time_limit": 5}).json()
    client.post("/api/plan?time_limit=3")  # plan changes underneath the proposal
    assert client.post(f"/api/retask/{prop2['id']}/approve").status_code in (404, 409)


def test_met_snapshot_closures_follow_threshold():
    _planned()
    hi = client.get("/api/met?source=snapshot&threshold=0.5&at=0").json()
    assert hi["forecast"]["source"] == "snapshot" and hi["forecast"]["bases"]
    assert hi["events"] and all(e["kind"] == "base_closure" and e["probability"] >= 0.5 for e in hi["events"])
    lo = client.get("/api/met?source=snapshot&threshold=0.3&at=0").json()
    assert sum(w["end"] - w["start"] for w in lo["windows"]) >= sum(w["end"] - w["start"] for w in hi["windows"])
