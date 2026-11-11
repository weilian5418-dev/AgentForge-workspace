import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentforge.contracts.events import EventEnvelope, parse_event_payload
from agentforge.contracts.runs import RunCreateRequest
from agentforge.contracts.validations import ValidationReport

SAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "samples"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_valid_samples_parse() -> None:
    RunCreateRequest.model_validate(_load("run-create.valid.json"))
    ValidationReport.model_validate(_load("validation-report.valid.json"))
    envelope = EventEnvelope.model_validate(_load("event-run-status.valid.json"))
    parse_event_payload(envelope.type, envelope.payload)


def test_invalid_sample_rejected() -> None:
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(_load("run-create.invalid-unknown-field.json"))
