"""Historical Markdown preview resolves only current tenant metadata, without indexing it."""

from tests.conftest import register, workspace


async def create(client, wid, title, content=""):
    response = await client.post(f"/api/workspaces/{wid}/pages", json={"title": title, "content": content})
    assert response.status_code == 201, response.text
    return response.json()


async def test_revision_links_use_selected_snapshot_without_rewriting_current_index(client):
    await register(client)
    wid = (await workspace(client))["id"]
    target = await create(client, wid, "Target")
    page = await create(client, wid, "Source", "[[Target]] [[Missing]]")
    prefix = f"/api/workspaces/{wid}/pages/{page['id']}"
    saved = await client.patch(prefix, json={"version": 1, "content": "Current content has no links"})
    assert saved.status_code == 200
    before = (await client.get(prefix + "/links")).json()
    response = await client.get(prefix + "/revisions/1/links")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "resolution": "current_workspace",
        "outgoing": [
            {"raw_target": "Target", "target_id": target["id"], "state": "resolved"},
            {"raw_target": "Missing", "target_id": None, "state": "unresolved"},
        ],
    }
    assert (await client.get(prefix + "/links")).json() == before
    assert (await client.get(prefix)).json()["version"] == 2
    assert (await client.get(prefix + "/revisions/2/links")).json()["outgoing"] == []


async def test_revision_link_preview_rechecks_tenant_and_revision_scope(client, second_client):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create(client, wid, "Source", "[[Secret]]")
    await register(second_client, "other@example.com")
    other = (await workspace(second_client))["id"]
    secret = await create(second_client, other, "Secret")
    path = f"/api/workspaces/{wid}/pages/{page['id']}/revisions/1/links"
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["outgoing"] == [{"raw_target": "Secret", "target_id": None, "state": "unresolved"}]
    assert secret["id"] not in response.text
    assert (await second_client.get(path)).status_code == 404
    assert (await client.get(path.replace("/revisions/1/", "/revisions/99/"))).status_code == 404
    assert (
        await second_client.get(f"/api/workspaces/{other}/pages/{page['id']}/revisions/1/links")
    ).status_code == 404


async def test_revision_link_preview_respects_id_aliases_ambiguity_deletion_and_code(client):
    await register(client)
    wid = (await workspace(client))["id"]
    target = await create(client, wid, "Target")
    deleted = await create(client, wid, "Deleted")
    assert (await client.delete(f"/api/workspaces/{wid}/pages/{deleted['id']}?version=1")).status_code == 200
    await create(client, wid, "Same")
    await create(client, wid, "Same")
    source = await create(
        client,
        wid,
        "Source",
        f"[[id:{target['id']}|Alias]] [[Same]] [[id:{deleted['id']}]] `[[Code]]`",
    )
    response = await client.get(f"/api/workspaces/{wid}/pages/{source['id']}/revisions/1/links")
    assert response.status_code == 200, response.text
    links = {link["raw_target"]: link for link in response.json()["outgoing"]}
    assert links[f"id:{target['id']}"]["target_id"] == target["id"]
    assert links[f"id:{target['id']}"]["state"] == "resolved"
    assert links["Same"]["state"] == "ambiguous"
    assert links["Same"]["target_id"] is None
    assert links[f"id:{deleted['id']}"]["state"] == "deleted"
    assert "Code" not in links
