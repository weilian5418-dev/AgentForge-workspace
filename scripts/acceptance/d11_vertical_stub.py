"""D11 minimal vertical acceptance checklist — does not claim product pass.

Usage:
  python -m scripts.acceptance.d11_vertical_stub

Requires operator judgment for real adapter / Host steps. Exits 2 when
AGENTFORGE_TEST_DATABASE_URL is unset so CI does not treat this as green product proof.
"""

from __future__ import annotations

import os
import sys

STEPS = [
    "1. Confirmed plan → durable driver thread (PG checkpoint)",
    "2. Prepare workspace/session for WP-A; reserve attempt; dispatch once",
    "3. Graph interrupt; destroy driver process/connections",
    "4. Commit trusted structured A result; freeze/verify; D10 ReleaseRecord",
    "5. Restart driver; consume durable wake; dispatch WP-B once only",
    "6. Pause during B; claim paused only after child executions stop",
    "7. Resume → recovery probe; resume_existing or explicit handoff",
    "8. B released → IntegrationHandoff; Run must NOT be completed",
]


def main() -> int:
    print("D11 vertical acceptance stub")
    print("grade: checklist-only (not product evidence)")
    for step in STEPS:
        print(f"  [ ] {step}")
    pg = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
    if not pg:
        print("blocker: AGENTFORGE_TEST_DATABASE_URL unset — PG mechanisms not proven here")
        return 2
    print("note: database URL present; still requires migrated 0017 + verified adapters")
    print("product adapters: pending unless Host reports supported resume/stop evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
