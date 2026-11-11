"""Versioned public JSON models."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    """Shared contract base: camelCase JSON, reject unknown fields.

    Python construction may use field names (`populate_by_name=True`).
    HTTP request bodies should use :class:`ApiInputModel` so only documented
    aliases are accepted from external JSON.
    """

    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ApiInputModel(ContractModel):
    """External JSON input: accept documented camelCase aliases only."""

    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=False,
    )
