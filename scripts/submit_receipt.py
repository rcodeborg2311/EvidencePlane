from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx


SIGNATURE_HEADER = "X-EvidencePlane-Signature"


def _print_json(data: Any, *, pretty: bool, stream=sys.stdout) -> None:
    if pretty:
        print(json.dumps(data, indent=2, sort_keys=True), file=stream)
    else:
        print(json.dumps(data, separators=(",", ":")), file=stream)


def _read_json_bytes(path: Path) -> bytes:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"could not read receipt file: {exc}") from exc

    try:
        json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"receipt file is not valid JSON: {exc.msg}") from exc
    return body


def _signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _target_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/runs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign and submit a RunReceipt JSON file to EvidencePlane."
    )
    parser.add_argument("--file", required=True, type=Path, help="Path to RunReceipt JSON.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="EvidencePlane base URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the target URL and signature without sending the receipt.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args(argv)

    secret = os.environ.get("EVIDENCEPLANE_HMAC_SECRET")
    if not secret:
        print(
            "error: EVIDENCEPLANE_HMAC_SECRET is required",
            file=sys.stderr,
        )
        return 2

    try:
        body = _read_json_bytes(args.file)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    target = _target_url(args.url)
    signature = _signature(body, secret)
    if args.dry_run:
        _print_json(
            {
                "dry_run": True,
                "target_url": target,
                "signature": signature,
            },
            pretty=args.pretty,
        )
        return 0

    try:
        response = httpx.post(
            target,
            content=body,
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: signature,
            },
            timeout=10,
        )
    except httpx.RequestError as exc:
        print(f"error: network request failed: {exc}", file=sys.stderr)
        return 1

    try:
        response_body: Any = response.json()
    except json.JSONDecodeError:
        response_body = {"status_code": response.status_code, "body": response.text}

    if response.status_code < 200 or response.status_code >= 300:
        _print_json(response_body, pretty=args.pretty, stream=sys.stderr)
        return 1

    _print_json(response_body, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
