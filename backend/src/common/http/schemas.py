"""Base JSON contract: strict values, discarded unknown fields."""

from pydantic import BaseModel, ConfigDict, Field


class JSONModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class FieldError(JSONModel):
    location: list[str | int]
    code: str


class Problem(JSONModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    errors: list[FieldError] = Field(default_factory=list)
