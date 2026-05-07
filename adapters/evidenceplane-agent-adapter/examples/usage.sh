#!/usr/bin/env sh
set -eu

python adapters/evidenceplane-agent-adapter/agent_wrapper.py \
  --repo-path . \
  --agent-command 'echo replace with coding-agent command' \
  --test-command 'python -m pytest -q' \
  --receipt-output /tmp/evidenceplane-agent-receipt.json \
  --dry-run \
  --pretty
