# GitHub App Integration Design

This document is a design specification, not implementation. Do not build this until the pilot
is smooth and a design partner has validated the GitHub Actions flow.

---

## Why Build a GitHub App

The existing GitHub Actions integration works but has limitations:

- Users must copy workflow YAML and three Python scripts into every repository
- Check summaries are workflow-job-level, not per-commit native check runs
- No per-repository configuration stored in EvidencePlane
- No native PR comments with evidence links
- No centralized installation across an org

A GitHub App solves all of these:

- **Installable by org or repo** — one install, all repositories in scope
- **Native PR check runs** — richer status, annotations, and links than workflow summaries
- **PR comments** — post evidence links and violation summaries directly on the PR
- **Centralized config** — per-repo enforcement settings stored in EvidencePlane
- **Webhook-driven** — EvidencePlane receives events directly from GitHub, not via CI scripts

The GitHub Actions integration remains the preferred submission path because CI independently
observes the commit SHA, diff, test results, and scanner outputs. The GitHub App adds the
presentation layer — it does not replace CI as the trusted signer.

---

## App Permissions

Minimum required permissions:

| Permission | Level | Reason |
| --- | --- | --- |
| Checks | Read + Write | Create and update check runs on PRs |
| Pull requests | Read | Read PR metadata for evidence linking |
| Metadata | Read | Required by GitHub for all Apps |
| Contents | Read (optional) | Avoid if possible; only needed if reading repo config files |

Do not request:
- Write access to contents (EvidencePlane never writes code)
- Organization admin permissions
- Secrets access

---

## Webhook Events

Subscribe to:

| Event | Reason |
| --- | --- |
| `installation` | Track app installs and uninstalls |
| `installation_repositories` | Track which repositories the app gains or loses |
| `check_suite` | Optionally trigger receipt submission when a suite starts |
| `workflow_run` | Optionally detect completed CI runs that submitted a receipt |
| `pull_request` | Link evidence to the correct PR |

Do not subscribe to `push` without a reason. Keep the event surface minimal.

---

## Security

**Webhook validation:**
Validate every incoming webhook with the GitHub HMAC SHA-256 signature. Reject requests
that do not include `X-Hub-Signature-256` or whose signature does not match. Use constant-time
comparison. Never log request bodies before validation.

**Credentials:**
- `GITHUB_APP_ID`: the app ID assigned by GitHub
- `GITHUB_APP_PRIVATE_KEY`: PEM-formatted RSA key, loaded from an env var or secret file
- `GITHUB_WEBHOOK_SECRET`: high-entropy secret set in the app configuration

Never hardcode these. Never log them. Store them the same way as `EVIDENCEPLANE_HMAC_SECRET`.

**Installation tokens:**
GitHub App installation tokens expire after one hour. Generate them on demand using the
private key and app ID. Cache them safely in memory or re-generate on each request.
Do not persist installation tokens to the database.

**Least privilege:**
The app should request only the permissions it uses. If PR comments are not needed in Phase A,
do not request `pull_requests: write`. Add permissions per phase.

---

## Data Model Changes

New tables likely needed:

### `github_installations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `installation_id` | int | GitHub installation ID |
| `account_login` | str | Org or user login |
| `account_type` | str | `Organization` or `User` |
| `installed_at` | datetime | UTC |
| `uninstalled_at` | datetime | UTC, nullable |

### `github_repositories`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `installation_id` | int | FK to `github_installations.installation_id` |
| `repo_id` | int | GitHub repository ID |
| `repo_full_name` | str | `owner/repo` |
| `enforce_block` | bool | Default true |
| `enforce_review` | bool | Default false |
| `added_at` | datetime | UTC |
| `removed_at` | datetime | UTC, nullable |

### `github_check_runs` (or extend `runs` table)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `run_id` | UUID | FK to `runs.id` |
| `installation_id` | int | GitHub installation ID |
| `repo_full_name` | str | `owner/repo` |
| `check_run_id` | int | GitHub check run ID |
| `head_sha` | str | Commit SHA |
| `pr_number` | int | Nullable if not from a PR |
| `created_at` | datetime | UTC |

---

## API Routes Needed

### Webhook receiver

```
POST /api/v1/github/webhook
```

- Validates `X-Hub-Signature-256`
- Dispatches to installation handler, check handler, or PR handler
- Returns `200` immediately; all work must complete synchronously or be queued

**Important:** GitHub expects a response within 10 seconds. If processing is slow, return
`200` immediately and complete the update asynchronously. For MVP, synchronous is acceptable
if response time is under 5 seconds.

### Installation management (admin only)

```
GET  /api/v1/github/installations
GET  /api/v1/github/installations/{installation_id}
GET  /api/v1/github/installations/{installation_id}/repos
PATCH /api/v1/github/repos/{repo_full_name}/settings
```

All require `Authorization: Bearer <ADMIN_TOKEN>`.

---

## UX Changes Needed

### Run detail page additions

- Link to the GitHub PR that triggered the run
- Link to the GitHub check run
- Show `installation_id` and `repo_full_name` if available

### New: repository settings page

```
GET /settings/repos
GET /settings/repos/{repo_full_name}
```

Shows per-repo enforcement settings. Admin only.

### New: GitHub installation status card

On the dashboard or a settings page, show:
- Which GitHub orgs have the app installed
- How many repositories are in scope
- Last webhook received

---

## Implementation Phases

### Phase A — Webhook + Check Runs (minimal)

Goals:
1. Receive and validate GitHub webhooks
2. Persist installations and repositories
3. When a receipt is submitted and EvidencePlane decides, create or update a GitHub check run

This phase does not add PR comments or per-repo settings UI. It proves the end-to-end loop.

Deliverables:
- `POST /api/v1/github/webhook` with signature validation
- `github_installations` and `github_repositories` tables
- Alembic migrations
- Internal service: `create_github_check_run(installation_id, repo, head_sha, decision)`
- `github_check_runs` table
- Tests: webhook signature validation, installation persistence, check creation

### Phase B — PR Comments + Per-Repo Config

Goals:
1. Post a comment on the PR with the EvidencePlane decision summary and evidence link
2. Admin UI for per-repo enforcement settings (`enforce_block`, `enforce_review`)
3. Use per-repo settings instead of global defaults when creating check runs

Deliverables:
- PR comment creation service
- `PATCH /api/v1/github/repos/{repo}/settings`
- Settings UI page
- Tests for all new paths

### Phase C — Org Dashboards + Advanced Policy Templates

Goals:
1. Org-level view: all repositories, installations, run statistics
2. Policy template system: org-wide defaults for enforcement settings
3. Better check run annotations: file-level annotations for violations

---

## Explicit Non-Goals

- EvidencePlane does not run an AI or LLM at runtime
- EvidencePlane does not store source code
- The GitHub App does not replace GitHub Actions as the CI signer
- No broad marketplace connector system
- No OAuth login for end users; admin token is the only admin authentication for MVP
- The app does not handle GitHub PR auto-merge or branch protection automation

---

## Open Questions Before Building

1. **Public or private GitHub App?**
   Private (installed manually by org admins) is safer for MVP. Public requires App Store
   listing and security review.

2. **How does the CI signer know the check run ID?**
   The check run is created by EvidencePlane when it receives the receipt. The CI job submits
   the receipt and gets back the `run_id`. EvidencePlane then updates the GitHub check run
   internally. The CI job does not need to know the GitHub check run ID.

3. **Webhook race condition?**
   If the `pull_request` webhook arrives before the CI receipt, EvidencePlane may not have
   a decision to post. The simplest approach: only create a check run when a receipt arrives,
   using the `head_sha` in the receipt to associate it with the correct PR.

4. **Private key rotation?**
   GitHub App private keys can be rotated. The rotation process needs a plan: old key remains
   valid until all in-flight tokens expire, then the new key takes over. Document this
   in the operations runbook.

5. **Rate limits?**
   GitHub API rate limits: 5,000 requests per hour per installation. For small orgs this is
   fine. For large orgs with many concurrent PRs, add basic backoff and retry.

6. **Multi-tenant future?**
   The current app is single-tenant. If multi-tenant is ever needed, the `github_installations`
   table needs a `tenant_id` or organization-isolation strategy from the start.
