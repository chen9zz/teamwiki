import io
import zipfile

import pytest
from fastapi import HTTPException

from app.services.imports import validate_upload


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries:
            z.writestr(name, content)
    return output.getvalue()


def test_markdown_and_nested_zip_are_imported_as_data():
    assert validate_upload("guide.md", b"# Hello") == [
        {"path": "guide.md", "title": "guide", "content": "# Hello"}
    ]
    assert len(validate_upload("docs.zip", archive([("guide.md", "hello"), ("api/intro.md", "world")]))) == 2


@pytest.mark.parametrize(
    "path", ["../secret.md", "/etc/secret.md", "C:/private.md", "safe/../../private.md", "safe\\..\\evil.md"]
)
def test_zip_traversal_is_rejected(path):
    with pytest.raises(HTTPException) as exc:
        validate_upload("docs.zip", archive([(path, "bad")]))
    assert exc.value.status_code == 422


def test_zip_bomb_and_duplicate_paths_are_rejected():
    for payload in [archive([("huge.md", "a" * 500_000)]), archive([("x.md", "a"), ("X.md", "b")])]:
        with pytest.raises(HTTPException):
            validate_upload("docs.zip", payload)


def test_non_utf8_and_empty_imports_are_rejected():
    for name, data in [("bad.md", b"\xff\xfe"), ("empty.zip", archive([])), ("bad.exe", b"hello")]:
        with pytest.raises(HTTPException):
            validate_upload(name, data)
