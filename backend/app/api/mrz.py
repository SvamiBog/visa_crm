"""Reading a passport's machine-readable zone (backlog 2.5): closed by the «passport_mrz»
module. Takes text only — typed, pasted or recognised from a photo in the browser, so
the photo never reaches the server — and stores nothing: the specialist moves what is
needed into the card, and that change goes to the audit log like any other.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.mrz import MrzNotFound, country_alpha2, read_mrz

router = APIRouter(
    prefix="/mrz", tags=["mrz"], dependencies=[Depends(require_module("passport_mrz"))]
)

Member = Annotated[CurrentUser, Depends(require_module("passport_mrz"))]


class MrzIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=5000)]


class MrzOut(BaseModel):
    format: str
    document_type: str
    issuing_state: str
    # ISO codes as in the countries reference; None for stateless persons, UN documents
    # and the ICAO specimen.
    issuing_country: str | None
    surname: str
    given_names: str
    # As in the card's «ФИО латиницей»: surname, then given names.
    full_name_latin: str
    document_number: str
    nationality: str
    nationality_country: str | None
    birth_date: date | None
    sex: str | None
    expiry_date: date | None
    expired: bool
    personal_number: str | None
    lines: list[str]
    valid: bool
    failed: list[str]
    corrected: list[str]


@router.post("/read")
async def read(body: MrzIn, user: Member, session: TenantSession) -> MrzOut:
    assert user.tenant_id is not None
    today = await workspace_today(session, user.tenant_id)
    try:
        mrz = read_mrz(body.text, today)
    except MrzNotFound as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "mrz_not_found") from error
    return MrzOut(
        format=mrz.format,
        document_type=mrz.document_type,
        issuing_state=mrz.issuing_state,
        issuing_country=country_alpha2(mrz.issuing_state),
        surname=mrz.surname,
        given_names=mrz.given_names,
        full_name_latin=f"{mrz.surname} {mrz.given_names}".strip(),
        document_number=mrz.document_number,
        nationality=mrz.nationality,
        nationality_country=country_alpha2(mrz.nationality),
        birth_date=mrz.birth_date,
        sex=mrz.sex,
        expiry_date=mrz.expiry_date,
        expired=mrz.expiry_date is not None and mrz.expiry_date < today,
        personal_number=mrz.personal_number,
        lines=mrz.lines,
        valid=mrz.valid,
        failed=mrz.failed,
        corrected=mrz.corrected,
    )
