from app.services.links import parse_wiki_links


def test_parser_extracts_deduplicated_targets_and_ignores_code():
    text = (
        "[[FastAPI]] [[FastAPI|framework]] [[id:123|Stable]] `[[hidden]]`"
        "\n```md\n[[example]]\n```\n\\[[escaped]]"
    )
    assert parse_wiki_links(text) == ["FastAPI", "id:123"]


def test_parser_rejects_blank_and_multiline_targets():
    assert parse_wiki_links("[[ ]] [[broken\nlink]] [[ PostgreSQL ]]") == ["PostgreSQL"]


def test_unclosed_fences_do_not_index_document_examples():
    assert parse_wiki_links("```markdown\n[[hidden]]") == []
    assert parse_wiki_links("~~~\n[[hidden]]\n~~~~\n[[visible]]") == ["visible"]
    assert parse_wiki_links("``[[visible]]`") == ["visible"]


def test_adversarial_code_delimiters_have_bounded_parse_time():
    import os
    import subprocess
    import sys

    code = (
        "from app.services.links import parse_wiki_links; "
        'assert parse_wiki_links("prefix " + "`" * 100_000 + "x [[Visible]]") == ["Visible"]'
    )
    subprocess.run(
        [sys.executable, "-c", code], check=True, timeout=1.0, env={**os.environ, "PYTHONPATH": "backend"}
    )


def test_indented_code_and_even_backslash_escapes():
    assert parse_wiki_links("    [[CodeExample]]\n\n[[Visible]]") == ["Visible"]
    assert parse_wiki_links(r"\\[[Visible]] \[[hidden]]") == ["Visible"]
