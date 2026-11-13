from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

RequestID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,80}$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class Submission(InputModel):
    request_id: RequestID
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=8000)


class Draft(InputModel):
    category: Literal["incidente", "solicitud", "consulta"]
    title: str = Field(min_length=1, max_length=140)
    summary: str = Field(min_length=1, max_length=2000)
    missing_information: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(max_length=10)


class Decision(InputModel):
    decision: Literal["approve", "reject"]


class PublicationSuccess(InputModel):
    claim_token: str = Field(min_length=1, max_length=100)
    issue_number: int = Field(gt=0)


class PublicationFailure(InputModel):
    claim_token: str = Field(min_length=1, max_length=100)


class Reconciliation(InputModel):
    issue_number: int = Field(gt=0)
