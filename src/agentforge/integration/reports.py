"""Bounded safe parsing of check structured reports (D12.10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from agentforge.driver.binding import sha256_hex

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_JUNIT_ELEMENTS = 20_000


class ReportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_json_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError("report_missing", str(path))
    size = path.stat().st_size
    if size > MAX_REPORT_BYTES:
        raise ReportError("report_too_large", f"{size} bytes")
    raw = path.read_bytes()
    if b"<!ENTITY" in raw.upper() or b"SYSTEM" in raw and b"<!DOCTYPE" in raw.upper():
        # Defense in depth for accidental XML dropped as .json
        raise ReportError("report_unsafe", "entity-like content refused")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportError("report_invalid", "json parse failed") from exc
    if not isinstance(data, dict):
        raise ReportError("report_invalid", "json root must be object")
    # Never treat client/agent passed flag as authoritative alone
    return data


def parse_junit_summary(path: Path) -> dict[str, Any]:
    """Parse JUnit XML with stdlib ET — forbid external entities by not resolving them.

    Prefer defusedxml when installed; fall back to ElementTree with size limits.
    """
    if not path.is_file():
        raise ReportError("report_missing", str(path))
    size = path.stat().st_size
    if size > MAX_REPORT_BYTES:
        raise ReportError("report_too_large", f"{size} bytes")
    raw = path.read_bytes()
    if b"<!ENTITY" in raw.upper():
        raise ReportError("report_unsafe", "XML entities refused")
    try:
        import defusedxml.ElementTree as ET  # type: ignore[import-untyped]
    except ImportError:
        import xml.etree.ElementTree as ET  # noqa: N812

    try:
        root = ET.fromstring(raw)  # noqa: S314 — guarded size + entity ban; defused when present
    except ParseError as exc:
        raise ReportError("report_invalid", "junit parse failed") from exc

    tests = failures = errors = skipped = 0
    stack = [(root, 0)]
    elements = 0
    while stack:
        node, depth = stack.pop()
        elements += 1
        if elements > MAX_JUNIT_ELEMENTS or depth > 64:
            raise ReportError("report_too_large", "XML limits exceeded")
        if str(node.tag).rsplit("}", 1)[-1] == "testcase":
            tests += 1
            tags = {str(c.tag).rsplit("}", 1)[-1] for c in node}
            failures += int("failure" in tags)
            errors += int("error" in tags)
            skipped += int("skipped" in tags)
        stack.extend((child, depth + 1) for child in node)
    passed = max(0, tests - failures - errors - skipped)
    return {
        "collected": tests,
        "passedCount": passed,
        "failedCount": failures,
        "errorCount": errors,
        "skippedCount": skipped,
        "reportHash": sha256_hex(
            {
                "tests": tests,
                "failures": failures,
                "errors": errors,
                "skipped": skipped,
            }
        ),
    }
