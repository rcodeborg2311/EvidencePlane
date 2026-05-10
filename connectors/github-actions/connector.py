"""GitHub Actions connector — reference implementation.

The GitHub Actions connector lives in scripts/github_actions_receipt.py
(receipt builder) and scripts/github_actions_feedback.py (decision display).

This file re-exports the main entry point for consistency with other connectors.
Run via:
  python scripts/github_actions_receipt.py [--dry-run]

Required environment variables:
  EVIDENCEPLANE_URL, EVIDENCEPLANE_HMAC_SECRET

Optional:
  EVIDENCEPLANE_SOURCE_ID, EVIDENCEPLANE_DRY_RUN
"""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    sys.argv[0] = str(scripts_dir / "github_actions_receipt.py")
    runpy.run_path(str(scripts_dir / "github_actions_receipt.py"), run_name="__main__")
