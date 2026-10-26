import asyncio
import os

import pytest
from sqlalchemy import select

from app.models import AuditLog, PageRevision
from tests.conftest import register, workspace


async def create_page(client, wid, title="Guide", content="initial", **values):
    response = await client.post(
        f"/api/workspaces/{wid}/pages",
        json={
            "title": title,
            "content": content,
            **values,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def setup(client):
    await register(client)
    wid = (await workspace(client))["id"]
    return wid, await create_page(client, wid)


async def test_pages_require_authentication(client):
    response = await client.get("/api/workspaces/unknown/pages")
    assert response.status_code == 401


async def test_save_conflict_and_restore_are_atomic(client, session_factory):
    wid, page = await setup(client)
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    saved = await client.patch(url, json={"version": 1, "content": "winner"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    stale = await client.patch(url, json={"version": 1, "content": "loser"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["content"] == "winner"
    revisions = (await client.get(url + "/revisions")).json()
    assert [r["version"] for r in revisions] == [2, 1]
    restored = await client.post(url + "/revisions/1/restore", json={"version": 2})
    assert restored.status_code == 200, restored.text
    assert (restored.json()["version"], restored.json()["content"]) == (3, "initial")
    assert (await client.post(url + "/revisions/1/restore", json={"version": 2})).status_code == 409
    async with session_factory() as db:
        versions = (
            await db.scalars(select(PageRevision.version).where(PageRevision.page_id == page["id"]))
        ).all()
        assert sorted(versions) == [1, 2, 3]
        actions = (await db.scalars(select(AuditLog.action).where(AuditLog.entity_id == page["id"]))).all()
        assert actions.count("page.update") == 1
        assert actions.count("page.restore_revision") == 1


async def test_tenant_scope_applies_to_every_page_surface(client, second_client):
    wid, page = await setup(client)
    await register(second_client, "outsider@example.com")
    other = (await workspace(second_client, "Other"))["id"]
    # Knowing both workspace and page IDs does not grant membership.
    for suffix in ["", "/revisions", "/revisions/1", "/links", "/export"]:
        assert (
            await second_client.get(f"/api/workspaces/{wid}/pages/{page['id']}{suffix}")
        ).status_code == 404
    # Member of another workspace cannot substitute its workspace ID either.
    for suffix in ["", "/links", "/export", "/revisions"]:
        assert (
            await second_client.get(f"/api/workspaces/{other}/pages/{page['id']}{suffix}")
        ).status_code == 404
    assert (await second_client.get(f"/api/workspaces/{wid}/search?q=initial")).status_code == 404
    assert (await second_client.get(f"/api/workspaces/{other}/search?q=initial")).json() == []
    assert (
        await second_client.patch(
            f"/api/workspaces/{other}/pages/{page['id']}", json={"version": 1, "content": "stolen"}
        )
    ).status_code == 404


async def test_viewer_cannot_mutate_pages(client, second_client):
    wid, page = await setup(client)
    await register(second_client, "viewer@example.com")
    invite = await client.post(
        f"/api/workspaces/{wid}/invitations", json={"email": "viewer@example.com", "role": "viewer"}
    )
    assert invite.status_code == 201, invite.text
    assert (
        await second_client.post("/api/invitations/accept", json={"token": invite.json()["token"]})
    ).status_code == 200
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    assert (await second_client.get(url)).status_code == 200
    assert (await second_client.patch(url, json={"version": 1, "content": "bad"})).status_code == 403
    assert (await second_client.delete(url + "?version=1")).status_code == 403
    assert (await second_client.post(url + "/revisions/1/restore", json={"version": 1})).status_code == 403
    assert (
        await second_client.post(f"/api/workspaces/{wid}/pages", json={"title": "bad"})
    ).status_code == 403


async def test_tree_cycle_and_foreign_parent_rejected(client):
    wid, parent = await setup(client)
    child = await create_page(client, wid, title="Child", parent_id=parent["id"])
    url = f"/api/workspaces/{wid}/pages/{parent['id']}"
    assert (await client.patch(url, json={"version": 1, "parent_id": child["id"]})).status_code == 422
    other = (await workspace(client, "Other"))["id"]
    foreign = await create_page(client, other, title="Foreign")
    assert (await client.patch(url, json={"version": 1, "parent_id": foreign["id"]})).status_code == 404
    # A nonempty parent cannot disappear and strand visible descendants.
    assert (await client.delete(url + "?version=1")).status_code == 409


async def test_links_pin_identity_and_reconcile_deleted_ambiguous_targets(client):
    await register(client)
    wid = (await workspace(client))["id"]
    target = await create_page(client, wid, "FastAPI")
    source = await create_page(client, wid, "Source", "[[FastAPI]] [[missing]]")
    base = f"/api/workspaces/{wid}/pages"
    links = (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"]
    assert {link["raw_target"]: link["state"] for link in links} == {
        "FastAPI": "resolved",
        "missing": "unresolved",
    }
    await client.patch(f"{base}/{target['id']}", json={"version": 1, "title": "Renamed"})
    pinned = (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"][0]
    assert pinned["target_id"] == target["id"]
    assert pinned["target_title"] == "Renamed"
    assert (await client.delete(f"{base}/{target['id']}?version=2")).status_code == 200
    assert (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"][0]["state"] == "deleted"
    assert (await client.post(f"{base}/{target['id']}/undelete", json={"version": 3})).status_code == 200
    assert (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"][0]["state"] == "resolved"
    await create_page(client, wid, "missing")
    assert (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"][1]["state"] == "resolved"
    await create_page(client, wid, "Duplicate")
    await create_page(client, wid, "Duplicate")
    new = await create_page(client, wid, "Ambiguity", "[[Duplicate]]")
    assert (await client.get(f"{base}/{new['id']}/links")).json()["outgoing"][0]["state"] == "ambiguous"
    backlinks = (await client.get(f"{base}/{target['id']}/links")).json()["backlinks"]
    assert backlinks[0]["source_id"] == source["id"]
    await client.delete(f"{base}/{source['id']}?version=1")
    assert (await client.get(f"{base}/{target['id']}/links")).json()["backlinks"] == []


async def test_foreign_title_or_stable_id_never_resolves(client):
    await register(client)
    first = (await workspace(client))["id"]
    foreign = await create_page(client, first, "Secret")
    second = (await workspace(client, "Public"))["id"]
    source = await create_page(client, second, "Source", f"[[Secret]] [[id:{foreign['id']}|Secret]]")
    links = (await client.get(f"/api/workspaces/{second}/pages/{source['id']}/links")).json()["outgoing"]
    assert all(link["state"] == "unresolved" and link["target_id"] is None for link in links)
    assert (await client.get(f"/api/workspaces/{second}/graph")).json()["edges"] == []


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="True concurrent transactions require PostgreSQL",
)
async def test_simultaneous_saves_have_one_winner(client):
    wid, page = await setup(client)
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    results = await asyncio.gather(
        *[client.patch(url, json={"version": 1, "content": f"writer-{i}"}) for i in range(4)]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    assert len((await client.get(url + "/revisions")).json()) == 2


async def test_page_input_validation_and_soft_delete_recovery(client):
    wid, page = await setup(client)
    base = f"/api/workspaces/{wid}/pages"
    for title in ["", "   "]:
        assert (await client.post(base, json={"title": title})).status_code == 422
    assert (await client.patch(f"{base}/{page['id']}", json={"version": 0})).status_code == 422
    assert (await client.delete(f"{base}/{page['id']}?version=1")).status_code == 200
    assert (await client.get(f"{base}/{page['id']}")).status_code == 404
    assert len((await client.get(base + "?deleted=true")).json()) == 1
    assert (await client.get(f"/api/workspaces/{wid}/search?q=initial")).json() == []
    assert (await client.post(f"{base}/{page['id']}/undelete", json={"version": 2})).json()["version"] == 3
