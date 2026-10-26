async def test_readiness_and_request_correlation(client):
    response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert len(response.headers["x-request-id"]) == 36


async def test_openapi_and_docs_are_reachable_through_same_origin_api_proxy(client):
    docs = await client.get("/api/docs")
    assert docs.status_code == 200
    schema = await client.get("/api/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/workspaces/{workspace_id}/pages/{page_id}" in paths
    assert "/api/workspaces/{workspace_id}/imports" in paths
