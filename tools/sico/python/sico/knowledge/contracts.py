"""Bounded retrieval values and multilingual lexical terms."""

import hashlib
import json
import re
import time
import unicodedata

MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 2048
MAX_CHUNKS = 32768
MAX_OUTPUT = 32768
CHUNK_BYTES = 2400
AUTHORITY = "reference_only; PDK eligibility, CDF rules and published process data remain structured"


class KnowledgeError(ValueError):
    def __init__(self, code, message, *, diagnostic_id=None, category=None):
        self.code = code
        self.diagnostic_id = diagnostic_id
        self.category = category or code
        super().__init__(message)


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def clipped(text, size):
    return str(text).encode("utf-8")[:size].decode("utf-8", errors="ignore")


def terms(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    words = re.findall(r"[a-z0-9_]+(?:[.+/<>:-][a-z0-9_]+)*|[\u3400-\u9fff]+", text)
    result = set()
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff":
            result.update(word)
            result.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.add(word)
            result.update(re.findall(r"[a-z0-9_]+", word))
    return sorted(value for value in result if len(value) <= 128)


def query_terms(query):
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 1024:
        raise KnowledgeError("invalid_query", "问题不能为空，且最多 1024 字节")
    result = terms(query)
    if not result or len(result) > 128:
        raise KnowledgeError("invalid_query", "问题没有可检索的词项或超过 128 个词项")
    return result


def checkpoint(cancelled, deadline):
    if cancelled():
        raise KnowledgeError("cancelled", "知识源操作已取消，保留上一可用索引")
    if time.monotonic() > deadline:
        raise KnowledgeError("timeout", "知识源操作超时，保留上一可用索引")


def bounded(value):
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_OUTPUT:
        raise KnowledgeError("output_limit", "检索结果超过输出上限，请缩小查询范围")
    return value
