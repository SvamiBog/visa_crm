"""Money types shared by the API (docs/decisions.md → «Деньги»): Decimal only, never float."""

from decimal import Decimal
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, Field

# Currencies a specialist keeps accounts and takes payments in.
Currency = Literal["BYN", "PLN", "USD", "EUR"]
CURRENCIES: tuple[str, ...] = get_args(Currency)

CENT = Decimal("0.01")

# A positive amount stored as NUMERIC(14, 2).
Amount = Annotated[
    Decimal,
    Field(gt=0, max_digits=14, decimal_places=2),
    AfterValidator(lambda value: value.quantize(CENT)),
]
# A share of a price in percent, more than 0 and up to 100, stored as NUMERIC(5, 2).
Percent = Annotated[
    Decimal,
    Field(gt=0, le=100, max_digits=5, decimal_places=2),
    AfterValidator(lambda value: value.quantize(CENT)),
]
