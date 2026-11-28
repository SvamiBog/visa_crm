"""Reading the National Bank's answer (app/core/rates.py, `_fetch`; audit H8): BYN for one
unit of a currency, the bank's rate over its scale, six places; every answer that does not
give a positive rate is «no rate» (RateUnavailable), never a wrong one.

The other tests replace `rates._fetch` with a fake (`fake_rates` in conftest.py); these
keep the real one and replace only the call to the bank below it, so no test asks
api.nbrb.by."""

import io
import json
import urllib.error
from datetime import date
from decimal import Decimal

import pytest

from app.core import rates

# Taken when the module is imported, before the autouse fixture puts its fake in.
REAL_FETCH = rates._fetch
DAY = date(2026, 11, 27)


class Bank:
    """Answers the one request `_fetch` makes with `body` (bytes), or raises `error`."""

    def __init__(self, body: bytes = b"", error: Exception | None = None) -> None:
        self.body, self.error, self.urls = body, error, []

    def urlopen(self, url: str, timeout: float) -> io.BytesIO:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.body)


def answering(monkeypatch: pytest.MonkeyPatch, body: object = None, **bank) -> Bank:
    fake = Bank(json.dumps(body).encode() if body is not None else b"", **bank)
    monkeypatch.setattr(rates.urllib.request, "urlopen", fake.urlopen)
    return fake


def answer(code: str, rate: object, scale: object = 1) -> dict:
    return {
        "Cur_ID": 451,
        "Date": "2026-11-27T00:00:00",
        "Cur_Abbreviation": code,
        "Cur_Scale": scale,
        "Cur_Name": "—",
        "Cur_OfficialRate": rate,
    }


def test_a_rate_for_one_unit_is_taken_as_the_bank_gives_it(monkeypatch: pytest.MonkeyPatch):
    bank = answering(monkeypatch, answer("EUR", 3.4012))
    assert REAL_FETCH("EUR", DAY) == Decimal("3.401200")
    # The currency's code and the day are in the request.
    assert bank.urls == [rates.NBRB_URL.format(code="EUR", day="2026-11-27")]
    assert "rates/EUR?" in bank.urls[0] and "ondate=2026-11-27" in bank.urls[0]


def test_a_rate_per_10_or_100_units_is_divided_by_the_scale(monkeypatch: pytest.MonkeyPatch):
    # PLN is quoted per 10 units: 7.4512 BYN for 10 PLN is 0.745120 for one.
    answering(monkeypatch, answer("PLN", 7.4512, 10))
    assert REAL_FETCH("PLN", DAY) == Decimal("0.745120")
    answering(monkeypatch, answer("RUB", 3.5467, 100))
    assert REAL_FETCH("RUB", DAY) == Decimal("0.035467")


def test_the_rate_is_read_as_a_decimal_and_rounded_to_six_places(
    monkeypatch: pytest.MonkeyPatch,
):
    # Never through float: 0.1 + 0.2 style errors would move kopecks.
    answering(monkeypatch, answer("USD", 3.1234567))
    rate = REAL_FETCH("USD", DAY)
    assert isinstance(rate, Decimal) and rate == Decimal("3.123457")
    answering(monkeypatch, answer("PLN", 1, 3))  # 0.3333333…
    assert REAL_FETCH("PLN", DAY) == Decimal("0.333333")


@pytest.mark.parametrize(
    "body",
    [
        answer("EUR", 0),
        answer("EUR", -3.4),
        answer("EUR", 3.4, 0),  # a scale of 0
        answer("EUR", 3.4, -10),  # a negative scale gives a negative rate
        answer("EUR", None),
        answer("EUR", "3,40"),
        {k: v for k, v in answer("EUR", 3.4).items() if k != "Cur_OfficialRate"},
        {k: v for k, v in answer("EUR", 3.4).items() if k != "Cur_Scale"},
        {},
        [answer("EUR", 3.4)],  # a list instead of one rate
        "Not found",
    ],
    ids=[
        "zero",
        "negative",
        "scale 0",
        "negative scale",
        "null rate",
        "text rate",
        "no rate",
        "no scale",
        "empty object",
        "list",
        "text",
    ],
)
def test_an_answer_without_a_positive_rate_is_no_rate(
    monkeypatch: pytest.MonkeyPatch, body: object
):
    answering(monkeypatch, body)
    with pytest.raises(rates.RateUnavailable):
        REAL_FETCH("EUR", DAY)


@pytest.mark.parametrize("body", [b"", b"<html>502 Bad Gateway</html>", b'{"Cur_Scale": 1'])
def test_an_empty_or_broken_answer_is_no_rate(monkeypatch: pytest.MonkeyPatch, body: bytes):
    monkeypatch.setattr(rates.urllib.request, "urlopen", Bank(body).urlopen)
    with pytest.raises(rates.RateUnavailable):
        REAL_FETCH("EUR", DAY)


@pytest.mark.parametrize(
    "error",
    [
        # An unknown currency: the bank answers 404.
        urllib.error.HTTPError(rates.NBRB_URL, 404, "Not Found", {}, None),  # type: ignore[arg-type]
        urllib.error.URLError("no route to host"),
        TimeoutError("timed out"),
    ],
    ids=["unknown currency", "unreachable", "timeout"],
)
def test_a_bank_that_does_not_answer_is_no_rate(monkeypatch: pytest.MonkeyPatch, error: Exception):
    answering(monkeypatch, error=error)
    with pytest.raises(rates.RateUnavailable):
        REAL_FETCH("XYZ", DAY)
