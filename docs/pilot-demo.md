# EvidencePlane Pilot Demo

This guide walks through the full pilot flow from local startup through three realistic scenarios.
Follow it exactly to verify each part of the system works before running with a design partner.

---

## Prerequisites

- Python 3.12.13 (via pyenv or system install)
- PostgreSQL 16 running locally or accessible via a connection string
- Git

Verify:

```sh
python --version        # must be 3.12.x
psql --version          # must be 16.x
```

---

## 1. Local Startup

### 1.1 Install dependencies

```sh
cd /path/to/evidenceplane
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 1.2 Set environment variables

```sh
export DATABASE_URL='postgresql+psycopg://USER@localhost/evidenceplane'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
```

Never commit these values. Use secret managers or `.env` files excluded from version control.

### 1.3 Create the database and apply migrations

```sh
psql -d postgres -c "CREATE DATABASE evidenceplane;"
.venv/bin/alembic upgrade head
```

Expected output: three migration steps applied (`0001`, `0002`, `0003`).

### 1.4 Start the server

```sh
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The dashboard shows "No receipts yet" on a fresh database.

### 1.5 Verify health

```sh
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

---

## 2. GitHub Repo Setup

### 2.1 Copy the workflow and helper scripts

In the target repository:

```sh
mkdir -p .github/workflows scripts
cp /path/to/evidenceplane/examples/github-actions/evidenceplane.yml .github/workflows/evidenceplane.yml
cp /path/to/evidenceplane/scripts/github_actions_receipt.py scripts/
cp /path/to/evidenceplane/scripts/github_actions_feedback.py scripts/
cp /path/to/evidenceplane/scripts/submit_receipt.py scripts/
```

If the target repository does not use Python and pytest, adjust the install and test steps in the workflow.

### 2.2 Configure GitHub repository secrets

Required secrets (Settings → Secrets and variables → Actions → Secrets):

| Name | Value |
| --- | --- |
| `EVIDENCEPLANE_URL` | Public or internal base URL of your EvidencePlane deployment |
| `EVIDENCEPLANE_HMAC_SECRET` | Same value as `EVIDENCEPLANE_HMAC_SECRET` on the server |

### 2.3 Configure optional repository variables

Optional variables (Settings → Secrets and variables → Actions → Variables):

| Name | Default | Purpose |
| --- | --- | --- |
| `EVIDENCEPLANE_ENFORCE_BLOCK` | `true` | Fail CI when decision is `block` |
| `EVIDENCEPLANE_ENFORCE_REVIEW` | `false` | Fail CI when decision is `review` |
| `EVIDENCEPLANE_RUN_DETAIL_BASE_URL` | *(empty)* | Base URL for run detail links in the check summary |
| `EVIDENCEPLANE_SECRET_SCAN_COMMAND` | *(empty)* | Shell command that writes `evidenceplane-secret-scan.json` |

### 2.4 Expose EvidencePlane publicly for CI

GitHub Actions runners cannot reach `http://127.0.0.1:8000`. For the pilot, use one of:

- **ngrok**: `ngrok http 8000` — gives a public URL to set as `EVIDENCEPLANE_URL`
- **Cloud deployment**: deploy via Docker Compose to a server and use that URL
- **Tailscale or VPN**: make the server reachable from your CI runner network

Set `EVIDENCEPLANE_RUN_DETAIL_BASE_URL` to the same base URL so run detail links in check summaries work.

### 2.5 Local pilot alternative (no live GitHub Actions)

You can run the full pilot flow locally using the CLI submitter and the feedback script
without GitHub Actions. This is the fastest way to verify the core product before installing
the workflow in a repository.

```sh
# Submit the allow receipt
export EVIDENCEPLANE_HMAC_SECRET='your-server-secret'
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/allow.json \
  --url http://127.0.0.1:8000 \
  --pretty

# Submit the review receipt
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/review.json \
  --url http://127.0.0.1:8000 \
  --pretty

# Submit the block receipt
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/block.json \
  --url http://127.0.0.1:8000 \
  --pretty
```

Then use the dashboard and run detail pages to show the results.

---

## 3. Pilot Scenarios

### Scenario 1 — Safe docs change → `allow`

**What it represents:** An agent or CI job changed only documentation. No code was touched,
no tests failed, no secrets were detected.

**If running via CLI:**

```sh
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/allow.json \
  --url http://127.0.0.1:8000 \
  --pretty
```

**If running via GitHub Actions:**
Create a PR that modifies only Markdown files (README, docs). Let CI run.

**Expected EvidencePlane decision:**

```json
{
  "decision": "allow",
  "risk_score": 12,
  "review_status": "not_required",
  "violations": []
}
```

**Expected GitHub check summary:**

```
Decision:    allow
Next action: allowed
Risk score:  12
Policy:      1.0
Violations:  none
```

CI check passes (green).

**Expected dashboard state:**

- Row appears in the evidence ledger with decision badge `allow`
- Review status shows `not_required`
- Metrics card shows 1 allowed run
- Pending reviews counter stays at 0

**Expected run detail state:**

- Decision/risk hero shows `allow` / `12`
- Policy outcome section shows no violations
- Human review section says "Human review not required"
- Evidence download button is present

---

### Scenario 2 — Code change with no passed tests → `review`

**What it represents:** An agent or CI job changed source code but did not produce any
passing test evidence. EvidencePlane requires a human to review before this is treated
as cleared.

**If running via CLI:**

```sh
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/review.json \
  --url http://127.0.0.1:8000 \
  --pretty
```

**If running via GitHub Actions:**
Create a PR that modifies a source file (`.py`, `.ts`, `.go`, etc.) and skip or omit tests
by setting `EVIDENCEPLANE_ENFORCE_REVIEW=false` so CI does not block.

**Expected EvidencePlane decision:**

```json
{
  "decision": "review",
  "risk_score": 60,
  "review_status": "pending",
  "violations": [{"code": "NO_TEST_EVIDENCE", "severity": "medium"}]
}
```

**Expected GitHub check summary:**

```
Decision:    review
Next action: human review required
Risk score:  60
Violations:  NO_TEST_EVIDENCE — Code changed without any passed test evidence.
```

CI check passes (warning, not blocked) because `EVIDENCEPLANE_ENFORCE_REVIEW=false` by default.

**Expected dashboard state:**

- Row with `review` badge and `pending` review status
- Pending reviews counter increments
- Row is visually distinct in the table

**Expected run detail state:**

- Decision panel shows `review` / `60`
- Human review section shows "Human review required"
- Approve and reject forms are visible

**Approve the review (UI path):**

1. Open the run detail page at `http://127.0.0.1:8000/runs/{run_id}`
2. Enter `ADMIN_TOKEN` in the "Admin token" field
3. Fill in reviewer name/email and an optional note
4. Click "Approve review"
5. Page reloads showing review status `approved`

**Approve the review (API path):**

```sh
curl -X POST \
  "http://127.0.0.1:8000/api/v1/runs/{run_id}/review/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reviewer_identity": "name@example.com", "review_note": "Approved after inspection."}'
```

**Reject the review (API path):**

```sh
curl -X POST \
  "http://127.0.0.1:8000/api/v1/runs/{run_id}/review/reject" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reviewer_identity": "name@example.com", "review_note": "Rejected — needs tests."}'
```

---

### Scenario 3 — Fake scanner finding → `block`

**What it represents:** A secret scanner detected a credential or token in a changed file.
EvidencePlane blocks the run immediately. No human review can clear a block.

**If running via CLI:**

```sh
.venv/bin/python scripts/submit_receipt.py \
  --file examples/receipts/block.json \
  --url http://127.0.0.1:8000 \
  --pretty
```

**If running via GitHub Actions:**
Set the repository variable `EVIDENCEPLANE_SECRET_SCAN_COMMAND` to a command that writes:

```json
{"secret_detected": true}
```

to `evidenceplane-secret-scan.json`. For a demo using a real scanner:

```sh
EVIDENCEPLANE_SECRET_SCAN_COMMAND='echo "{\"secret_detected\":true}" > evidenceplane-secret-scan.json'
```

Or use a real scanner like `gitleaks`.

**Expected EvidencePlane decision:**

```json
{
  "decision": "block",
  "risk_score": 95,
  "review_status": "not_required",
  "violations": [{"code": "SECRET_PATTERN_DETECTED", "severity": "high"}]
}
```

**Expected GitHub check summary:**

```
Decision:    block
Next action: blocked
Risk score:  95
Violations:  SECRET_PATTERN_DETECTED — A changed file indicated a detected secret pattern.
```

CI check **fails** (red) because `EVIDENCEPLANE_ENFORCE_BLOCK=true` by default.

**Expected dashboard state:**

- Row with `block` badge and `not_required` review status
- Metrics card shows 1 blocked run

---

## 4. Download and Verify Evidence Packs

After completing the scenarios, download the evidence pack for any run:

**Via browser:** Click "Download evidence JSON" on the run detail page.

**Via curl (URL shown on run detail page):**

```sh
curl -sS http://127.0.0.1:8000/api/v1/evidence/{run_id} | python3 -m json.tool
```

**What to show a buyer:**

1. `policy_version` — proves which deterministic rules were applied
2. `decision` and `violations` — explains why the run was allowed, reviewed, or blocked
3. `evidence_sha256` — proves the pack has not been modified since it was created
4. `review_status`, `reviewer_identity`, `review_note`, `reviewed_at` — proves who reviewed
   and what they decided, including their note

The hash is recomputed on every export. If it does not match the stored value, the export fails.

---

## 5. Demo Narrative for a Buyer

**Opening:**
"EvidencePlane is the trust layer between your coding agents and your main branch.
Every time an agent or CI job touches a repository, it sends a signed evidence receipt here.
EvidencePlane applies fixed, auditable policy rules and decides: allow, review, or block.
GitHub surfaces the decision. Humans review the risky ones. Every decision has a cryptographic
evidence pack that you can download and prove."

**Walk through scenario 1 (allow):**
"Safe docs change. Policy sees no code changes, no test failures, no scanner findings.
Risk score 12. Allowed. No human needed. Evidence pack generated and sealed."

**Walk through scenario 2 (review):**
"Code changed, but we have no passing tests to prove the change is safe. Policy says:
human review required. The CI check passes so the PR is not blocked, but your team
can see the pending review in the EvidencePlane dashboard. An admin approves it here,
and the evidence pack now includes the reviewer's name, timestamp, and note."

**Walk through scenario 3 (block):**
"Scanner detected a secret in the changed files. Blocked. Risk score 95. CI fails.
The PR cannot merge without intervention. No human review can override a block —
only fixing the underlying issue and resubmitting will change the outcome."

**Closing:**
"The value isn't that we run the agent. The value is that you can prove what happened —
what policy version was applied, what the risk score was, who reviewed it, and whether
it passed or failed — and you have a cryptographic evidence pack you can export for
compliance or audit."

---

## 6. Troubleshooting

**Server returns 500 on first receipt submission**

The database was not migrated. Run `.venv/bin/alembic upgrade head` before starting the server.

**`invalid_signature` (401)**

The `EVIDENCEPLANE_HMAC_SECRET` on the client does not match the server.
Both must use the exact same value.

**Receipt submission fails with validation error (422)**

The JSON does not match the `RunReceipt` schema. Common causes:
- `commit_sha` is not exactly 40 lowercase hex characters
- `timestamp_utc` is not timezone-aware (must end with `Z` or `+00:00`)
- `changed_files` is empty (must have at least one file)
- Extra fields in the JSON (schema uses `extra="forbid"`)

**GitHub Actions cannot reach EvidencePlane**

GitHub Actions runners are on the public internet. `http://127.0.0.1:8000` is not reachable.
Use ngrok, a cloud deployment, or a VPN-exposed server.

**`admin_auth_invalid` (403) when approving a review**

The `Authorization: Bearer <token>` value does not match `ADMIN_TOKEN` on the server.

**Evidence download returns `service_unavailable` (503)**

The evidence integrity check failed. This means the stored hash does not match the pack
content. This should not occur in normal operation. If it does, restart the server and
resubmit the receipt.

**Review approval fails with `review_not_pending` (409)**

The run has already been approved or rejected. Only `pending` reviews can be actioned.
Fetch the run detail (`GET /api/v1/runs/{run_id}`) to check current `review_status`.

**Workflow `Build RunReceipt` step fails with missing GitHub environment**

The script requires `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_ACTOR`, and `GITHUB_RUN_ID`.
These are always set by GitHub Actions. If they are missing, the step is not running in
a GitHub Actions environment.

**Evidence SHA-256 changed after approval**

This is expected and correct. Approving or rejecting a review updates the evidence pack
(including `reviewer_identity`, `review_note`, and `reviewed_at`), and the hash is
recomputed over the new pack content. The new hash is stored and returned on download.
