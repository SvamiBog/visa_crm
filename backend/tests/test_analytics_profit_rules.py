"""Profit by month (backlog 1.18; app/core/analytics.py): the money that came in and went
out in a month, each amount on its own day. The specialist's answer to question 25: «чётко
по приходам и оплатам всего» — docs/decisions.md → «Ответы визовика (2026-11-26)»."""

from datetime import date
from decimal import Decimal

from app.core.analytics import CaseMoney, Expense, Payment, credit_day, profit_by_month
from app.models import PartnerTermsType

D = Decimal
MARCH, APRIL, MAY, JUNE = (date(2026, m, 1) for m in (3, 4, 5, 6))
OPENED = date(2026, 3, 2)
HALF_YEAR = (date(2026, 1, 1), date(2026, 6, 30))
TEN = D("10")


def months(*cases: CaseMoney, tax_rate: Decimal | None = None) -> dict[date, Decimal]:
    """The months of January–June 2026 that are not zero."""
    by_month = profit_by_month(cases, tax_rate, *HALF_YEAR)
    return {month: amount for month, amount in by_month.items() if amount}


def test_the_prepayment_and_the_rest_count_in_the_months_they_were_paid():
    # Question 25: the price is 1000, 200 paid in March, the rest and the talon in May.
    case = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        payments=(Payment(date(2026, 3, 5), D("200.00")), Payment(date(2026, 5, 12), D("800.00"))),
        registrar_fee=D("150.00"),
        registrar_fee_on=date(2026, 5, 12),
    )
    assert months(case) == {MARCH: D("200.00"), MAY: D("650.00")}


def test_every_month_of_the_period_is_there_and_an_empty_one_is_zero():
    case = CaseMoney(opened_on=OPENED, payments=(Payment(date(2026, 5, 31), D("70.00")),))
    assert profit_by_month([case], None, date(2026, 4, 10), date(2026, 6, 2)) == {
        APRIL: D("0.00"),
        MAY: D("70.00"),
        JUNE: D("0.00"),
    }
    assert profit_by_month([], None, MAY, MAY) == {MAY: D("0.00")}


def test_money_outside_the_period_is_left_out():
    # A period from the middle of a month takes only its own days, both ends included.
    case = CaseMoney(
        opened_on=OPENED,
        payments=(
            Payment(date(2026, 5, 9), D("1.00")),
            Payment(date(2026, 5, 10), D("20.00")),
            Payment(date(2026, 6, 20), D("300.00")),
            Payment(date(2026, 6, 21), D("4000.00")),
        ),
        registrar_fee=D("150.00"),
        registrar_fee_on=date(2026, 4, 30),
        expenses=(Expense(date(2026, 7, 1), D("35.00")),),
    )
    assert profit_by_month([case], None, date(2026, 5, 10), date(2026, 6, 20)) == {
        MAY: D("20.00"),
        JUNE: D("300.00"),
    }


def test_every_kind_of_money_outside_the_period_is_left_out():
    # The credit, an official payment and its tax, an expense on the day before the period;
    # the talon and the partner's payout on the day after it. Their months are in the period,
    # their days are not.
    case = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        consultation_credit=D("50.00"),
        consultation_paid_on=date(2026, 5, 9),
        consultation_held_on=date(2026, 5, 9),
        payments=(
            Payment(date(2026, 5, 9), D("100.00"), official=True),
            Payment(date(2026, 5, 10), D("20.00")),
        ),
        registrar_fee=D("150.00"),
        registrar_fee_on=date(2026, 6, 21),
        partner_terms=PartnerTermsType.FIXED,
        partner_fixed=D("60.00"),
        partner_paid_on=date(2026, 6, 21),
        expenses=(Expense(date(2026, 5, 9), D("35.00")),),
    )
    assert profit_by_month([case], TEN, date(2026, 5, 10), date(2026, 6, 20)) == {
        MAY: D("20.00"),
        JUNE: D("0.00"),
    }


def test_the_consultation_credit_counts_in_the_month_the_consultation_was_paid():
    # Paid in March, the case opened in June: March grows.
    paid = CaseMoney(
        opened_on=date(2026, 6, 3),
        consultation_credit=D("50.00"),
        consultation_paid_on=date(2026, 3, 20),
        consultation_held_on=date(2026, 2, 18),
    )
    assert credit_day(paid) == date(2026, 3, 20)
    assert months(paid) == {MARCH: D("50.00")}


def test_a_credit_without_a_payment_day_counts_on_the_day_of_the_consultation():
    held = CaseMoney(
        opened_on=date(2026, 6, 3),
        consultation_credit=D("50.00"),
        consultation_held_on=date(2026, 4, 18),
    )
    assert credit_day(held) == date(2026, 4, 18)
    assert months(held) == {APRIL: D("50.00")}


def test_a_credit_of_a_case_without_a_consultation_counts_on_the_day_it_was_opened():
    typed = CaseMoney(opened_on=date(2026, 6, 3), consultation_credit=D("50.00"))
    assert credit_day(typed) == date(2026, 6, 3)
    assert months(typed) == {JUNE: D("50.00")}


def test_a_consultation_fee_not_credited_to_the_case_stays_out():
    # The fee counts only as the credit of a case (the owner, 2026-10-28). A consultation no
    # case came from is not money of a case at all: tests/test_analytics.py proves it.
    case = CaseMoney(
        opened_on=date(2026, 6, 3),
        consultation_paid_on=date(2026, 3, 20),
        consultation_held_on=date(2026, 3, 18),
    )
    assert months(case) == {}


def test_the_tax_is_taken_in_the_month_of_the_official_payment():
    # 10% of the official 800 of May; the unofficial 200 of March is not taxed, and neither
    # is the credit.
    case = CaseMoney(
        opened_on=OPENED,
        price=D("1050"),
        consultation_credit=D("50.00"),
        payments=(
            Payment(date(2026, 3, 5), D("200.00")),
            Payment(date(2026, 5, 12), D("800.00"), official=True),
        ),
    )
    assert months(case, tax_rate=TEN) == {MARCH: D("250.00"), MAY: D("720.00")}


def test_the_tax_of_a_month_is_rounded_once():
    # 10% of 66.66 is 6.67, not 3.33 + 3.33, whatever the cases the payments belong to.
    one = CaseMoney(opened_on=OPENED, payments=(Payment(date(2026, 5, 5), D("33.33"), True),))
    two = CaseMoney(opened_on=OPENED, payments=(Payment(date(2026, 5, 25), D("33.33"), True),))
    assert months(one, two, tax_rate=TEN) == {MAY: D("59.99")}
    # Half a kopeck goes up, and the tax is rounded before it is taken: 10% of 33.25 is
    # 3.325, so 3.33 and 29.92 are left (not 29.93).
    half = CaseMoney(opened_on=OPENED, payments=(Payment(date(2026, 5, 5), D("33.25"), True),))
    assert months(half, tax_rate=TEN) == {MAY: D("29.92")}


def test_no_tax_while_the_rate_is_not_set_or_is_zero():
    case = CaseMoney(opened_on=OPENED, payments=(Payment(date(2026, 5, 12), D("800.00"), True),))
    assert months(case, tax_rate=None) == {MAY: D("800.00")}
    assert months(case, tax_rate=D("0")) == {MAY: D("800.00")}


def test_the_talon_counts_on_its_day():
    # Whoever it was paid through: a talon «paid before the CRM» is a talon with a day too
    # (the rule is not told which it is; tests/test_analytics.py sends a settled one).
    case = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 5, 12), D("800.00")),),
        registrar_fee=D("170.00"),
        registrar_fee_on=date(2026, 4, 28),
    )
    assert months(case) == {APRIL: D("-170.00"), MAY: D("800.00")}


def test_the_partner_payout_counts_in_the_month_it_was_paid():
    # 10% of the whole price, on the day it is marked paid.
    case = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        payments=(Payment(date(2026, 3, 5), D("1000.00")),),
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=TEN,
        partner_paid_on=date(2026, 6, 1),
    )
    assert months(case) == {MARCH: D("1000.00"), JUNE: D("-100.00")}


def test_a_payout_not_marked_paid_is_in_no_month():
    percent = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        payments=(Payment(date(2026, 3, 5), D("1000.00")),),
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=TEN,
    )
    fixed = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        payments=(Payment(date(2026, 4, 5), D("1000.00")),),
        partner_terms=PartnerTermsType.FIXED,
        partner_fixed=D("50.00"),
    )
    assert months(percent, fixed) == {MARCH: D("1000.00"), APRIL: D("1000.00")}


def test_a_percent_of_a_case_without_a_price_is_not_taken():
    case = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 3, 5), D("300.00")),),
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=TEN,
        partner_paid_on=date(2026, 3, 9),
    )
    assert months(case) == {MARCH: D("300.00")}


def test_a_fixed_payout_of_a_case_without_a_price_is_taken():
    case = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 3, 5), D("300.00")),),
        partner_terms=PartnerTermsType.FIXED,
        partner_fixed=D("50.00"),
        partner_paid_on=date(2026, 4, 9),
    )
    assert months(case) == {MARCH: D("300.00"), APRIL: D("-50.00")}


def test_an_expense_counts_on_the_day_it_was_spent():
    case = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 3, 5), D("300.00")),),
        expenses=(Expense(date(2026, 3, 30), D("20.00")), Expense(date(2026, 4, 1), D("35.50"))),
    )
    assert months(case) == {MARCH: D("280.00"), APRIL: D("-35.50")}


def test_a_case_without_a_price_counts_by_its_days_and_a_month_may_be_negative():
    # The talon paid in May, the client paid in June (the owner, 2026-11-26).
    case = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 6, 2), D("800.00")),),
        registrar_fee=D("150.00"),
        registrar_fee_on=date(2026, 5, 28),
    )
    assert months(case) == {MAY: D("-150.00"), JUNE: D("800.00")}


def test_amounts_count_in_the_base_currency():
    # A case priced in PLN at 0.80: the credit of 50 PLN is 40, the partner's 10% of 500 PLN
    # is 40. A payment keeps the base amount of its own day's rate (300 PLN at 0.817).
    case = CaseMoney(
        opened_on=date(2026, 5, 4),
        price=D("500"),
        rate=D("0.80"),
        consultation_credit=D("50"),
        payments=(Payment(date(2026, 5, 6), D("245.10")),),
        partner_terms=PartnerTermsType.PERCENT,
        partner_percent=TEN,
        partner_paid_on=date(2026, 6, 15),
    )
    assert months(case) == {MAY: D("285.10"), JUNE: D("-40.00")}


def test_the_period_is_the_sum_of_its_months():
    # Two cases over three months, the tax rounded month by month: 10% of the period's
    # official 66.66 at once would be 6.67, the months give 3.33 + 3.33. The total the API
    # returns is this sum (tests/test_analytics.py).
    one = CaseMoney(
        opened_on=OPENED,
        price=D("1000"),
        consultation_credit=D("50.00"),
        consultation_paid_on=date(2026, 3, 1),
        consultation_held_on=date(2026, 3, 1),
        payments=(
            Payment(date(2026, 3, 5), D("33.33"), official=True),
            Payment(date(2026, 5, 12), D("33.33"), official=True),
        ),
        registrar_fee=D("150.00"),
        registrar_fee_on=date(2026, 5, 12),
        partner_terms=PartnerTermsType.FIXED,
        partner_fixed=D("50.00"),
        partner_paid_on=date(2026, 6, 1),
    )
    two = CaseMoney(
        opened_on=OPENED,
        payments=(Payment(date(2026, 6, 30), D("400.00")),),
        expenses=(Expense(date(2026, 5, 20), D("20.00")),),
    )
    by_month = months(one, two, tax_rate=TEN)
    assert by_month == {MARCH: D("80.00"), MAY: D("-140.00"), JUNE: D("350.00")}
    assert sum(by_month.values()) == D("290.00")
