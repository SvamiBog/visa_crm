"""Exchange rates of the National Bank of the Republic of Belarus (api.nbrb.by).

A rate is BYN for one unit of a currency on a day. It is asked for once per currency and
day and kept in `exchange_rates`, shared by all workspaces; the system scope writes it,
any scope reads it. A rate is only a suggestion: payments keep their own, which the
specialist may correct.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import get_sessionmaker, scope_to_system
from app.models import ExchangeRate

NBRB_URL = "https://api.nbrb.by/exrates/rates/{code}?parammode=2&ondate={day}"
RATE_PLACES = Decimal("0.000001")
ONE = Decimal("1")
# After a failed request the bank is not asked for the same rate for a while, so a slow
# or unreachable bank does not hold up every case card that suggests a rate.
RETRY_AFTER = 600  # seconds
_misses: dict[tuple[str, date], float] = {}


class RateUnavailable(RuntimeError):
    """The National Bank has no rate for that day (yet), or cannot be reached."""


def _fetch(currency: str, day: date) -> Decimal:
    """BYN for one unit; the bank quotes some currencies per 10 or 100 units."""
    url = NBRB_URL.format(code=currency, day=day.isoformat())
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 — fixed host
            body = json.loads(response.read(), parse_float=Decimal)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise RateUnavailable(currency) from error
    try:
        rate = Decimal(body["Cur_OfficialRate"]) / Decimal(body["Cur_Scale"])
    except (KeyError, TypeError, ArithmeticError) as error:
        raise RateUnavailable(currency) from error
    if rate <= 0:
        raise RateUnavailable(currency)
    return rate.quantize(RATE_PLACES)


async def byn_rate(currency: str, day: date) -> Decimal:
    """BYN for one unit on the day: from the cache, or from the bank and then cached."""
    if currency == "BYN":
        return ONE
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        cached = await session.scalar(
            select(ExchangeRate.rate).where(
                ExchangeRate.currency == currency, ExchangeRate.on_date == day
            )
        )
        if cached is not None:
            return cached
        missed = _misses.get((currency, day))
        if missed is not None and time.monotonic() - missed < RETRY_AFTER:
            raise RateUnavailable(currency)
        try:
            rate = await asyncio.to_thread(_fetch, currency, day)
        except RateUnavailable:
            _misses[(currency, day)] = time.monotonic()
            raise
        _misses.pop((currency, day), None)
        await session.execute(
            insert(ExchangeRate)
            .values(currency=currency, on_date=day, rate=rate)
            .on_conflict_do_nothing()
        )
        await session.commit()
        return rate


async def rate_to_base(currency: str, base: str, day: date) -> Decimal:
    """Units of the base currency for one unit of `currency` (through BYN)."""
    if currency == base:
        return ONE
    rate = await byn_rate(currency, day)
    if base != "BYN":
        rate = rate / await byn_rate(base, day)
    return rate.quantize(RATE_PLACES)
