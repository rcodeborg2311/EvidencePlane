# Pilot Demo Plan

Use a real GitHub repository with the EvidencePlane workflow installed. Configure `EVIDENCEPLANE_URL`, `EVIDENCEPLANE_HMAC_SECRET`, and, if available, `EVIDENCEPLANE_SECRET_SCAN_COMMAND`.

Prepare three pull requests:

1. Safe docs change
   - Change only a Markdown file.
   - Keep tests passing.
   - Expected EvidencePlane decision: `allow`.

2. Code change with no passed tests
   - Change a source file.
   - Skip or omit tests in the workflow variant used for the demo.
   - Expected EvidencePlane decision: `review`.

3. Blocked change
   - Either make tests fail or configure the scanner hook to report a finding.
   - Expected EvidencePlane decision: `block`.

Demo path:

1. Open the pull request and show the EvidencePlane check.
2. Open the check summary and point to decision, next action, risk score, policy version, review status, run id, evidence SHA-256, and violation codes.
3. Open the EvidencePlane dashboard and show the run ledger.
4. Open the run detail page and show changed files, tests, tool calls, policy outcome, human review state, and evidence integrity.
5. For the `review` run, approve or reject the pending review in the UI with an admin token.
6. Download the evidence JSON and show `policy_version`, review fields, and `evidence_sha256`.

Pilot script:

1. Safe docs change -> expect `allow`.
2. Code change with no passed tests -> expect `review`.
3. Fake scanner result -> expect `block`.
4. Approve or reject the review in the run detail UI.
5. Show the GitHub Actions summary.
6. Download the evidence pack.

Keep the positioning narrow: EvidencePlane is not the coding agent and not an AI runtime. It is the deterministic evidence and policy checkpoint that CI or orchestrators report into.
