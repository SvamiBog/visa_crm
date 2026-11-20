"""Case finance formulas (docs/clients-module.md → «Финансы»): the prototype's example
case and the edge cases around it."""

from decimal import Decimal as D

from app.core.base_currency import rebased
from app.core.finance import (
    Paid,
    balance,
    converted,
    in_case_currency,
    partner_payout,
    profit,
    summarize,
    tax,
    tax_rate_set,
    to_base,
)
from app.core.rates import cross_rate
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


def test_without_a_tax_rate_the_tax_is_not_set_and_a_rate_of_zero_is_no_tax():
    # Pilot T8: NULL is «not set» (the case asks for it), 0 is her explicit «no tax».
    unset = _her_case(base_rate=D("1"), price=D("300"), tax_rate=None)
    assert unset.tax is None
    assert unset.profit == D("300.00")  # nothing is taken for a tax nobody set
    zero = _her_case(base_rate=D("1"), price=D("300"), tax_rate=D("0"))
    assert zero.tax == D("0.00")
    assert zero.profit == D("300.00")
    assert not tax_rate_set(None) and tax_rate_set(D("0")) and tax_rate_set(D("10"))


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


# The National Bank's rates as the site keeps them: BYN for one unit (pilot T8).
NBRB = {"USD": D("3.1"), "EUR": D("3.4"), "PLN": D("0.74496")}


def test_a_cross_rate_goes_through_byn_to_six_places():
    assert cross_rate("USD", "BYN", NBRB) == D("3.100000")
    assert cross_rate("BYN", "PLN", NBRB) == D("1.342354")  # 1 / 0.74496
    assert cross_rate("USD", "PLN", NBRB) == D("4.161297")  # 3.1 / 0.74496
    assert cross_rate("PLN", "PLN", NBRB) == D("1")
    assert cross_rate("BYN", "BYN", {}) == D("1")  # no rate needed


def test_a_new_base_keeps_a_rows_own_amount_and_gives_it_the_banks_rate():
    # From base PLN to base BYN: a payment in USD, one in BYN (now the base), one in PLN.
    assert rebased(D("100"), "USD", "BYN", NBRB) == (D("3.100000"), D("310.00"))
    assert rebased(D("150"), "BYN", "BYN", NBRB) == (D("1"), D("150.00"))
    assert rebased(D("333.33"), "PLN", "BYN", NBRB) == (D("0.744960"), D("248.32"))  # 248.3175
    # From base BYN to base PLN: the stored rate times the amount, rounded once.
    assert rebased(D("10.01"), "USD", "PLN", NBRB) == (D("4.161297"), D("41.65"))  # 41.6546
    assert rebased(D("150"), "BYN", "PLN", NBRB) == (D("1.342354"), D("201.35"))


def test_an_amount_kept_in_the_old_base_is_converted_and_rounded_once():
    assert converted(D("100"), "PLN", "BYN", NBRB) == D("74.50")  # 74.496
    assert converted(D("300"), "BYN", "PLN", NBRB) == D("402.71")  # 402.7062
    # Not through a rate rounded to six places first (that would give 1 657 227.15).
    assert converted(D("1234567.89"), "BYN", "PLN", NBRB) == D("1657227.09")
    assert converted(D("100"), "PLN", "EUR", NBRB) == D("21.91")  # 100 × 0.74496 / 3.4
    assert converted(D("55.55"), "EUR", "EUR", NBRB) == D("55.55")


def test_a_talon_in_a_third_currency_goes_through_byn():
    # Base PLN → EUR; the talon is in USD: 3.1 / 3.4 BYN, six places, then the amount.
    assert rebased(D("60"), "USD", "EUR", NBRB) == (D("0.911765"), D("54.71"))  # 54.7059
    assert rebased(D("60"), "PLN", "EUR", NBRB) == (D("0.219106"), D("13.15"))  # 13.14636


def test_a_hand_corrected_rate_gives_way_to_the_banks():
    # She had typed 4.5 PLN for 1 EUR (225 PLN): the new base gets the bank's, whatever it was.
    corrected = (D("4.500000"), D("225.00"))
    assert rebased(D("50"), "EUR", "BYN", NBRB) == (D("3.400000"), D("170.00"))
    assert rebased(D("50"), "EUR", "BYN", NBRB) != corrected


def test_a_partner_payout_is_converted_half_up_once():
    assert converted(D("333.33"), "PLN", "BYN", NBRB) == D("248.32")  # 248.3175
    assert converted(D("1.25"), "PLN", "BYN", {"PLN": D("0.5")}) == D("0.63")  # 0.625, up
    assert converted(D("150"), "BYN", "USD", NBRB) == D("48.39")  # 48.387
    # A cent may come out as nothing here; the change of the base keeps it a cent.
    assert converted(D("0.01"), "BYN", "USD", NBRB) == D("0.00")
