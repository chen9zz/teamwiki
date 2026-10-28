"""Metadata pagination exercises real requests, scope checks and bounded SQL."""

from sqlalchemy import event, select

from app.models import Page, WorkspaceMember
from tests.conftest import register, workspace
from tests.test_pages import create_page


async def test_tree_paginates_real_siblings_without_bodies_or_fake_roots(client, session_factory):
    owner = await register(client)
    wid = (await workspace(client))["id"]
    async with session_factory() as db:
        roots = [
            Page(
                workspace_id=wid,
                title=f"Root {i:03}",
                content="secret body",
                created_by=owner["id"],
                updated_by=owner["id"],
            )
            for i in range(52)
        ]
        db.add_all(roots)
        await db.flush()
        child = Page(
            workspace_id=wid,
            parent_id=roots[-1].id,
            title="Child",
            content="child secret",
            created_by=owner["id"],
            updated_by=owner["id"],
        )
        db.add(child)
        await db.commit()
        parent_id, child_id = roots[-1].id, child.id
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(db.bind.sync_engine, "before_cursor_execute", capture)
        try:
            first = await client.get(f"/api/workspaces/{wid}/page-tree")
            assert first.status_code == 200, first.text
            first = first.json()
            assert (len(first["items"]), first["has_more"], first["limit"], first["offset"]) == (
                50,
                True,
                50,
                0,
            )
            assert [row["title"] for row in first["items"]] == [f"Root {i:03}" for i in range(50)]
            assert all("content" not in row and row["parent_id"] is None for row in first["items"])
            last = (await client.get(f"/api/workspaces/{wid}/page-tree?offset=50")).json()
            assert [row["title"] for row in last["items"]] == ["Root 050", "Root 051"]
            assert last["has_more"] is False
            assert last["items"][-1]["has_children"] is True
            assert not any("pages.content" in statement for statement in statements)
        finally:
            event.remove(db.bind.sync_engine, "before_cursor_execute", capture)
    children = (await client.get(f"/api/workspaces/{wid}/page-tree?parent_id={parent_id}")).json()
    assert [row["id"] for row in children["items"]] == [child_id]
    assert children["items"][0]["has_children"] is False
    summary = (await client.get(f"/api/workspaces/{wid}/page-summaries?limit=2&offset=0")).json()
    assert [row["title"] for row in summary["items"]] == ["Child", "Root 000"]
    assert summary["has_more"] is True
    assert summary["items"][0]["parent_id"] == parent_id
    assert all("content" not in row for row in summary["items"])


async def test_tree_scope_bounds_and_membership_are_rechecked(client, second_client, session_factory):
    await register(client)
    wid = (await workspace(client))["id"]
    parent = await create_page(client, wid)
    other = (await workspace(client, "Other"))["id"]
    foreign = await create_page(client, other)
    for endpoint in ["page-tree", "page-summaries"]:
        for query in ["limit=0", "limit=201", "offset=-1"]:
            assert (await client.get(f"/api/workspaces/{wid}/{endpoint}?{query}")).status_code == 422
        assert (await second_client.get(f"/api/workspaces/{wid}/{endpoint}")).status_code == 401
    assert (await client.get(f"/api/workspaces/{wid}/page-tree?parent_id={foreign['id']}")).status_code == 404
    assert (await client.get(f"/api/workspaces/{wid}/page-tree?parent_id=missing")).status_code == 404
    assert (await client.delete(f"/api/workspaces/{wid}/pages/{parent['id']}?version=1")).status_code == 200
    assert (await client.get(f"/api/workspaces/{wid}/page-tree?parent_id={parent['id']}")).status_code == 404
    visitor = await register(second_client, "visitor@example.com")
    invite = await client.post(
        f"/api/workspaces/{wid}/invitations", json={"email": visitor["email"], "role": "viewer"}
    )
    await second_client.post("/api/invitations/accept", json={"token": invite.json()["token"]})
    assert (await second_client.get(f"/api/workspaces/{wid}/page-tree")).status_code == 200
    async with session_factory() as db:
        member = await db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == wid, WorkspaceMember.user_id == visitor["id"]
            )
        )
        await db.delete(member)
        await db.commit()
    for endpoint in ["page-tree", "page-summaries"]:
        assert (await second_client.get(f"/api/workspaces/{wid}/{endpoint}")).status_code == 404


async def test_tree_child_pages_have_stable_tie_order_and_exclude_deleted(client, session_factory):
    owner = await register(client)
    wid = (await workspace(client))["id"]
    parent = await create_page(client, wid)
    async with session_factory() as db:
        db.add_all(
            [
                Page(
                    id=f"child-{i:03}",
                    workspace_id=wid,
                    parent_id=parent["id"],
                    title="Same",
                    content="",
                    created_by=owner["id"],
                    updated_by=owner["id"],
                )
                for i in range(52)
            ]
        )
        await db.commit()
    base = f"/api/workspaces/{wid}/page-tree?parent_id={parent['id']}"
    first = (await client.get(base)).json()
    last = (await client.get(base + "&offset=50")).json()
    assert [row["id"] for row in first["items"]] == [f"child-{i:03}" for i in range(50)]
    assert [row["id"] for row in last["items"]] == ["child-050", "child-051"]
    assert last["has_more"] is False
    await client.delete(f"/api/workspaces/{wid}/pages/child-051?version=1")
    assert [row["id"] for row in (await client.get(base + "&offset=50")).json()["items"]] == ["child-050"]
