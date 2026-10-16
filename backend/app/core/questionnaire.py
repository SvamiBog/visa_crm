"""Consultation questionnaire (анкета консультации), kept on the contact so a returning
client is not asked again. Spec: docs/clients-module.md → «Анкета консультации».

Countries are ISO 3166-1 alpha-2 codes, the same as in the countries reference."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field

CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
ShortText = Annotated[str, Field(max_length=500)]

VisaKind = Literal["schengen", "national", "uk", "usa", "asia", "other"]


class VisaRecord(BaseModel):
    """A visa from the last 5 years: kind, issuing country, validity, where they went."""

    kind: VisaKind
    country: CountryCode | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    note: ShortText | None = None


class Refusal(BaseModel):
    country: CountryCode
    year: Annotated[int, Field(ge=1990, le=2100)] | None = None
    reason: ShortText | None = None


class Questionnaire(BaseModel):
    passport_issued_on: date | None = None
    passport_expires_on: date | None = None
    visas: Annotated[list[VisaRecord], Field(max_length=50)] = []
    # Countries where relatives or friends could invite the client.
    relatives_abroad: Annotated[list[CountryCode], Field(max_length=50)] = []
    refusals: Annotated[list[Refusal], Field(max_length=50)] = []
    deportation: bool = False
    deportation_note: ShortText | None = None
    # Childhood trips under humanitarian programmes (e.g. the Chernobyl programme).
    humanitarian_trips: Annotated[list[CountryCode], Field(max_length=10)] = []
    note: Annotated[str, Field(max_length=2000)] | None = None
