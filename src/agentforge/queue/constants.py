"""Stable task type/version identifiers for queue payloads."""

TASK_VERSION = 1
TASK_MODEL_PROBE = "model.probe"
TASK_MODEL_CALL = "model.call"
TASK_APPROVAL_FORWARD = "approval.forward"

# Outbox intent_type values
INTENT_MODEL_PROBE = "model.probe.v1"
INTENT_MODEL_CALL = "model.call.v1"
INTENT_APPROVAL_FORWARD = "approval.forward.v1"

QUEUE_SCHEMA = "queue"
DEFAULT_QUEUE_NAME = "agentforge"
