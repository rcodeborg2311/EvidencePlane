# MCP Integration Roadmap

EvidencePlane can be exposed to coding agents through an MCP adapter later, but the core application should remain the deterministic policy and evidence layer.

This repository does not implement an MCP server today. The current approved dependency set does not include an MCP SDK, and implementing the protocol directly would add avoidable risk and protocol guessing.

## Conceptual Tools

A future adapter could expose:

- `submit_run_receipt`: sign or forward a `RunReceipt` to EvidencePlane
- `get_run_decision`: fetch the stored decision for a run
- `get_evidence_pack`: retrieve the evidence JSON for audit or review
- `list_recent_runs`: list the latest stored runs

## Trust Model

MCP should be an adapter into EvidencePlane, not the source of policy truth.

EvidencePlane still makes deterministic decisions from signed receipts. Agents may submit receipts or query results, but CI or orchestrator-signed receipts are preferred because they are closer to the execution boundary and can independently observe commit SHA, diff metadata, test results, and tool exit codes.

## Implementation Guidance

If MCP support is added later, build it as a separate adapter package unless the core repository's dependency policy changes. The adapter should call the existing HTTP API instead of duplicating policy logic.

Recommended adapter behavior:

- keep HMAC signing outside model-generated text
- never expose the HMAC secret in prompts or logs
- submit raw receipt bytes exactly as signed
- treat EvidencePlane responses as policy records, not suggestions
- avoid adding LLM calls, embeddings, vector databases, or autonomous orchestration to the core EvidencePlane app
