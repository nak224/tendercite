from test_analysis import create_run


def test_review_history_retains_original_and_effective_values(client, retrieval):
    run = create_run(client).json()
    original = run["findings"][0]
    path = "/api/v1/findings/" + original["id"]
    modified = {
        "statement": "Two comparable references required",
        "category": "MUST",
        "requirement_type": "REFERENCE",
    }
    for action, value, status in [
        ("CONFIRM", None, "CONFIRMED"),
        ("MODIFY", modified, "MODIFIED"),
        ("REJECT", None, "REJECTED"),
        ("CONFIRM", None, "CONFIRMED"),
    ]:
        request = {"action": action, "comment": "Checked by a human"}
        if value:
            request["reviewed_value"] = value
        response = client.post(path + "/reviews", json=request)
        assert response.status_code == 201, response.text
        assert response.json()["original_value"]["statement"] == original["statement"]
        assert client.get(path).json()["review_status"] == status
    finding = client.get(path).json()
    assert finding["statement"] == original["statement"]
    assert finding["effective_value"] == modified
    history = client.get(path + "/reviews").json()
    assert len(history) == 4
    assert history[1]["previous_value"]["statement"] == original["statement"]
    assert history[2]["previous_value"] == modified
    assert all(e["created_at"] and e["comment"] for e in history)
    assert client.get("/api/v1/analyses/" + run["id"]).json()["findings"][0] == original
    assert client.get("/api/v1/findings").json()[0]["effective_value"] == modified


def test_review_rejects_incomplete_modification(client, retrieval):
    fid = create_run(client).json()["findings"][0]["id"]
    assert (
        client.post("/api/v1/findings/" + fid + "/reviews", json={"action": "MODIFY"}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/findings/unknown/reviews", json={"action": "CONFIRM"}).status_code
        == 404
    )
