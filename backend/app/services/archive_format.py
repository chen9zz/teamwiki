"""Strict v1 portable archive schema and position-preserving Markdown rewriting."""

import json
import re
import stat
import string
import zipfile
from uuid import UUID

from fastapi import HTTPException

from app.services.links import _escaped, _without_code, parse_wiki_links

MAX_PAGES = 400
MAX_DEPTH = 20
MAX_MANIFEST = 1024 * 1024
MAX_BODY = 2 * 1024 * 1024
MAX_CHARACTERS = 1_000_000
MAX_EXPANDED = 16 * 1024 * 1024
MAX_ZIP = 8 * 1024 * 1024
PAGE_FIELDS = {"key", "path", "title", "parent_key", "tags", "links"}
DOCUMENT_FIELDS = PAGE_FIELDS | {"content", "format"}


def invalid(message):
    raise HTTPException(422, detail={"code": "invalid_import", "message": message})


def _canonical_uuid(value):
    if not isinstance(value, str):
        invalid("Archive identities must be canonical UUIDs")
    try:
        if str(UUID(value)) != value:
            invalid("Archive identities must be canonical UUIDs")
    except ValueError:
        invalid("Archive identities must be canonical UUIDs")
    return value


def _text(value, *, maximum, label, nonblank=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        invalid(f"Invalid {label}")
    if nonblank and (not value.strip() or value != value.strip()):
        invalid(f"Invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        invalid(f"{label} must be valid UTF-8")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            invalid("Duplicate JSON object keys")
        result[key] = value
    return result


def read_manifest(data):
    if len(data) > MAX_MANIFEST:
        invalid("Portable manifest exceeds 1 MiB")
    try:
        result = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: invalid("Invalid JSON constant"),
        )
    except (ValueError, UnicodeError, RecursionError):
        invalid("Manifest must be valid UTF-8 JSON")
    if (
        not isinstance(result, dict)
        or set(result) != {"format", "version", "pages"}
        or result["format"] != "teamwiki"
        or type(result["version"]) is not int
        or result["version"] != 1
    ):
        invalid("Unsupported portable archive format or version")
    validate_pages(result["pages"], with_content=False)
    return result


def validate_pages(pages, *, with_content=True):
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        invalid("Portable archive is limited to 400 pages")
    by_key, paths = {}, set()
    expanded = 0
    for page in pages:
        fields = DOCUMENT_FIELDS if with_content else PAGE_FIELDS
        if not isinstance(page, dict) or set(page) != fields:
            invalid("Unexpected or missing portable page fields")
        if with_content and page["format"] != "teamwiki-v1":
            invalid("Mixed or unsupported import payload format")
        key = _canonical_uuid(page["key"])
        if key in by_key:
            invalid("Duplicate portable page identity")
        by_key[key] = page
        if page["path"] != f"pages/{key}.md" or page["path"] in paths:
            invalid("Portable body paths must match their canonical UUID")
        paths.add(page["path"])
        _text(page["title"], maximum=240, label="page title", nonblank=True)
        if page["parent_key"] is not None:
            _canonical_uuid(page["parent_key"])
        tags = page["tags"]
        if not isinstance(tags, list) or len(tags) > 24:
            invalid("Invalid portable page tags")
        for tag in tags:
            _text(tag, maximum=64, label="tag", nonblank=True)
        if len(set(tags)) != len(tags):
            invalid("Duplicate tags are not allowed")
        if not isinstance(page["links"], list):
            invalid("Invalid portable links")
        seen = set()
        for link in page["links"]:
            if not isinstance(link, dict) or set(link) != {"raw_target", "target_key"}:
                invalid("Unexpected or missing portable link fields")
            raw = _text(link["raw_target"], maximum=1024, label="link target", nonblank=True)
            _canonical_uuid(link["target_key"])
            if raw in seen:
                invalid("Duplicate portable raw link targets")
            seen.add(raw)
        if with_content:
            content = _text(page["content"], maximum=MAX_CHARACTERS, label="Markdown content")
            size = len(content.encode("utf-8"))
            if size > MAX_BODY:
                invalid("Markdown file exceeds 2 MiB")
            expanded += size
            if expanded > MAX_EXPANDED:
                invalid("Expanded archive exceeds 16 MiB")
            visible = set(parse_wiki_links(content))
            if not seen <= visible:
                invalid("Manifest link target does not occur in visible Markdown")
    # Iterative traversal is bounded and never recurses on untrusted parents.
    depths = {}
    for key in by_key:
        chain, visiting, cursor = [], set(), key
        while cursor is not None and cursor not in depths:
            if cursor not in by_key:
                invalid("Portable parent must be present in this archive")
            if cursor in visiting:
                invalid("Portable page hierarchy contains a cycle")
            chain.append(cursor)
            visiting.add(cursor)
            if len(chain) > MAX_DEPTH:
                invalid("Portable hierarchy exceeds 20 levels")
            cursor = by_key[cursor]["parent_key"]
        depth = depths.get(cursor, 0)
        for child in reversed(chain):
            depth += 1
            if depth > MAX_DEPTH:
                invalid("Portable hierarchy exceeds 20 levels")
            depths[child] = depth
    for page in pages:
        for link in page["links"]:
            if link["target_key"] not in by_key:
                invalid("Portable link targets must be present in this archive")
            raw = link["raw_target"]
            if raw.startswith("id:") and raw[3:] != link["target_key"]:
                invalid("Explicit ID link does not match manifest target identity")
    return depths


def read_portable(archive):
    entries = archive.infolist()
    if len(entries) > MAX_PAGES + 1:
        invalid("Too many portable archive entries")
    names = set()
    for entry in entries:
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (
            entry.orig_filename != entry.filename
            or entry.is_dir()
            or mode not in {0, stat.S_IFREG}
            or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or entry.flag_bits & 1
            or entry.filename in names
        ):
            invalid("Duplicate, unsafe, encrypted or non-regular portable archive entry")
        names.add(entry.filename)
    manifests = [entry for entry in entries if entry.filename == "teamwiki.json"]
    if len(manifests) != 1 or manifests[0].file_size > MAX_MANIFEST:
        invalid("Portable archive requires one root manifest of at most 1 MiB")
    with archive.open(manifests[0]) as stream:
        manifest = read_manifest(stream.read(MAX_MANIFEST + 1))
    expected = {"teamwiki.json"} | {page["path"] for page in manifest["pages"]}
    if names != expected:
        invalid("Portable archive files must match the manifest exactly")
    files = {entry.filename: entry for entry in entries}
    if sum(files[page["path"]].file_size for page in manifest["pages"]) > MAX_EXPANDED:
        invalid("Expanded archive exceeds 16 MiB")
    documents = []
    for page in manifest["pages"]:
        entry = files[page["path"]]
        if entry.file_size > MAX_BODY:
            invalid("Markdown file exceeds 2 MiB")
        with archive.open(entry) as stream:
            data = stream.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            invalid("Markdown file exceeds 2 MiB")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            invalid("Markdown must be UTF-8")
        documents.append({**page, "content": content, "format": "teamwiki-v1"})
    validate_pages(documents)
    return documents


def _uuid_digit_positions(original):
    try:
        UUID(original)
    except ValueError:
        return None
    # UUID accepts compact, braces, URN prefixes and unusual hyphen positions.
    # Track the 32 hex digits it actually consumes, preserving every other byte.
    positions = list(range(len(original)))
    visible = original
    for prefix in ("urn:", "uuid:"):
        while (start := visible.find(prefix)) >= 0:
            end = start + len(prefix)
            visible = visible[:start] + visible[end:]
            del positions[start:end]
    left = len(visible) - len(visible.lstrip("{}"))
    right = len(visible.rstrip("{}"))
    visible, positions = visible[left:right], positions[left:right]
    digits = [position for character, position in zip(visible, positions) if character != "-"]
    # Python's int(hex, 16) also accepts whitespace, '+' and '0x' spellings.
    # These can include synthetic inline-code spaces and never identify a live
    # canonical page, so preserve them literally rather than rewriting examples.
    if len(digits) != 32 or any(original[position] not in string.hexdigits for position in digits):
        return None
    return digits


def unbound_uuid_spelling(original, replacement):
    """Keep the exact length and UUID punctuation of an unresolved ID spelling."""
    digits = _uuid_digit_positions(original)
    if digits is None:
        invalid("Invalid explicit UUID spelling")
    hexadecimal = UUID(replacement).hex
    original_digits = "".join(original[position] for position in digits)
    if original_digits.isupper():
        hexadecimal = hexadecimal.upper()
    rewritten = list(original)
    for position, character in zip(digits, hexadecimal):
        rewritten[position] = character
    return "".join(rewritten)


def explicit_uuid_targets(content):
    # Identity lookup in the live index is literal, so a noncanonical spelling
    # must remain external even if it denotes an archived UUID mathematically.
    identities = set()
    for raw in parse_wiki_links(content):
        if raw.startswith("id:") and _uuid_digit_positions(raw[3:]) is not None:
            identities.add(raw[3:])
    return identities


def remap_visible_ids(content, identities):
    """Replace ID targets only; labels, examples, spacing and newlines survive."""
    visible, offsets = _without_code(content, with_offsets=True)
    parts, cursor = [], 0
    for match in re.finditer(r"\[\[([^\[\]\n]{1,1024})\]\]", visible):
        if _escaped(visible, match.start()):
            continue
        target = match.group(1).split("|", 1)[0]
        raw = target.strip()
        try:
            identity = raw[3:] if raw.startswith("id:") else None
            if identity is not None:
                UUID(identity)
        except ValueError:
            identity = None
        if identity in identities:
            start = match.start(1) + len(target) - len(target.lstrip()) + 3
            end = start + len(raw) - 3
            replacement = identities[identity]
            if len(replacement) != end - start:
                invalid("UUID migration must preserve target length")
            # Source characters can be non-contiguous after code removal. Group
            # only adjacent positions, so intervening examples remain untouched.
            source_positions = offsets[start:end]
            group_start, previous, group = None, None, []
            for position, character in zip(source_positions, replacement):
                if position < 0:
                    invalid("An explicit UUID cannot contain synthetic code text")
                if previous is not None and position != previous + 1:
                    parts.extend((content[cursor:group_start], "".join(group)))
                    cursor, group_start, group = previous + 1, None, []
                if group_start is None:
                    group_start = position
                group.append(character)
                previous = position
            parts.extend((content[cursor:group_start], "".join(group)))
            cursor = previous + 1
    parts.append(content[cursor:])
    return "".join(parts)


def rewrite_portable_documents(documents, identities):
    """Validate actual migrated text, metadata and visible targets before writes."""
    rewritten = {}

    def target(raw):
        if raw.startswith("id:") and raw[3:] in identities:
            return "id:" + identities[raw[3:]]
        return raw

    for document in documents:
        content = remap_visible_ids(document["content"], identities)
        expected = {target(raw) for raw in parse_wiki_links(document["content"])}
        if set(parse_wiki_links(content)) != expected:
            invalid("UUID migration changed the visible Markdown link structure")
        key = identities[document["key"]]
        rewritten[document["key"]] = {
            **document,
            "key": key,
            "path": f"pages/{key}.md",
            "parent_key": identities.get(document["parent_key"]),
            "content": content,
            "links": [
                {"raw_target": target(link["raw_target"]), "target_key": identities[link["target_key"]]}
                for link in document["links"]
            ],
        }
    validate_pages(list(rewritten.values()))
    return rewritten


def preflight_portable_rewrite(documents):
    """Check migration constraints before queuing without changing the payload."""
    identities = {document["key"]: document["key"] for document in documents}
    for document in documents:
        for original in explicit_uuid_targets(document["content"]):
            if original not in identities:
                identities[original] = unbound_uuid_spelling(original, "ffffffff-ffff-4fff-bfff-ffffffffffff")
    rewrite_portable_documents(documents, identities)
