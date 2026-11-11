"""PermissionBridge — map user decisions to native options without expanding scope."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

BridgeOutcome = Literal["mapped", "mapping_unsupported", "manual_required"]

ScopeKind = Literal["file", "cmd", "net", "opaque"]


@dataclass(frozen=True)
class ParsedScope:
    kind: ScopeKind
    # file
    access: Literal["read", "write"] | None = None
    path: str | None = None
    # cmd
    executable: str | None = None
    args_digest: str | None = None
    # net
    protocol: str | None = None
    host: str | None = None
    port: str | None = None
    # opaque residual (must equal exactly)
    raw: str | None = None


@dataclass(frozen=True)
class BridgeResult:
    outcome: BridgeOutcome
    native_decision: Literal["allow", "deny"] | None
    detail: str
    mapped_options: dict[str, Any] | None = None


def _norm_path(path: str) -> str:
    text = path.replace("\\", "/").strip()
    while "//" in text:
        text = text.replace("//", "/")
    if os.name == "nt":
        text = text.casefold()
    return text.rstrip("/") or "/"


def _path_within(*, granted: str, requested: str) -> bool:
    """Segment-aware containment: repo/src covers repo/src/a, not repo/src-old."""
    g = PurePosixPath(_norm_path(granted))
    r = PurePosixPath(_norm_path(requested))
    if r == g:
        return True
    return g in r.parents


def parse_scope(scope: str) -> ParsedScope | None:
    """Parse typed scopes. Unparseable / empty → None (must not pass by default)."""
    raw = (scope or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    kind = parts[0].lower()

    if kind == "file" and len(parts) >= 3:
        access = parts[1].lower()
        if access not in {"read", "write"}:
            return None
        path = ":".join(parts[2:]).strip()
        if not path:
            return None
        return ParsedScope(kind="file", access=access, path=_norm_path(path), raw=raw)

    if kind == "cmd" and len(parts) >= 3:
        exe = parts[1].strip()
        args_digest = parts[2].strip().lower()
        if not exe or len(args_digest) < 8:
            return None
        return ParsedScope(kind="cmd", executable=exe.casefold(), args_digest=args_digest, raw=raw)

    if kind == "net" and len(parts) >= 4:
        protocol = parts[1].lower()
        host = parts[2].casefold()
        port = parts[3]
        if not protocol or not host or not port:
            return None
        return ParsedScope(kind="net", protocol=protocol, host=host, port=port, raw=raw)

    # Legacy repo:/path form used by existing ledger rows → file:write
    if kind == "repo" and len(parts) >= 2:
        path = ":".join(parts[1:]).strip()
        if not path:
            return None
        return ParsedScope(kind="file", access="write", path=_norm_path(path), raw=raw)

    # Exact opaque token only (no containment)
    return ParsedScope(kind="opaque", raw=raw)


def args_digest_for(args: list[str] | tuple[str, ...] | str) -> str:
    if isinstance(args, str):
        payload = args.encode("utf-8")
    else:
        payload = "\0".join(args).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scope_fits(*, native_scope: str, platform_scope: str) -> bool:
    """Native requested scope must be within platform-granted scope."""
    native = parse_scope(native_scope)
    platform = parse_scope(platform_scope)
    if native is None or platform is None:
        return False
    if native.kind != platform.kind:
        return False

    if native.kind == "file":
        assert native.access and platform.access and native.path and platform.path
        if platform.access == "read" and native.access == "write":
            return False
        return _path_within(granted=platform.path, requested=native.path)

    if native.kind == "cmd":
        return (
            native.executable == platform.executable
            and native.args_digest == platform.args_digest
        )

    if native.kind == "net":
        return (
            native.protocol == platform.protocol
            and native.host == platform.host
            and native.port == platform.port
        )

    return native.raw == platform.raw


def map_user_decision(
    *,
    user_decision: Literal["approve", "reject"],
    semantic: str,
    native_scope: str,
    platform_scope: str,
    native_options: list[str] | None = None,
) -> BridgeResult:
    """Map approve/reject onto product-native allow/deny.

    A once-scoped platform allow must not become session-wide native allow.
    If native options cannot be expressed precisely → mapping_unsupported.
    """
    if not native_scope.strip() or not platform_scope.strip():
        return BridgeResult(
            outcome="mapping_unsupported",
            native_decision=None,
            detail="missing scope; refuse default-allow",
        )

    if semantic == "session" and user_decision == "approve":
        # Explicit session grants require dedicated evidence; refuse silent expansion.
        if native_scope != platform_scope:
            return BridgeResult(
                outcome="mapping_unsupported",
                native_decision=None,
                detail="session approve requires exact native_scope == platform_scope",
            )

    if semantic == "once" and user_decision == "approve":
        # once must not be mapped as if session-wide
        pass

    options = {o.lower() for o in (native_options or ["allow", "deny", "approve", "reject"])}
    if user_decision == "approve":
        if not ({"allow", "approve"} & options):
            return BridgeResult(
                outcome="mapping_unsupported",
                native_decision=None,
                detail="native options lack allow/approve",
            )
        if not scope_fits(native_scope=native_scope, platform_scope=platform_scope):
            return BridgeResult(
                outcome="manual_required",
                native_decision=None,
                detail="native_scope exceeds platform_scope; refuse prompt-based safety",
            )
        return BridgeResult(
            outcome="mapped",
            native_decision="allow",
            detail="mapped approve→allow",
            mapped_options={
                "nativeDecision": "allow",
                "semantic": semantic,
                "platformScope": platform_scope,
                "nativeScope": native_scope,
                "once": semantic == "once",
            },
        )

    if not ({"deny", "reject"} & options):
        return BridgeResult(
            outcome="mapping_unsupported",
            native_decision=None,
            detail="native options lack deny/reject",
        )
    return BridgeResult(
        outcome="mapped",
        native_decision="deny",
        detail="mapped reject→deny",
        mapped_options={
            "nativeDecision": "deny",
            "semantic": semantic,
            "platformScope": platform_scope,
            "nativeScope": native_scope,
        },
    )


# Back-compat alias used by older call sites / tests
def _scope_fits(native_scope: str, platform_scope: str) -> bool:
    return scope_fits(native_scope=native_scope, platform_scope=platform_scope)
