"""Pinned synthetic producer bytes and independent test-side canonical hashing."""

import copy
import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures/capabilities/tendercite-capability-v1.json"
CONTENT_HASH = "sha256:e15ea7ea84176a4a9edf4bce710ede9008b620aa8329ddc14cb799354e9d6134"
FILE_HASH = "b7fe0f79bf9a2421aca3bd4ff907d177110e8165304ba0cdc1a3337fc0cc2b54"


def example():
    return json.loads(FIXTURE.read_bytes())


def encode(body, *, rehash=True):
    body = copy.deepcopy(body)
    if rehash:
        body.pop("content_hash", None)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        body["content_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return json.dumps(body, ensure_ascii=False).encode()


def member(body):
    return body["workforce_members"][0]


def capacity(body):
    return member(body)["capacity"]
