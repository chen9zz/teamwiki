import io
import os
import zipfile

import pytest

from tests.conftest import register, workspace
from tests.test_pages import create_page


async def test_markdown_and_zip_export_use_scoped_content(client, second_client):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid, title="../../Unsafe", content="# export\n[[Wiki]]")
    exported = await client.get(f"/api/workspaces/{wid}/pages/{page['id']}/export")
    assert exported.status_code == 200
    assert exported.text == "# export\n[[Wiki]]"
    archive = await client.get(f"/api/workspaces/{wid}/export")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zip_file:
        assert len(zip_file.namelist()) == 1
        assert "/" not in zip_file.namelist()[0]
        assert zip_file.read(zip_file.namelist()[0]).decode() == "# export\n[[Wiki]]"
    await register(second_client, "outsider@example.com")
    assert (await second_client.get(f"/api/workspaces/{wid}/export")).status_code == 404


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"), reason="FTS requires real PostgreSQL"
)
async def test_postgres_full_text_search_weights_title_and_excludes_deleted(client):
    await register(client)
    wid = (await workspace(client))["id"]
    title = await create_page(client, wid, title="Concurrency", content="guide")
    body = await create_page(client, wid, title="Other", content="Concurrency guide")
    results = (await client.get(f"/api/workspaces/{wid}/search?q=Concurrency")).json()
    assert [row["id"] for row in results] == [title["id"], body["id"]]
    assert results[0]["rank"] > results[1]["rank"]
    await client.delete(f"/api/workspaces/{wid}/pages/{title['id']}?version=1")
    assert [r["id"] for r in (await client.get(f"/api/workspaces/{wid}/search?q=Concurrency")).json()] == [
        body["id"]
    ]
