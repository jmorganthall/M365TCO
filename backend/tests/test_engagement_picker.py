"""The Open-engagement picker must never put another customer's details on a
shared screen: the list endpoint returns only id, name and last-updated, and
finds engagements by a typed name (docs/WALKTHROUGH.md §2)."""


def _make(client, name, **extra):
    eid = client.post("/api/engagements", json={"customer_name": name}).json()["id"]
    if extra:
        client.patch(f"/api/engagements/{eid}", json=extra)
    return eid


def test_list_rows_carry_no_customer_details(client):
    _make(client, "Picker Detail Co", notes="confidential merger notes",
          industry="Healthcare", website="https://example.test")
    rows = client.get("/api/engagements", params={"q": "Picker Detail"}).json()
    assert len(rows) == 1
    assert set(rows[0]) == {"id", "customer_name", "updated_at"}
    assert "confidential" not in str(rows)


def test_search_matches_anywhere_in_the_name_case_insensitively(client):
    _make(client, "Northwind Picker Traders")
    _make(client, "Contoso Picker Labs")
    names = [r["customer_name"] for r in
             client.get("/api/engagements", params={"q": "picker trad"}).json()]
    assert names == ["Northwind Picker Traders"]


def test_typed_wildcards_are_literal(client):
    _make(client, "Wild 100% Picker")
    _make(client, "Wild 1000 Picker")
    names = [r["customer_name"] for r in
             client.get("/api/engagements", params={"q": "100%"}).json()]
    assert names == ["Wild 100% Picker"]
    assert client.get("/api/engagements", params={"q": "Wild_1"}).json() == []


def test_limit_caps_the_result_count(client):
    for i in range(3):
        _make(client, f"Limit Picker {i}")
    assert len(client.get("/api/engagements",
                          params={"q": "Limit Picker", "limit": 2}).json()) == 2


def test_full_record_still_available_per_engagement(client):
    eid = _make(client, "Full Record Co", notes="kept for the engagement itself")
    assert client.get(f"/api/engagements/{eid}").json()["notes"] == "kept for the engagement itself"
