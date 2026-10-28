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
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.core.db import get_sessionmaker, scope_to_system
from app.models import ExchangeRate

NBRB_URL = "https://api.nbrb.by/exrates/rates/{code}?parammode=2&ondate={day}"
RATE_PLACES = Decimal("0.000001")
ONE = Decimal("1")
# After a failed request the bank is not asked for the same rate for a while, so a slow
# or unreachable bank does not hold up every case card that suggests a rate.
RETRY_AFTER = 600  # seconds
# Requests to the bank at once when a whole file's rates are asked for ahead.
PREFETCH_CONCURRENCY = 8
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


async def prefetch(pairs: set[tuple[str, date]], on_done: Callable[[], None]) -> None:
    """Ask the bank ahead for the rates a whole file needs, a few at a time, and cache them:
    one by one, a history of payments took minutes (backlog 1.21). A rate the bank does not
    give is remembered as a miss; the row that needs it reports it. `on_done` is called
    once for each pair, for the progress shown while the file loads."""
    pairs = {(currency, day) for currency, day in pairs if currency != "BYN"}
    if not pairs:
        return
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        cached = await session.execute(
            select(ExchangeRate.currency, ExchangeRate.on_date).where(
                tuple_(ExchangeRate.currency, ExchangeRate.on_date).in_(sorted(pairs))
            )
        )
        known = set(cached.tuples())
    for _ in known & pairs:
        on_done()
    gate = asyncio.Semaphore(PREFETCH_CONCURRENCY)
    found: dict[tuple[str, date], Decimal] = {}

    async def ask(currency: str, day: date) -> None:
        missed = _misses.get((currency, day))
        if missed is None or time.monotonic() - missed >= RETRY_AFTER:
            async with gate:
                try:
                    found[(currency, day)] = await asyncio.to_thread(_fetch, currency, day)
                    _misses.pop((currency, day), None)
                except RateUnavailable:
                    _misses[(currency, day)] = time.monotonic()
        on_done()

    await asyncio.gather(*(ask(currency, day) for currency, day in sorted(pairs - known)))
    if not found:
        return
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        await session.execute(
            insert(ExchangeRate)
            .values([{"currency": c, "on_date": d, "rate": r} for (c, d), r in found.items()])
            .on_conflict_do_nothing()
        )
        await session.commit()


async def rate_to_base(currency: str, base: str, day: date) -> Decimal:
    """Units of the base currency for one unit of `currency` (through BYN)."""
    if currency == base:
        return ONE
    rate = await byn_rate(currency, day)
    if base != "BYN":
        rate = rate / await byn_rate(base, day)
    return rate.quantize(RATE_PLACES)
