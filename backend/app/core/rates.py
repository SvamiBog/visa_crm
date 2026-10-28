"""Exchange rates of the National Bank of the Republic of Belarus (api.nbrb.by), kept on
the site.

A rate is BYN for one unit of a currency. The site asks the bank itself: when the web
process starts and every few hours after (`keep_fresh`, started in app/main.py), for
today's rate of each currency the CRM keeps; every rate it gets stays in `exchange_rates`,
shared by all workspaces (the system scope writes it, any scope reads it). Requests — a
case's finances, the import of a file — never ask the bank: they take the latest rate the
site has, whatever the day of the payment (owner's decision of 2026-10-28,
docs/decisions.md). A whole history in a file needed a request to the bank per day and took
minutes. A rate is only a suggestion: payments keep their own, which the specialist may
correct.
"""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import DEFAULT_TIMEZONE, today_in
from app.core.db import get_sessionmaker, scope_to_system
from app.core.money import CURRENCIES
from app.models import ExchangeRate

logger = logging.getLogger(__name__)

NBRB_URL = "https://api.nbrb.by/exrates/rates/{code}?parammode=2&ondate={day}"
RATE_PLACES = Decimal("0.000001")
ONE = Decimal("1")
# The currencies the site keeps a rate of: all the CRM keeps accounts in but BYN.
KEPT = tuple(c for c in CURRENCIES if c != "BYN")
# The bank sets a day's rate once; asking a few times a day catches it soon after.
REFRESH_EVERY = 3 * 3600  # seconds
# A currency the bank did not give is asked for again sooner: it often answers on a retry.
RETRY_MISSING = 600  # seconds
# The latest rate of each currency, with its day: read from the database once, then kept
# up by `refresh` (one web process, docs/deploy.md).
_latest: dict[str, tuple[date, Decimal]] = {}


class RateUnavailable(RuntimeError):
    """The site has no rate of the currency yet (the bank has not been reached since)."""


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


async def refresh(today: date) -> list[str]:
    """Ask the bank for today's rate of each kept currency and keep it; returns the
    currencies the bank did not give, which keep the rate the site had."""
    found: dict[str, Decimal] = {}
    for currency in KEPT:
        try:
            found[currency] = await asyncio.to_thread(_fetch, currency, today)
        except RateUnavailable:
            logger.warning("National Bank rate of %s for %s not received", currency, today)
    missing = [c for c in KEPT if c not in found]
    if not found:
        return missing
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        await session.execute(
            insert(ExchangeRate)
            .values([{"currency": c, "on_date": today, "rate": r} for c, r in found.items()])
            .on_conflict_do_nothing()
        )
        await session.commit()
    for currency, rate in found.items():
        held = _latest.get(currency)
        if held is None or held[0] <= today:
            _latest[currency] = (today, rate)
    return missing


async def keep_fresh() -> None:
    """Keep the site's rates up to date while the web process runs."""
    while True:
        try:
            missing = await refresh(today_in(DEFAULT_TIMEZONE))
        except Exception as error:  # the next round tries again; requests use what is kept
            logger.warning("Refreshing the rates failed: %s", type(error).__name__)
            missing = list(KEPT)
        await asyncio.sleep(RETRY_MISSING if missing else REFRESH_EVERY)


async def latest(currency: str) -> tuple[date, Decimal]:
    """The site's latest rate of the currency and its day; never asks the bank."""
    held = _latest.get(currency)
    if held is not None:
        return held
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        row = (
            await session.execute(
                select(ExchangeRate.on_date, ExchangeRate.rate)
                .where(ExchangeRate.currency == currency)
                .order_by(ExchangeRate.on_date.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        raise RateUnavailable(currency)
    _latest[currency] = (row.on_date, row.rate)
    return row.on_date, row.rate


async def byn_rate(currency: str) -> Decimal:
    """BYN for one unit: the site's latest rate."""
    if currency == "BYN":
        return ONE
    return (await latest(currency))[1]


async def rate_to_base(currency: str, base: str) -> Decimal:
    """Units of the base currency for one unit of `currency` (through BYN)."""
    if currency == base:
        return ONE
    rate = await byn_rate(currency)
    if base != "BYN":
        rate = rate / await byn_rate(base)
    return rate.quantize(RATE_PLACES)
