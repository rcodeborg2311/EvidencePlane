# EvidencePlane

EvidencePlane is a single-tenant governance app for coding-agent and CI execution receipts. It accepts HMAC-signed `RunReceipt` JSON, applies deterministic policy rules, stores an auditable evidence pack, and renders dashboard/detail pages with server-side Jinja templates.

EvidencePlane is not an AI runtime. It does not call LLMs, create embeddings, run autonomous agents, or use vector search. Its job is to be the policy and evidence layer that receives facts from CI, coding-agent wrappers, or future adapters.

## How EvidencePlane Is Used

1. A coding agent, CI job, or orchestration wrapper performs work against a repository.
2. The runner collects verifiable facts:
   - changed files
   - tests
   - tool calls
   - network usage
   - scanner results
   - actor
   - branch
   - commit SHA
3. The runner serializes the receipt JSON and signs the exact bytes with `EVIDENCEPLANE_HMAC_SECRET`.
4. EvidencePlane validates the signature.
5. EvidencePlane applies deterministic policy.
6. EvidencePlane stores an evidence pack.
7. GitHub Actions can publish the result back to a PR/check.
8. Human reviewers handle `review` decisions.
9. EvidencePlane does not run the coding agent itself.

The local demo receipts are only for demonstration. Real deployments should submit receipts from CI or an orchestrator so the agent is not the only trusted narrator.

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

Secret-file variants are supported for container platforms and secret managers:

```sh
export DATABASE_URL_FILE='/run/secrets/database_url'
export EVIDENCEPLANE_HMAC_SECRET_FILE='/run/secrets/evidenceplane_hmac_secret'
export ADMIN_TOKEN_FILE='/run/secrets/admin_token'
```

API docs are disabled by default. Set `EVIDENCEPLANE_ENABLE_DOCS=true` only for trusted local development if `/docs`, `/redoc`, or `/openapi.json` are needed.

Optional OIDC login variables:

```sh
export OIDC_ISSUER='https://idp.example.com'
export OIDC_CLIENT_ID='evidenceplane-client-id'
export OIDC_CLIENT_SECRET='replace-with-client-secret'
export OIDC_REDIRECT_URI='https://evidenceplane.example.com/auth/oidc/callback'
```

`OIDC_ISSUER` must match the issuer returned by the provider's
`/.well-known/openid-configuration` document. EvidencePlane verifies ID tokens
against the provider JWKS and requires valid issuer, audience, expiry, issued-at,
and subject claims.

## Local Development

```sh
pyenv install 3.12.13
pyenv local 3.12.13
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`.

## Submit A Real Receipt

Use the CLI submitter to sign and send a receipt JSON file:

```sh
export EVIDENCEPLANE_HMAC_SECRET='replace-with-the-server-hmac-secret'
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/allow.json \
  --url http://127.0.0.1:8000 \
  --pretty
```

Dry-run mode computes the signature and target URL without sending:

```sh
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/review.json \
  --url http://127.0.0.1:8000 \
  --dry-run \
  --pretty
```

Example receipts live in `examples/receipts/` and cover the three MVP outcomes:

- `allow.json`
- `review.json`
- `block.json`

Older demo receipts remain in `demo/receipts/` for walkthrough use.

## GitHub Actions Integration

The example workflow at `examples/github-actions/evidenceplane.yml` shows how to submit a signed receipt from CI after tests run and publish the decision back to the GitHub check summary.

Required GitHub secrets:

- `EVIDENCEPLANE_URL`
- `EVIDENCEPLANE_HMAC_SECRET`

Optional repository variable:

- `EVIDENCEPLANE_SECRET_SCAN_COMMAND`: a scanner command that writes `evidenceplane-secret-scan.json`
- `EVIDENCEPLANE_ENFORCE_BLOCK`: defaults to `true`
- `EVIDENCEPLANE_ENFORCE_REVIEW`: defaults to `false`
- `EVIDENCEPLANE_RUN_DETAIL_BASE_URL`: optional public/internal base URL for run links

Read `docs/integrations/github-actions.md` for installation steps, signature details, and limitations.

CI is a better signer than the coding agent itself because CI can independently observe the checked-out commit, diff metadata, test exit codes, and runner-controlled environment.

Decision mapping in the workflow:

- `allow`: passing check
- `review`: warning check summary, non-blocking
- `block`: failing check

## Human Review

Runs with deterministic decision `review` start with `review_status: pending`. Admins can approve or reject a pending review with:

```http
POST /api/v1/runs/{run_id}/review/approve
Authorization: Bearer <ADMIN_TOKEN>
```

or:

```http
POST /api/v1/runs/{run_id}/review/reject
Authorization: Bearer <ADMIN_TOKEN>
```

The review action stores reviewer identity, note, outcome, and UTC timestamp on the run snapshot. It also appends a hashed `review_events` record so human review history remains auditable. Evidence exports include the current review state plus the append-only review event list, and recompute `evidence_sha256` when review state changes.

## AI Agent Code

AI agent code does not belong in the EvidencePlane core app. Use adapters or wrappers outside `app/`.

This repository includes a dependency-free reference scaffold at `adapters/evidenceplane-agent-adapter/`. It can wrap a local coding-agent command, collect diff/test/scanner facts, and submit a signed receipt. It does not call an LLM by default and should be treated as less trusted than CI unless its environment is controlled.

For production trust, prefer CI-signed receipts:

1. agent creates changes
2. CI independently verifies diff, tests, scanner output, and tool results
3. CI signs the final receipt
4. EvidencePlane decides `allow`, `review`, or `block`

## MCP Roadmap

MCP is documented but not implemented in this repository. A future MCP adapter could expose tools such as `submit_run_receipt`, `get_run_decision`, `get_evidence_pack`, and `list_recent_runs`.

The adapter should call EvidencePlane's HTTP API and should not duplicate policy logic. If MCP support requires new dependencies, it should be built as a separate adapter package or added only after explicit dependency approval.

Read `docs/integrations/mcp.md` for the current design notes.

## Security Model

- Single tenant only.
- All secrets come from environment variables or secret files.
- `POST /api/v1/runs` requires HMAC SHA-256 in `X-EvidencePlane-Signature`.
- Invalid signatures return `401 invalid_signature`.
- Payloads over 256 KB return `413 payload_too_large`.
- `ADMIN_TOKEN` protects human review approve/reject endpoints.
- EvidencePlane stores metadata, hashes, policy outcomes, and short fields. It does not store full source code blobs or secret values.
- Stack traces are not exposed in HTTP responses.

## Evidence Hashing

Evidence packs include `evidence_sha256`. The digest is computed over canonical JSON with sorted keys and stable separators while excluding the `evidence_sha256` field itself. Including the digest inside the bytes being digested would make the value self-referential.

Evidence export recomputes the digest and fails if the stored pack does not match the persisted hash. Human review events also carry their own SHA-256 hash, computed over canonical event JSON while excluding `event_sha256`.

Each decision and evidence pack also includes `policy_version`, currently `1.0`, so reviewers can prove which deterministic policy evaluated a run.

## Docker Compose

Create an environment with real secrets, then start the app:

```sh
export POSTGRES_PASSWORD='replace-with-a-high-entropy-password'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
docker compose up --build
```

The compose app runs `alembic upgrade head` before starting Uvicorn.

Stop the stack:

```sh
docker compose down
```

## Railway Deployment

Railway deployments use `railway.json` for the start command, healthcheck, and
US East replica configuration. Set `DATABASE_URL` on the web service with a
PostgreSQL reference such as `${{Postgres.DATABASE_URL}}`, then redeploy the
web service.

## Tests And Checks

```sh
.venv/bin/python --version
.venv/bin/python -m compileall app tests scripts adapters
.venv/bin/python -m pytest -q
DATABASE_URL='postgresql+psycopg://evidenceplane:password@127.0.0.1:5432/evidenceplane' \
  .venv/bin/alembic upgrade head
```

No formatter, linter, or type checker dependency is configured because the MVP is constrained to the requested dependency list.

## Repository CI

GitHub Actions for this repository runs on pushes to `main` and pull requests:

- install exact dependencies
- compile `app`, `tests`, `scripts`, and `adapters`
- run pytest
- run `alembic upgrade head` against PostgreSQL 16
- boot the app and run a signed ingest smoke test

## Demo

Demo receipts live in `demo/receipts/`:

- `allow.json`
- `review.json`
- `block.json`

Use `demo/walkthrough.md` for a short product demo.

For a live repository pilot, use `docs/pilot-demo.md`.
