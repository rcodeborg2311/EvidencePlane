from __future__ import annotations

import argparse
import hashlib
import hmac
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()

    body = args.receipt.read_bytes()
    signature = hmac.new(args.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    response = httpx.post(
        f"{args.base_url}/api/v1/runs",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-EvidencePlane-Signature": signature,
        },
        timeout=10,
    )
    print(response.status_code)
    print(response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
