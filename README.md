# EvidencePlane

EvidencePlane is a single-tenant governance app for coding-agent or CI execution receipts. It accepts HMAC-signed run receipts, applies deterministic policy rules, stores an auditable evidence pack, and renders dashboard/detail pages with server-side Jinja templates.

## Requirements

- Python 3.12.13
- PostgreSQL 16
- Exact Python packages from `requirements.txt`

## Environment

Set these variables before running the app or migrations:

```sh
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/evidenceplane'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
```

`EVIDENCEPLANE_HMAC_SECRET` signs `POST /api/v1/runs` payload bytes with HMAC SHA-256. `ADMIN_TOKEN` is reserved for mutating non-ingest endpoints; this MVP does not expose any such endpoint.

Secret-file variants are also supported for container platforms and secret managers:

```sh
export DATABASE_URL_FILE='/run/secrets/database_url'
export EVIDENCEPLANE_HMAC_SECRET_FILE='/run/secrets/evidenceplane_hmac_secret'
export ADMIN_TOKEN_FILE='/run/secrets/admin_token'
```

API docs are disabled by default. Set `EVIDENCEPLANE_ENABLE_DOCS=true` only for trusted local development if `/docs`, `/redoc`, or `/openapi.json` are needed.

## Local Setup

```sh
pyenv install 3.12.13
pyenv local 3.12.13
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`.

## Docker Compose

Create an environment with real secrets, then start the app:

```sh
export POSTGRES_PASSWORD='replace-with-a-high-entropy-password'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
docker compose up --build
```

The compose app runs `alembic upgrade head` before starting Uvicorn.

## Signed Ingest Example

```sh
body='{"idempotency_key":"allow-001","repo_name":"docs-site","commit_sha":"1111111111111111111111111111111111111111","branch":"docs/update-readme","actor":"ci-bot","timestamp_utc":"2026-05-06T19:00:00Z","changed_files":[{"path":"README.md","classification":"docs","additions":10,"deletions":2,"secret_detected":false}],"tests":[],"tool_calls":[],"policy_context":{"protected_branch":false,"emergency_override":false,"approver_email":null}}'
sig=$(BODY="$body" .venv/bin/python - <<'PY'
import hashlib, hmac, os
body = os.environ["BODY"].encode("utf-8")
print(hmac.new(os.environ["EVIDENCEPLANE_HMAC_SECRET"].encode(), body, hashlib.sha256).hexdigest())
PY
)
curl -sS \
  -H "Content-Type: application/json" \
  -H "X-EvidencePlane-Signature: $sig" \
  --data "$body" \
  http://127.0.0.1:8000/api/v1/runs
```

## Tests and Checks

```sh
.venv/bin/python -m compileall app tests scripts
.venv/bin/python -m pytest
DATABASE_URL='sqlite:///./evidenceplane-check.db' .venv/bin/alembic upgrade head
```

No formatter, linter, or type checker dependency is configured because the MVP is constrained to the requested dependency list.

## CI

GitHub Actions runs on pushes to `main` and pull requests:

- install exact dependencies
- compile `app`, `tests`, and `scripts`
- run pytest
- run `alembic upgrade head` against PostgreSQL 16
- boot the app and run a signed ingest smoke test

## Demo

Demo receipts live in `demo/receipts/`:

- `allow.json`
- `review.json`
- `block.json`

Use the walkthrough in `demo/walkthrough.md` for a short product demo.

## Evidence Hashing

Evidence packs include `evidence_sha256`. The digest is computed over the canonical JSON evidence body with `evidence_sha256` excluded, because including a digest inside the bytes being digested would make the value self-referential.
