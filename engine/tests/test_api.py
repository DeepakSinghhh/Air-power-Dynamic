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
    # KPI deltas now compare with the plan before the change, not the (fog-free) manual baseline.
    assert after["reference_label"] == "vs before last retask"
    assert after["baseline_kpis"] == s["kpis"]


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


def test_coa_compare_adopt_and_intent_persists():
    s = _planned()
    r = client.post("/api/coa?time_limit=4").json()
    ids = [c["id"] for c in r["coas"]]
    assert ids == ["effect", "risk", "defend"]
    by = {c["id"]: c for c in r["coas"]}
    assert by["risk"]["metrics"]["expected_losses"] <= by["effect"]["metrics"]["expected_losses"]
    assert by["defend"]["metrics"]["munitions_total"] <= by["effect"]["metrics"]["munitions_total"]
    prop = client.post("/api/coa/risk/propose").json()
    assert prop["world"]["intent"]["name"] == "Min risk"
    assert client.get("/api/state").json()["version"] == s["version"]  # nothing changes until approved
    after = client.post(f"/api/retask/{prop['id']}/approve").json()
    assert after["world"]["intent"]["name"] == "Min risk"
    # Later retasks keep honouring the adopted intent.
    tst = next(p for p in client.get("/api/presets?at=0").json() if p["id"] == "tst")
    nxt = client.post("/api/retask/propose", json={"events": tst["events"], "time_limit": 4}).json()
    assert nxt["world"]["intent"]["name"] == "Min risk"
    # COAs computed for an older plan cannot be adopted.
    assert client.post("/api/coa/defend/propose").status_code == 409


def test_robustness_spares_proposal_and_policy_persists():
    s = _planned()
    r = client.get("/api/robustness?runs=500").json()
    assert r["hardened"] and r["hardened"]["spares"] > 0
    assert r["hardened"]["mean"] >= r["current"]["mean"]
    assert sum(r["current"]["hist"]) == 500
    prop = client.post("/api/robustness/propose").json()
    assert prop["diff"]["aircraft_changes"] == 0 and prop["diff"]["spare_changes"] > 0
    assert client.get("/api/state").json()["version"] == s["version"]
    after = client.post(f"/api/retask/{prop['id']}/approve").json()
    assert after["world"]["spare_policy"] and after["kpis"]["spares"] > 0
    assert after["kpis"]["expected_value"] >= s["kpis"]["expected_value"]
    tst = next(p for p in client.get("/api/presets?at=0").json() if p["id"] == "tst")
    nxt = client.post("/api/retask/propose", json={"events": tst["events"], "time_limit": 4}).json()
    assert nxt["kpis"]["spares"] > 0  # spares survive later retasks
    assert client.get("/api/robustness?runs=200").json()["hardened"] is None
    assert client.post("/api/robustness/propose").status_code == 409


def test_readiness_board_and_feed_freshness():
    _planned()
    r = client.get("/api/readiness?at=0").json()
    assert r["bases"] and len(r["hours"]) == 24
    hlw = next(b for b in r["bases"] if b["base"] == "HLW")
    assert all(0 <= v <= hlw["crews_available"] for v in hlw["crews_fit_hourly"])
    assert all(w["left"] == w["stock"] - w["planned"] for w in hlw["weapons"])
    feeds = {f["id"]: f for f in r["feeds"]}
    assert feeds["maintenance"]["as_of"] < 0
    # An aircraft going unserviceable refreshes the maintenance feed (seen in the proposal's world).
    tail = next(iter(client.get("/api/state").json()["world"]["aircraft"]))
    client.post("/api/retask/propose", json={"events": [{"kind": "aircraft_down", "at": 60, "tails": [tail]}],
                                             "time_limit": 3})
    p = client.get("/api/readiness?proposal=true").json()
    assert {f["id"]: f for f in p["feeds"]}["maintenance"]["as_of"] == 60


def test_copilot_answers_proposes_and_logs():
    s = _planned()
    st = client.get("/api/copilot/status").json()
    assert "why_not" in st["tools"]
    r = client.post("/api/copilot", json={"text": "status"}).json()
    assert r["intent"] == "status" and r["router"] == "rules" and "missions planned" in r["text"]
    base = next(iter(s["world"]["bases"].values()))["name"]
    r = client.post("/api/copilot", json={"text": f"what if {base} fogs in from 05:00 to 09:30"}).json()
    assert r["intent"] == "close_base" and r["proposal"] and "Proposal ready" in r["text"]
    assert client.get("/api/state").json()["version"] == s["version"]  # nothing changes until approved
    client.post(f"/api/retask/{r['proposal']['id']}/reject")
    log = client.get("/api/copilot/log").json()
    assert [e["intent"] for e in log[-2:]] == ["status", "close_base"] and log[-1]["proposal"]
