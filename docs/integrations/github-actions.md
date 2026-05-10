# GitHub Actions Integration

EvidencePlane can receive signed `RunReceipt` JSON from GitHub Actions after CI runs. This makes CI the evidence signer instead of relying only on an agent's self-report.

## What The Workflow Does

The production-oriented example in `examples/github-actions/evidenceplane.yml`:

- checks out the repository with enough history to inspect a diff
- installs the repository's Python dependencies
- runs `python -m pytest -q --junitxml=evidenceplane-pytest.xml`
- parses JUnit XML into EvidencePlane test results
- captures changed-file metadata from `git diff --numstat`
- reads commit SHA, branch, actor, run id, and protected-branch state from GitHub context
- consumes a scanner output file, `evidenceplane-secret-scan.json`, to set `secret_detected`
- builds a contract-valid `RunReceipt`
- signs the exact receipt bytes with HMAC SHA-256
- sends the signed receipt to `POST /api/v1/runs`
- publishes the EvidencePlane decision to the GitHub check summary

The workflow does not send source code contents. It sends metadata only: paths, line counts, classifications, test status, tool command, network flag, policy context, and evidence identifiers.

## Required Secrets

Configure these GitHub repository or organization secrets before enabling the workflow:

- `EVIDENCEPLANE_URL`: the base URL of your EvidencePlane deployment, for example `https://evidenceplane.internal.example`
- `EVIDENCEPLANE_HMAC_SECRET`: the same high-entropy secret configured on the EvidencePlane server

Do not commit either value to the repository.

## Enforcement Controls

The workflow supports these repository variables:

- `EVIDENCEPLANE_ENFORCE_BLOCK`: defaults to `true`
- `EVIDENCEPLANE_ENFORCE_REVIEW`: defaults to `false`
- `EVIDENCEPLANE_RUN_DETAIL_BASE_URL`: optional base URL used for run-detail links in the check summary

Decision behavior:

- `allow`: workflow succeeds
- `review`: workflow emits a warning and succeeds unless `EVIDENCEPLANE_ENFORCE_REVIEW=true`
- `block`: workflow fails unless `EVIDENCEPLANE_ENFORCE_BLOCK=false`

GitHub Actions does not have a native "requires manual review" result that can be emitted from a normal job without additional integration. When review enforcement is enabled, EvidencePlane fails the workflow so the pull request cannot merge until a reviewer resolves the underlying issue and reruns CI.

## Optional Secret Scanner Hook

EvidencePlane remains scanner-agnostic. The CI workflow is responsible for running the scanner and sending only the result.

Set a repository variable named `EVIDENCEPLANE_SECRET_SCAN_COMMAND` to a command that writes `evidenceplane-secret-scan.json`. For example:

```sh
gitleaks detect --source . --report-format json --report-path evidenceplane-secret-scan.json --exit-code 0
```

**Important:** `EVIDENCEPLANE_SECRET_SCAN_COMMAND` is a repository-level variable. It runs on
every branch and every PR. If the scanner reports a finding, EvidencePlane will block that run —
including pull requests that touch only documentation or tests. Only set this variable if you
want the scanner active for all branches. For pilot demos or testing, prefer committing a
`evidenceplane-secret-scan.json` file directly in the branch that should simulate a block,
rather than setting a global scanner command.

The receipt builder understands common scanner-style JSON:

- a non-empty JSON list of findings
- `{"secret_detected": true}`
- `{"secrets_detected": true}`
- `{"findings": [...]}`
- `{"results": [...]}`
- JSON lines where each line is a finding object

If the finding path can be mapped to a changed file, only that file is marked with `secret_detected: true`. If a scanner reports findings without file paths, the receipt marks the first changed file as secret-bearing so the deterministic policy blocks the run.

## GitHub Check Behavior

The workflow job itself becomes the visible governance check:

- `allow` returns success
- `review` returns success with a GitHub warning and a check summary explaining the review reason
- `block` returns failure

The check summary includes:

- decision
- policy version
- risk score
- review status
- next action
- run id
- evidence SHA-256
- EvidencePlane detail URL
- violations

This creates a practical PR gate without adding GitHub-specific logic to the EvidencePlane server.

## Install The Example

1. Copy `examples/github-actions/evidenceplane.yml` into the target repository at `.github/workflows/evidenceplane.yml`.
2. Copy these helper scripts into the target repository's `scripts/` directory:
   - `scripts/submit_receipt.py`
   - `scripts/github_actions_receipt.py`
   - `scripts/github_actions_feedback.py`
3. Configure `EVIDENCEPLANE_URL` and `EVIDENCEPLANE_HMAC_SECRET`.
4. Optionally configure `EVIDENCEPLANE_SECRET_SCAN_COMMAND`.
5. Adjust the install and test commands if the target repository does not use Python and pytest.
6. Run the workflow on a pull request or push.
7. Open the EvidencePlane dashboard and confirm the run appears with an `allow`, `review`, or `block` decision.
8. Inspect the GitHub check summary for the EvidencePlane decision and evidence link.

## Signature Flow

The workflow serializes compact JSON and signs those exact bytes:

```text
signature = hmac_sha256(EVIDENCEPLANE_HMAC_SECRET, raw_receipt_json_bytes)
```

The signature is sent in `X-EvidencePlane-Signature`. EvidencePlane recomputes the HMAC over the received request body and rejects invalid signatures with `401 invalid_signature`.

The byte-level signing rule matters: a receipt must be signed exactly as it is sent. Reformatting JSON after signing will produce a different signature.

## Why CI Should Sign

Coding agents are useful sources of intent, but they should not be the only trusted narrator of what happened. CI is closer to the repository and runner boundary:

- it sees the checked-out commit SHA
- it can run tests and record real exit codes
- it can inspect the Git diff independently
- it can apply organization-controlled secrets
- it can submit evidence even when the agent did not cooperate

Agent-submitted receipts can still be useful, but CI or orchestrator-signed receipts should be preferred for policy enforcement.

## Current Limitations

- Changed-file metadata is derived from `git diff --numstat`.
- File classification is heuristic and based on file extensions.
- Secret detection depends on an external scanner configured in CI.
- Tool-call capture depends on the runner or wrapper. The example records the pytest command and exit code.
- The example is Python/pytest-oriented and should be adapted for other stacks.
- No source code blobs are sent.
- EvidencePlane does not run AI at runtime. It validates receipts, applies deterministic policy rules, stores evidence, and renders review pages.
