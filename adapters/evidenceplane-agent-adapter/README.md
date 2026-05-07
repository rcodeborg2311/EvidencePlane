# EvidencePlane Agent Adapter Scaffold

This directory is outside the EvidencePlane core app on purpose.

It is not a full AI agent, not an MCP server, and not part of EvidencePlane's deterministic policy engine. It is a lightweight wrapper that can run a local coding-agent command, collect verifiable execution facts, build a `RunReceipt`, and submit that receipt to EvidencePlane.

EvidencePlane remains the source of deterministic policy decisions. The adapter only reports facts.

## Trust Model

Treat this adapter as less trusted than CI unless its environment is tightly controlled. A coding-agent wrapper can observe its local command and working tree, but production enforcement should prefer this pattern:

1. agent creates changes
2. CI independently verifies diff, tests, scanner output, and tool results
3. CI signs the final receipt
4. EvidencePlane returns `allow`, `review`, or `block`
5. GitHub checks and human review consume the EvidencePlane decision

## Usage

Dry run without submitting:

```sh
python adapters/evidenceplane-agent-adapter/agent_wrapper.py \
  --repo-path . \
  --agent-command 'echo agent command would run here' \
  --test-command 'python -m pytest -q' \
  --receipt-output /tmp/evidenceplane-receipt.json \
  --dry-run \
  --pretty
```

Submit to EvidencePlane:

```sh
export EVIDENCEPLANE_HMAC_SECRET='replace-with-server-secret'
python adapters/evidenceplane-agent-adapter/agent_wrapper.py \
  --repo-path . \
  --agent-command 'your-coding-agent-command' \
  --test-command 'python -m pytest -q' \
  --evidenceplane-url http://127.0.0.1:8000 \
  --receipt-output /tmp/evidenceplane-receipt.json \
  --enforce-review false \
  --enforce-block true \
  --pretty
```

Optional scanner result format:

```json
{
  "secret_detected": true,
  "paths": ["src/settings.py"]
}
```

Pass it with:

```sh
--scanner-result /path/to/scanner-result.json
```

The adapter does not store source code contents and does not log the HMAC secret.
