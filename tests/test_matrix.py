from test_analysis import create_run


def test_matrix_is_human_managed_and_invalidates_stale_decisions(client, retrieval):
    finding = create_run(client).json()["findings"][0]
    path = "/api/v1/findings/" + finding["id"] + "/reviews"
    matrix_path = "/api/v1/go-no-go/" + finding["id"]
    assignment = {"status": "FULFILLED", "rationale": "Bidder supplied two project references"}
    assert client.get("/api/v1/go-no-go").json() == []
    assert client.patch(matrix_path, json=assignment).status_code == 409
    client.post(path, json={"action": "CONFIRM"})
    row = client.get("/api/v1/go-no-go").json()[0]
    assert row["status"] == "CLARIFICATION_NEEDED"
    assert row["evidence"] == finding["evidence"]
    for status in ["FULFILLED", "PARTIAL", "MISSING", "CLARIFICATION_NEEDED", "NOT_APPLICABLE"]:
        assignment["status"] = status
        result = client.patch(matrix_path, json=assignment)
        assert result.status_code == 200
        assert client.get("/api/v1/go-no-go").json()[0]["status"] == status
    client.post(
        path,
        json={
            "action": "MODIFY",
            "reviewed_value": {
                "statement": "Three references required",
                "category": "MUST",
                "requirement_type": "REFERENCE",
            },
        },
    )
    assert client.get("/api/v1/go-no-go").json()[0]["status"] == "CLARIFICATION_NEEDED"
    client.post(path, json={"action": "REJECT"})
    assert client.get("/api/v1/go-no-go").json() == []
    assert client.patch(matrix_path, json=assignment).status_code == 409
    assert client.patch("/api/v1/go-no-go/unknown", json=assignment).status_code == 404
