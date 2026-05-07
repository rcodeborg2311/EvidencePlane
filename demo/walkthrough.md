# EvidencePlane Demo Walkthrough

## Setup

Start the Docker stack:

```sh
export POSTGRES_PASSWORD='replace-with-a-high-entropy-password'
export EVIDENCEPLANE_HMAC_SECRET='replace-with-a-high-entropy-secret'
export ADMIN_TOKEN='replace-with-a-high-entropy-token'
docker compose up --build
```

Open `http://127.0.0.1:8000/`.

## Flow

1. Show the empty or latest-runs dashboard.
2. Ingest the allow receipt:

```sh
.venv/bin/python scripts/post_receipt.py demo/receipts/allow.json --secret "$EVIDENCEPLANE_HMAC_SECRET"
```

3. Refresh the dashboard and open the detail page.
4. Ingest the review receipt:

```sh
.venv/bin/python scripts/post_receipt.py demo/receipts/review.json --secret "$EVIDENCEPLANE_HMAC_SECRET"
```

5. Ingest the block receipt:

```sh
.venv/bin/python scripts/post_receipt.py demo/receipts/block.json --secret "$EVIDENCEPLANE_HMAC_SECRET"
```

6. Open each detail page and download the evidence pack.

## Talk Track

EvidencePlane is not an AI runtime. It is a deterministic evidence and policy plane for coding-agent governance. The product accepts signed execution receipts, evaluates explicit rules, persists the decision and evidence, and gives reviewers a reproducible audit trail before agent changes reach protected repositories.
