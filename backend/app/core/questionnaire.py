"""Consultation questionnaire (анкета консультации), kept on the contact so a returning
client is not asked again. Spec: docs/clients-module.md → «Анкета консультации».

Countries are ISO 3166-1 alpha-2 codes, the same as in the countries reference."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

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


# A passport or personal number as in the machine-readable zone: letters and digits.
DocumentNumber = Annotated[str, Field(max_length=20, pattern=r"^[A-Z0-9]+$")]


def _document_number(value: object) -> object:
    if not isinstance(value, str):
        return value
    return value.replace(" ", "").upper() or None


class Passport(BaseModel):
    """The passport's data page, for visa forms: typed in with the client's card or
    taken from the machine-readable zone (backlog 2.5). The name in Latin and the date of
    birth are on the contact itself."""

    passport_number: DocumentNumber | None = None
    passport_issued_on: date | None = None
    passport_expires_on: date | None = None
    # The authority that issued it, as written in the passport.
    passport_issued_by: Annotated[str, Field(max_length=200)] | None = None
    nationality: CountryCode | None = None
    sex: Literal["M", "F", "X"] | None = None
    # The national identity number: in Belarus, the identification number.
    personal_number: DocumentNumber | None = None
    birth_place: Annotated[str, Field(max_length=200)] | None = None

    _numbers = field_validator("passport_number", "personal_number", mode="before")(
        _document_number
    )

    @field_validator("passport_issued_by", "birth_place")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return " ".join((value or "").split()) or None


class Questionnaire(Passport):
    """The passport is kept with the answers: its dates are what the checks look at."""

    visas: Annotated[list[VisaRecord], Field(max_length=50)] = []
    # Countries where relatives or friends could invite the client.
    relatives_abroad: Annotated[list[CountryCode], Field(max_length=50)] = []
    refusals: Annotated[list[Refusal], Field(max_length=50)] = []
    deportation: bool = False
    deportation_note: ShortText | None = None
    # Childhood trips under humanitarian programmes (e.g. the Chernobyl programme).
    humanitarian_trips: Annotated[list[CountryCode], Field(max_length=10)] = []
    note: Annotated[str, Field(max_length=2000)] | None = None
