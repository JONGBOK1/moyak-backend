from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, AliasChoices
from uuid import uuid4

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ClientID = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
SummaryItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class MessageCreate(BaseModel):
    client_id: ClientID = Field(default_factory=lambda: uuid4().hex)
    text: NonBlank = Field(validation_alias=AliasChoices('text', 'content'))
    sender_id: str | None = None
    sender_role: str | None = None


class SummaryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symptoms: list[SummaryItem] = Field(max_length=30)
    discussion: list[SummaryItem] = Field(max_length=30)
    medication_guidance: list[SummaryItem] = Field(max_length=30)
    precautions: list[SummaryItem] = Field(max_length=30)
    follow_up: list[SummaryItem] = Field(max_length=30)
    needs_verification: list[SummaryItem] = Field(max_length=30)
