"""Frozen search-index algorithm v1, also used by migration 0003.

Do not change this module or its pinned jieba dictionary in place: introduce a new
version and a data migration. NFKC/casefold + jieba 0.42.1 search mode (HMM off)
is shared by documents and Chinese/mixed queries. No user dictionaries are loaded.
The 32,768-character source prefix and 8,192-term/96-KiB output budget per field
bound CPU and PostgreSQL vector storage; terms over 128 UTF-8 bytes are omitted.
The source stays intact; callers persist search_index_truncated for lost coverage.
"""

import re
import unicodedata

import jieba

SOURCE_CHAR_LIMIT = 32768
TERM_LIMIT = 8192
TERM_BYTE_LIMIT = 128
OUTPUT_BYTE_LIMIT = 96 * 1024
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
TOKENIZER = jieba.Tokenizer()


def warm_tokenizer():
    """Initialize once under jieba's own lock; async callers run this in a worker."""
    TOKENIZER.initialize()


def _terms(source):
    warm_tokenizer()
    normalized = unicodedata.normalize("NFKC", source[:SOURCE_CHAR_LIMIT]).casefold()
    terms, byte_count = [], 0
    truncated = len(source) > SOURCE_CHAR_LIMIT
    for term in TOKENIZER.cut_for_search(normalized, HMM=False):
        # Keep searchable words/numbers; discard punctuation rather than giving it
        # tsquery syntax. PostgreSQL's simple parser processes both sides equally.
        if not any(character.isalnum() for character in term):
            continue
        size = len(term.encode("utf-8"))
        if size > TERM_BYTE_LIMIT:
            truncated = True
            continue
        additional = size + bool(terms)
        if len(terms) == TERM_LIMIT or byte_count + additional > OUTPUT_BYTE_LIMIT:
            truncated = True
            break
        terms.append(term)
        byte_count += additional
    return " ".join(terms), truncated


def document_terms(title, content):
    title_terms, title_truncated = _terms(title)
    content_terms, content_truncated = _terms(content)
    return title_terms, content_terms, title_truncated or content_truncated


def query_terms(query):
    terms, truncated = _terms(query)
    if truncated:
        # A document may have partial coverage, but dropping a query term would
        # broaden its meaning and return pages that do not satisfy the request.
        raise ValueError("Search query terms exceed the index limit; shorten the query terms.")
    return terms


def has_chinese(query):
    return HAN.search(query) is not None
