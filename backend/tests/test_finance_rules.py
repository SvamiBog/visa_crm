"""Case finance formulas (docs/clients-module.md → «Финансы»): the prototype's example
case and the edge cases around it."""

from decimal import Decimal as D

from app.core.finance import (
    Paid,
    balance,
    in_case_currency,
    partner_payout,
    profit,
    summarize,
    tax,
    tax_rate_set,
    to_base,
)
from app.models import PartnerTermsType


def test_foreign_amounts_are_converted_and_rounded_once():
    assert to_base(D("150"), D("3.40")) == D("510.00")
    assert to_base(D("60"), D("3.4")) == D("204.00")
    assert to_base(D("10.01"), D("3.37065")) == D("33.74")  # 33.7402… → kopecks
    assert to_base(D("0.03"), D("0.5")) == D("0.02")  # half up, never float


def test_partner_payout_follows_the_terms():
    price = D("1350")
    assert partner_payout(PartnerTermsType.PERCENT, price, D("10"), None) == D("135.00")
    assert partner_payout(PartnerTermsType.PERCENT, D("999"), D("12.5"), None) == D("124.88")
    assert partner_payout(PartnerTermsType.FIXED, price, None, D("50")) == D("50.00")
    assert partner_payout(PartnerTermsType.NONE, price, None, None) == D("0.00")
    # «Не указывать»: the payout is neither counted nor stored.
    assert partner_payout(PartnerTermsType.UNSPECIFIED, price, None, None) is None
    assert partner_payout(None, price, None, None) is None
    # A percent of an unknown price is unknown.
    assert partner_payout(PartnerTermsType.PERCENT, None, D("10"), None) is None


def test_tax_is_a_share_of_official_payments_only():
    assert tax([D("510"), D("90")], D("10")) == D("60.00")
    assert tax([], D("10")) == D("0.00")
    assert tax([D("333.33")], D("10")) == D("33.33")
    assert tax([D("100")], D("0")) == D("0.00")


def test_balance_counts_the_consultation_and_every_payment():
    assert balance(D("1350"), D("90"), [D("510")]) == D("750.00")
    assert balance(D("100"), D("0"), [D("60"), D("60")]) == D("-20.00")  # overpaid
    assert balance(None, D("90"), [D("510")]) is None


def test_profit_is_the_price_less_what_the_case_costs():
    # The prototype: 1350 − talon 204 − partner 135 − tax 60 = 951.
    assert profit(D("1350"), D("204"), D("135"), D("60"), []) == D("951.00")
    assert profit(D("1350"), D("204"), D("135"), D("60"), [D("40"), D("11")]) == D("900.00")
    # No talon, partner not tracked: nothing is taken for them.
    assert profit(D("500"), None, None, D("0"), []) == D("500.00")
    assert profit(None, D("204"), None, D("0"), []) is None


def test_summary_of_the_prototype_case():
    summary = summarize(
        price=D("1350"),
        currency="BYN",
        rate=D("1"),
        consultation_credit=D("90"),
        payments=[
            Paid(D("150"), "EUR", D("510"), official=True),
            Paid(D("90"), "BYN", D("90"), official=True),
        ],
        registrar_fee=D("204"),
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=D("10"),
        partner_fixed=None,
        tax_rate=D("10"),
        expenses=[],
    )
    assert summary.paid == D("600.00")
    assert summary.official == D("600.00")
    assert summary.balance == D("660.00")
    assert summary.partner_payout == D("135.00")
    assert summary.tax == D("60.00")
    assert summary.profit == D("951.00")


# --- The price in the case's own currency (pilot fixes 4 and 5, 2026-11-19) ----------
# Rates are base units for one unit, as the National Bank gives them through BYN.


def test_the_balance_is_in_the_case_currency_whatever_the_payments_came_in():
    # Base BYN; the price was named in PLN (0.80 BYN for 1 PLN); the client paid in BYN,
    # PLN and USD. A payment in the case's currency counts as it is; any other one is its
    # base amount at the case's rate.
    summary = summarize(
        price=D("800"),
        currency="PLN",
        rate=D("0.80"),
        consultation_credit=D("0"),
        payments=[
            Paid(D("150"), "BYN", D("150.00"), official=True),  # 187.50 PLN
            Paid(D("100"), "PLN", D("80.00")),  # 100 PLN as it is
            Paid(D("20"), "USD", D("62.00")),  # 77.50 PLN
        ],
        registrar_fee=None,
        partner_terms=None,
        partner_percent=None,
        partner_fixed=None,
        tax_rate=D("10"),
        expenses=[],
    )
    assert summary.paid == D("365.00")  # in PLN, like the price
    assert summary.balance == D("435.00")  # PLN
    assert summary.balance_base == D("348.00")  # BYN, for the home screen's debt
    assert summary.price_base == D("640.00")
    # Tax and profit stay in the base currency.
    assert summary.official == D("150.00")
    assert summary.tax == D("15.00")
    assert summary.profit == D("625.00")  # 640 − 15


def test_a_payment_counts_for_the_specialist_never_against_her():
    summary = _her_case(base_rate=D("1"), price=D("300"), tax_rate=D("10"))
    assert summary.paid == D("300.00") > 0
    assert summary.balance == D("0.00")
    assert summary.profit is not None and summary.profit > 0


def test_her_case_in_base_byn_tax_on_each_official_payment():
    # Price 300 BYN, 150 + 150 BYN paid officially, НПД 10%: 15 from each payment.
    summary = _her_case(base_rate=D("1"), price=D("300"), tax_rate=D("10"))
    assert summary.official == D("300.00")
    assert summary.tax == D("30.00")
    assert summary.balance == D("0.00")
    assert summary.profit == D("270.00")  # 300 − 30, no talon or expenses


def test_her_case_in_base_pln_with_the_price_in_byn():
    # Base PLN: 1.342354 PLN for 1 BYN. The price stays 300 BYN and the balance is 0 BYN;
    # tax and profit are in PLN.
    summary = _her_case(base_rate=D("1.342354"), price=D("300"), tax_rate=D("10"))
    assert summary.balance == D("0.00")
    assert summary.official == D("402.70")
    assert summary.tax == D("40.27")
    assert summary.price_base == D("402.71")  # 402.7062
    assert summary.profit == D("362.44")


def test_without_a_tax_rate_the_tax_is_not_set_rather_than_zero():
    for rate in (None, D("0")):
        summary = _her_case(base_rate=D("1"), price=D("300"), tax_rate=rate)
        assert summary.tax is None
        # Nothing is taken for a tax nobody set.
        assert summary.profit == D("300.00")
    assert not tax_rate_set(None) and not tax_rate_set(D("0")) and tax_rate_set(D("10"))


def test_without_a_price_there_is_no_balance_or_profit_never_a_loss():
    summary = _her_case(base_rate=D("1.342354"), price=None, tax_rate=D("10"))
    assert summary.paid == D("300.00")
    assert summary.balance is None and summary.balance_base is None
    assert summary.price_base is None and summary.profit is None
    assert summary.tax == D("40.27")  # the tax is owed on what came in all the same


def test_a_converted_balance_is_rounded_once():
    # Base BYN, price 300 PLN at 0.74496 BYN for 1 PLN; two payments of 100 BYN are
    # 134.2354… PLN each. Rounded once: 31.53; rounding each payment would give 31.52.
    summary = summarize(
        price=D("300"),
        currency="PLN",
        rate=D("0.744960"),
        consultation_credit=D("0"),
        payments=[Paid(D("100"), "BYN", D("100.00")), Paid(D("100"), "BYN", D("100.00"))],
        registrar_fee=None,
        partner_terms=None,
        partner_percent=None,
        partner_fixed=None,
        tax_rate=D("10"),
        expenses=[],
    )
    assert summary.balance == D("31.53")
    assert summary.paid == D("268.47")
    assert in_case_currency(Paid(D("100"), "BYN", D("100.00")), "PLN", D("0.744960")) == (
        D("100.00") / D("0.744960")
    )


def test_a_partner_percent_is_taken_from_the_price_in_the_base_currency():
    summary = summarize(
        price=D("1000"),
        currency="PLN",
        rate=D("0.80"),
        consultation_credit=D("0"),
        payments=[],
        registrar_fee=None,
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=D("10"),
        partner_fixed=None,
        tax_rate=D("10"),
        expenses=[],
    )
    assert summary.partner_payout == D("80.00")  # 10% of 800 BYN
    assert summary.profit == D("720.00")


def _her_case(*, base_rate: D, price: D | None, tax_rate: D | None):
    """The pilot's case: price in BYN, two official payments of 150 BYN; `base_rate` is
    base units for 1 BYN (1 with base BYN)."""
    return summarize(
        price=price,
        currency="BYN",
        rate=base_rate,
        consultation_credit=D("0"),
        payments=[
            Paid(D("150"), "BYN", to_base(D("150"), base_rate), official=True),
            Paid(D("150"), "BYN", to_base(D("150"), base_rate), official=True),
        ],
        registrar_fee=None,
        partner_terms=None,
        partner_percent=None,
        partner_fixed=None,
        tax_rate=tax_rate,
        expenses=[],
    )
