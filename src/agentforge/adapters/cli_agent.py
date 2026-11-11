"""Second candidate: generic CLI coding agent (e.g. claude/codex-style) — D03.

Passive discovery via PATH only. Does not launch the CLI, scrape credentials,
or attach to existing private sessions.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentforge.adapters.base import AdapterCapabilityMatrix
from agentforge.adapters.results import failed, outcome, unknown, unsupported

ADAPTER_ID = "cli_coding_agent"
ADAPTER_VERSION = "0.1.0"
PRODUCT_ID = "cli_coding_agent"
# Candidate binaries — presence ≠ controllable automation.
_CANDIDATE_BINARIES = ("claude", "codex", "aider")


class CliCodingAgentAdapter:
    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION

    def discover(self, environment: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if environment not in {"windows_native", "windows", "wsl"}:
            return found
        for name in _CANDIDATE_BINARIES:
            exe = shutil.which(name)
            if not exe:
                continue
            found.append(
                {
                    "productId": PRODUCT_ID,
                    "productVersion": "unknown",
                    "environmentId": environment,
                    "locationRef": exe,
                    "authStatus": "unknown",
                    "notes": [f"which:{name}", "binary_present_not_validated", "no_session_started"],
                }
            )
        return found

    def inspect(self, installation_ref: str) -> dict[str, Any]:
        path = Path(installation_ref)
        if path.exists():
            return outcome(
                "supported",
                detail="path_exists",
                installationRef=installation_ref,
                loggedIn=None,
            )
        return failed("installation path not found")

    def validate_capabilities(self, profile_id: str) -> AdapterCapabilityMatrix:
        _ = profile_id
        caps = {
            "inspect": "supported",
            "validate": "supported",
            "createSession": "unknown",
            "submitTask": "unknown",
            "events": "unsupported",
            "message": "unknown",
            "approval": "unsupported",
            "requestStop": "unknown",
            "inspectExecution": "unknown",
            "resumeOrHandoff": "unsupported",
            "close": "unknown",
            "workspaceBinding": "unknown",
            "stopConfirmation": "unknown",
            "approvalRoundtrip": "unsupported",
        }
        return AdapterCapabilityMatrix(
            product_id=PRODUCT_ID,
            product_version="unknown",
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            environment="windows_native",
            checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            capabilities=caps,  # type: ignore[arg-type]
            limits={
                "structuredEvents": "unsupported_until_evidenced",
                "nativeApproval": "platform_ledger_ready_product_roundtrip_unevidenced",
                "noPrivateSessionAttach": True,
            },
            evidence_refs=["passive_which_probe", "d04_ledger_without_product_ack"],
        )

    def create_session(self, *, profile_id: str, workspace_id: str, operation_id: str) -> dict[str, Any]:
        _ = (profile_id, workspace_id, operation_id)
        return unknown("createSession not verified in D03")

    def submit_task(self, *, session_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (session_ref, payload)
        return unsupported("submitTask not enabled until evidenced")

    def poll_events(self, *, session_ref: str, cursor: str | None = None) -> list[dict[str, Any]]:
        _ = (session_ref, cursor)
        return [unsupported("structured events not available for this candidate in D03")]

    def deliver_message(self, *, session_ref: str, message: dict[str, Any]) -> dict[str, Any]:
        _ = (session_ref, message)
        return unsupported("message delivery not verified in D03")

    def respond_approval(
        self,
        *,
        session_ref: str,
        native_request_id: str,
        decision: str,
        semantic: str | None = None,
        mapped_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = (session_ref, native_request_id, decision, semantic, mapped_options)
        return unsupported(
            "cli agent native approval round-trip not evidenced; "
            "platform ledger may mark mapping_unsupported"
        )

    def request_stop(self, *, session_ref: str, operation_id: str) -> dict[str, Any]:
        _ = (session_ref, operation_id)
        return unknown("stop confirmation not evidenced in D03")

    def inspect_execution(self, *, session_ref: str) -> dict[str, Any]:
        _ = session_ref
        return unknown("inspectExecution not evidenced in D03")

    def resume_or_handoff(self, *, session_ref: str, context: dict[str, Any]) -> dict[str, Any]:
        _ = (session_ref, context)
        return unsupported("resume/handoff not claimed in D03")

    def close_session(self, *, session_ref: str) -> dict[str, Any]:
        _ = session_ref
        return unknown("close not evidenced in D03")
