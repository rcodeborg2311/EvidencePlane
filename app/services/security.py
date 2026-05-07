from __future__ import annotations

import hashlib
import hmac
import re

SIGNATURE_HEADER = "X-EvidencePlane-Signature"
_HEX_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def compute_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def signature_is_valid(signature: str | None, body: bytes, secret: str) -> bool:
    if signature is None:
        return False
    supplied = signature.strip()
    if supplied.startswith("sha256="):
        supplied = supplied.removeprefix("sha256=")
    if not _HEX_SHA256_RE.fullmatch(supplied):
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(supplied.lower(), expected)


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
