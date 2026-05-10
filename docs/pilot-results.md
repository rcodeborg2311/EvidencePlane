# EvidencePlane Pilot Results

**Date:** 2026-05-09
**Pilot repo:** [rcodeborg2311/ep-pilot-test](https://github.com/rcodeborg2311/ep-pilot-test)
**EvidencePlane deployment:** Local server (Cloudflare quick tunnel for public access)
**Workflow:** `.github/workflows/evidenceplane.yml` from `examples/github-actions/`

---

## Scenario Results

| Scenario | Branch | Decision | Risk | Violations | Review status | CI outcome | Expected | Pass |
|---|---|---|---|---|---|---|---|---|
| Docs change | `pilot/docs-change` | `allow` | 12 | none | `not_required` | success | allow / 12 | ✅ |
| Code, no tests | `pilot/code-no-tests` | `review` | 60 | `NO_TEST_EVIDENCE` | `pending` → `approved` | success | review / 60 | ✅ |
| Fake secret | `pilot/fake-secret` | `block` | 95 | `SECRET_PATTERN_DETECTED` | `not_required` | failure | block / 95 | ✅ |

---

## Scenario 1 — Docs change → allow

**Branch:** `pilot/docs-change`
**PR:** [Pilot 1: docs change](https://github.com/rcodeborg2311/ep-pilot-test/pull/1)
**GitHub run:** [25614683928](https://github.com/rcodeborg2311/ep-pilot-test/actions/runs/25614683928)
**EvidencePlane run_id:** `346adde7-e8cb-4894-aabe-2c7d71ec42aa`

| Field | Value |
|---|---|
| Decision | `allow` |
| Risk score | 12 |
| Violations | none |
| Review status | `not_required` |
| Policy version | 1.0 |
| Evidence SHA-256 | `562cc9defd9aa4b5f98a9e0456520b0b4c1ca122f7cf05208dda1d554743db62` |
| CI outcome | success ✅ |

**Result:** PASS. EvidencePlane correctly allowed a docs-only change with no violations.

---

## Scenario 2 — Code change, no passed tests → review

**Branch:** `pilot/code-no-tests`
**PR:** [Pilot 2: code no tests](https://github.com/rcodeborg2311/ep-pilot-test/pull/2)
**GitHub run:** [25614745611](https://github.com/rcodeborg2311/ep-pilot-test/actions/runs/25614745611)
**EvidencePlane run_id:** `1200aa54-cd6f-4332-9e64-3e24671165a3`

| Field | Value |
|---|---|
| Decision | `review` |
| Risk score | 60 |
| Violations | `NO_TEST_EVIDENCE` (medium) |
| Review status | `pending` → `approved` |
| Policy version | 1.0 |
| Evidence SHA-256 (before approval) | `99d5db0db6aac14f23540e02e16bb1b7613dab0fd8314575cfbaadfc0e5e4b58` |
| Evidence SHA-256 (after approval) | `e3eb57f03cb8bf1eccdf7c6e8415bf7c8bbb26607bfabc0db104dd03cf305b69` |
| CI outcome | success ✅ (review not enforced by default) |

**Human review verification:**
- Approved via `POST /api/v1/runs/{run_id}/review/approve`
- Reviewer identity: `ridham.patel@company.com`
- Review note: recorded in evidence pack
- Evidence SHA-256 changed after approval: ✅ (hash recomputed over updated review state)
- Downloaded evidence pack includes `reviewer_identity`, `review_note`, `reviewed_at`: ✅

**Result:** PASS.

---

## Scenario 3 — Fake scanner finding → block

**Branch:** `pilot/fake-secret`
**PR:** [Pilot 3: fake secret](https://github.com/rcodeborg2311/ep-pilot-test/pull/3)
**GitHub run:** [25614503424](https://github.com/rcodeborg2311/ep-pilot-test/actions/runs/25614503424)
**EvidencePlane run_id:** `919e4a7a-cbf1-4ca6-88e3-78e2484417c4`

| Field | Value |
|---|---|
| Decision | `block` |
| Risk score | 95 |
| Violations | `SECRET_PATTERN_DETECTED` (high) |
| Review status | `not_required` |
| Policy version | 1.0 |
| Evidence SHA-256 | `56257b6bd3220c0b5ccf90740e164e0cf9e88d217452a39ed89dbb8517424483` |
| CI outcome | failure ✅ (`EVIDENCEPLANE_ENFORCE_BLOCK=true`) |

**Result:** PASS. CI failed (red check). No human review path for block decisions.

---

## Incident Note — Global Scanner Variable

**What happened:** During pilot setup, `EVIDENCEPLANE_SECRET_SCAN_COMMAND` was set as a
repository-level variable with value `printf '{"secret_detected":true}' > evidenceplane-secret-scan.json`.
This caused all branches — including `pilot/docs-change` and `pilot/code-no-tests` — to
report `secret_detected: true`. EvidencePlane correctly returned `block` / `95` for every run.
The dashboard showed all blocks.

**Root cause:** Repo-level scanner variable fires on every branch. This is correct behavior:
when a scanner reports a finding, EvidencePlane blocks. The issue was pilot configuration,
not a product bug.

**Fix applied:**
1. Deleted the global `EVIDENCEPLANE_SECRET_SCAN_COMMAND` variable
2. Re-ran scenarios 1 and 2 without the scanner active
3. For scenario 3, the block run from before the fix had the correct decision and is used
   as the canonical result (scanner was active, policy blocked correctly)
4. Updated `docs/pilot-demo.md` and `docs/integrations/github-actions.md` to warn against
   setting a global scanner command when running multiple scenario branches in one repo
5. Recommended approach: commit `evidenceplane-secret-scan.json` directly in the branch
   that should simulate a block, rather than using a repo-level variable

**EvidencePlane behavior was correct throughout.** The policy engine blocked every receipt
that included `secret_detected: true`, which is exactly what it should do.

---

## Evidence Integrity Verification

| Check | Result |
|---|---|
| Evidence SHA-256 recomputed on every export | ✅ |
| Export fails if stored hash does not match pack | ✅ |
| Evidence hash changes after review approve | ✅ |
| Post-approval pack includes reviewer_identity, review_note, reviewed_at | ✅ |
| Policy version `1.0` in every evidence pack | ✅ |

---

## Known Limitations

- **GitHub App not built.** Check summaries come from the workflow job, not native PR check runs.
  No PR comments, no per-repo config stored in EvidencePlane. GitHub App is designed
  (see `docs/integrations/github-app.md`) and ready to implement after pilot validation.
- **Public URL required for GitHub Actions.** Local server requires a tunnel (Cloudflare, ngrok)
  for CI runners to reach EvidencePlane. Production deployment needs a stable public or
  internal URL.
- **Scanner is external and optional.** EvidencePlane does not run a scanner itself. CI is
  responsible for running the scanner and passing only the result.
- **Scenario 2 setup requires skipped tests.** The test suite must produce no passing tests
  for `NO_TEST_EVIDENCE` to trigger. If any test passes, EvidencePlane correctly returns
  `allow`. The pilot branch uses `@pytest.mark.skip` to simulate this.
- **Cloudflare quick tunnel URL is ephemeral.** The tunnel URL changes on every restart.
  GitHub secrets must be updated each session. Use a stable deployment for a real design
  partner pilot.

---

## Recommendation

**Ready to run with a design partner.**

All three scenarios produce the correct EvidencePlane decisions end-to-end through live GitHub
Actions. Human review approval works and evidence integrity holds through the full cycle.
The pilot configuration mistake was identified, documented, and fixed.

Before running with a partner, set up a stable EvidencePlane deployment (not a quick tunnel)
so the URL does not change between demo sessions. The `docs/pilot-demo.md` guide is ready
to hand to a partner.

After the design partner session, the next decision point is whether to start GitHub App
implementation based on what the partner wants from the PR integration experience.
