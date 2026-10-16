"""Case finance formulas (docs/clients-module.md → «Финансы»): the prototype's example
case and the edge cases around it."""

from decimal import Decimal as D

from app.core.finance import (
    balance,
    partner_payout,
    profit,
    summarize,
    tax,
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
        consultation_credit=D("90"),
        payments=[(D("510"), True), (D("90"), True)],
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
