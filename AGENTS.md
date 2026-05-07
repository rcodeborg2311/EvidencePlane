# AGENTS.md

## Repository Layout

- `app/api/`: FastAPI routes and template handlers
- `app/models/`: Pydantic contracts and SQLAlchemy ORM models
- `app/services/`: HMAC validation, policy decisions, evidence hashing, persistence
- `app/templates/`: Jinja dashboard and run detail pages
- `app/static/`: local CSS and pinned htmx asset
- `examples/receipts/`: contract-valid receipts for real CLI submission examples
- `examples/github-actions/`: copyable CI integration example
- `docs/integrations/`: integration guides and MCP roadmap notes
- `scripts/`: receipt submission and smoke-test helpers
- `adapters/`: optional integration scaffolds outside the EvidencePlane core app
- `alembic/`: database migration environment and revisions
- `tests/`: unit and integration tests

## Run Commands

```sh
pyenv local 3.12.13
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/evidenceplane'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

## Test Commands

```sh
.venv/bin/python -m compileall app tests scripts adapters
.venv/bin/python -m pytest -q
```

## Lint and Type Commands

No lint or type checker dependency is configured. Use this standard-library syntax check:

```sh
.venv/bin/python -m compileall app tests scripts adapters
```

## Do-Not Rules

- EvidencePlane core is the policy and evidence layer, not the coding agent.
- Do not add runtime LLM calls, embeddings, vector databases, autonomous agents, semantic search, or background workers.
- Do not add AI runtime code to `app/`.
- MCP and agent adapters belong under `adapters/` or separate repositories.
- Do not add an MCP server or SDK without explicit dependency approval.
- Do not store source file contents or secret values.
- Do not add dependencies outside the pinned stack without stopping and reporting the need.
- Do not bypass HMAC validation for `POST /api/v1/runs`.
- Do not change policy rule ordering without updating tests and documentation.
- Keep `policy_version` explicit in decisions and evidence packs when policy behavior changes.
- Preserve evidence integrity when mutable audit state such as human review changes.
- Keep UI server-rendered with Jinja, local htmx, and local CSS. Do not add React, Tailwind, external component libraries, or CDNs.

## Definition of Done

- Exact dependencies install on Python 3.12.13.
- Alembic migrations apply successfully.
- Required API and HTML routes exist.
- Evidence pack hashes are recomputed before export.
- Required unit and integration tests succeed.
- `README.md` setup commands match the implementation.
