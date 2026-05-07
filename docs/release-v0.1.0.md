# EvidencePlane v0.1.0

## Scope Freeze

EvidencePlane v0.1.0 is scoped to the original MVP:

- ingest HMAC-signed `RunReceipt` JSON
- apply deterministic policy rules
- persist runs, violations, and evidence packs
- export evidence JSON with hash verification
- render dashboard and run detail pages
- run locally and through Docker Compose

Excluded from v0.1.0:

- runtime LLM calls
- embeddings or vector databases
- autonomous agents
- background workers
- multi-tenancy
- SSO
- cloud-provider-specific integrations

## Customer Discovery Prompt

Show the demo to platform, security, and engineering leaders and ask:

Would you require this evidence before letting coding agents touch production repos?

Follow-up questions:

- Which evidence fields are required for approval?
- Which violations must always block?
- Where should evidence packs be stored for audit?
- Who needs to review `review` decisions?
- What repo classes should be protected first?
