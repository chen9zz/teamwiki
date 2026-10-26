"""Regression tests for common-edit parallelism without weakening structural consistency."""

import asyncio

import pytest
from sqlalchemy import select

from app.models import PageLink, Workspace
from tests.conftest import register, workspace
from tests.test_pages import create_page


async def prepared(client):
    await register(client)
    wid = (await workspace(client))["id"]
    target = await create_page(client, wid, "Target")
    source = await create_page(client, wid, "Source", "[[Target]] old source")
    other = await create_page(client, wid, "Other", "[[Target]] unrelated source")
    return wid, target, source, other


async def test_normal_save_preserves_unrelated_link_rows_and_parses_only_changed_source(
    client, session_factory, monkeypatch
):
    from app.services import links

    wid, target, source, other = await prepared(client)
    async with session_factory() as db:
        previous = list(
            (await db.scalars(select(PageLink.id).where(PageLink.source_id == other["id"]))).all()
        )
    parsed = []
    original = links.parse_wiki_links

    def observe(content):
        parsed.append(content)
        return original(content)

    monkeypatch.setattr(links, "parse_wiki_links", observe)
    content = "[[Target]] updated source"
    response = await client.patch(
        f"/api/workspaces/{wid}/pages/{source['id']}",
        json={"version": 1, "title": "Source", "content": content},
    )
    assert response.status_code == 200, response.text
    async with session_factory() as db:
        current = list((await db.scalars(select(PageLink.id).where(PageLink.source_id == other["id"]))).all())
        edited = await db.scalar(select(PageLink).where(PageLink.source_id == source["id"]))
    assert current == previous, "Saving one page must not replace other pages link identities"
    assert parsed == [content], "Unchanged Markdown bodies must not be reparsed"
    assert edited.source_version == 2 and edited.target_id == target["id"]


async def test_normal_save_overlaps_another_workspace_shared_holder(client, session_factory):
    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL shared row locks")
    wid, target, source, other = await prepared(client)
    async with session_factory.begin() as db:
        await db.scalar(select(Workspace).where(Workspace.id == wid).with_for_update(read=True))
        response = await asyncio.wait_for(
            client.patch(
                f"/api/workspaces/{wid}/pages/{source['id']}",
                json={"version": 1, "title": "Source", "content": "[[Target]] parallel body"},
            ),
            timeout=1,
        )
        assert response.status_code == 200, response.text


async def test_rename_waits_for_workspace_shared_holders(client, session_factory):
    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL shared row locks")
    wid, target, source, other = await prepared(client)
    task = None
    try:
        async with session_factory.begin() as db:
            await db.scalar(select(Workspace).where(Workspace.id == wid).with_for_update(read=True))
            task = asyncio.create_task(
                client.patch(
                    f"/api/workspaces/{wid}/pages/{target['id']}",
                    json={"version": 1, "title": "Renamed"},
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
        response = await asyncio.wait_for(task, timeout=3)
        assert response.status_code == 200, response.text
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    links = (await client.get(f"/api/workspaces/{wid}/pages/{source['id']}/links")).json()
    assert links["outgoing"][0]["target_id"] == target["id"]
    assert links["outgoing"][0]["target_title"] == "Renamed"


async def test_content_update_and_rename_preserve_pinned_target_under_race(client):
    wid, target, source, other = await prepared(client)
    base = f"/api/workspaces/{wid}/pages"
    results = await asyncio.gather(
        client.patch(f"{base}/{source['id']}", json={"version": 1, "content": "[[Target]] changed"}),
        client.patch(f"{base}/{target['id']}", json={"version": 1, "title": "Renamed"}),
    )
    assert [r.status_code for r in results] == [200, 200]
    link = (await client.get(f"{base}/{source['id']}/links")).json()["outgoing"][0]
    assert (link["target_id"], link["target_title"], link["state"], link["source_version"]) == (
        target["id"],
        "Renamed",
        "resolved",
        2,
    )


async def test_member_revoked_while_save_waits_is_rechecked(client, second_client, session_factory):
    from sqlalchemy import delete, event

    from app.models import Page, WorkspaceMember

    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    wid, target, source, other = await prepared(client)
    member = await register(second_client, "editor@example.com")
    invitation = (
        await client.post(
            f"/api/workspaces/{wid}/invitations", json={"email": member["email"], "role": "editor"}
        )
    ).json()
    await second_client.post("/api/invitations/accept", json={"token": invitation["token"]})
    waiting = asyncio.Event()
    engine = session_factory.kw["bind"]

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "FROM workspaces" in statement and "FOR SHARE" in statement:
            waiting.set()

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    task = None
    try:
        async with session_factory.begin() as db:
            await db.scalar(select(Workspace).where(Workspace.id == wid).with_for_update())
            task = asyncio.create_task(
                second_client.patch(
                    f"/api/workspaces/{wid}/pages/{source['id']}",
                    json={"version": 1, "content": "must not persist"},
                )
            )
            await asyncio.wait_for(waiting.wait(), 3)
            await db.execute(
                delete(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == wid, WorkspaceMember.user_id == member["id"]
                )
            )
        response = await asyncio.wait_for(task, 3)
        assert response.status_code == 404
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as db:
        page = await db.get(Page, source["id"])
        assert (page.version, page.content) == (1, "[[Target]] old source")
