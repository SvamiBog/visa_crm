"""Reading the machine-readable zone of a passport or an ID card (backlog 2.5):
app/core/mrz.py. Specimens are from ICAO Doc 9303 (the fictional state Utopia, UTO)."""

from datetime import date

import pytest

from app.core.mrz import MrzNotFound, check_digit, country_alpha2, read_mrz

TODAY = date(2026, 10, 17)

TD3 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
TD1 = (
    "I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<"
)
TD2 = "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<\nD231458907UTO7408122F1204159<<<<<<<6"


def with_check(field: str) -> str:
    return field + str(check_digit(field))


def belarus_passport() -> str:
    """A made-up Belarusian passport: the personal number is in the optional data."""
    number, birth, expiry, personal = "MP1234567", "850512", "300101", "3120585A001PB8"
    line2 = (
        with_check(number)
        + "BLR"
        + with_check(birth)
        + "F"
        + with_check(expiry)
        + with_check(personal)
    )
    composite = line2[0:10] + line2[13:20] + line2[21:43]
    line2 += str(check_digit(composite))
    return "P<BLRIVANOVA<<ANNA<<<<<<<<<<<<<<<<<<<<<<<<<<\n" + line2


def test_check_digits_follow_icao():
    assert check_digit("L898902C3") == 6
    assert check_digit("740812") == 2
    assert check_digit("120415") == 9
    assert check_digit("ZE184226B<<<<<") == 1
    assert check_digit("D23145890") == 7


def test_passport_specimen():
    mrz = read_mrz(TD3, TODAY)
    assert mrz.format == "TD3"
    assert (mrz.document_type, mrz.issuing_state) == ("P", "UTO")
    assert (mrz.surname, mrz.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert mrz.document_number == "L898902C3"
    assert mrz.nationality == "UTO"
    assert mrz.birth_date == date(1974, 8, 12)
    assert mrz.sex == "F"
    assert mrz.expiry_date == date(2012, 4, 15)
    assert mrz.personal_number == "ZE184226B"
    assert mrz.valid and mrz.failed == [] and mrz.corrected == []


def test_id_card_specimens():
    for text, kind in ((TD1, "TD1"), (TD2, "TD2")):
        mrz = read_mrz(text, TODAY)
        assert mrz.format == kind
        assert mrz.document_number == "D23145890"
        assert (mrz.surname, mrz.given_names) == ("ERIKSSON", "ANNA MARIA")
        assert mrz.birth_date == date(1974, 8, 12)
        assert mrz.expiry_date == date(2012, 4, 15)
        assert mrz.valid, mrz.failed


def test_belarusian_passport_with_its_personal_number():
    mrz = read_mrz(belarus_passport(), TODAY)
    assert mrz.valid, mrz.failed
    assert (mrz.surname, mrz.given_names) == ("IVANOVA", "ANNA")
    assert mrz.document_number == "MP1234567"
    assert mrz.personal_number == "3120585A001PB8"
    assert mrz.birth_date == date(1985, 5, 12)
    assert mrz.expiry_date == date(2030, 1, 1)
    assert country_alpha2(mrz.nationality) == "BY"


def test_lines_are_found_in_noisy_text():
    noisy = (
        "PASSPORT  PASSEPORT\n  utopia  \n"
        "p<utoeriksson<<anna<maria<<<<<<<<<<<<<<<<<<<\n"
        "L898902C3 6UTO 7408122F1204159ZE184226B<<<<<10\n"
        "signature"
    )
    mrz = read_mrz(noisy, TODAY)
    assert mrz.valid and mrz.document_number == "L898902C3"


def test_usual_ocr_mistakes_are_fixed_by_the_field_type():
    # O for 0 in dates and check digits, 0 for O in the country code.
    text = (
        "P<UT0ERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UT07408122F12O4159ZE184226B<<<<<1O"
    )
    mrz = read_mrz(text, TODAY)
    assert mrz.valid, mrz.failed
    assert (mrz.issuing_state, mrz.nationality) == ("UTO", "UTO")
    assert mrz.expiry_date == date(2012, 4, 15)


def test_document_number_is_fixed_only_when_the_check_digit_agrees():
    # An O read for the 0 in the number: one swap makes the check digit agree.
    text = TD3.replace("L898902C36", "L8989O2C36")
    mrz = read_mrz(text, TODAY)
    assert mrz.document_number == "L898902C3"
    assert mrz.valid and mrz.corrected == ["document_number"]


def test_a_wrong_check_digit_is_reported():
    text = TD3.replace("7408122F", "7408123F")
    mrz = read_mrz(text, TODAY)
    assert not mrz.valid
    assert mrz.failed == ["birth_date", "composite"]
    assert mrz.birth_date == date(1974, 8, 12)  # still shown, to compare with the page


def test_filler_read_as_letters_is_dropped_from_the_names():
    text = TD3.replace("MARIA<<<<<<<<<", "MARIA<<<<K<<<<")
    assert read_mrz(text, TODAY).given_names == "ANNA MARIA"
    # A line a character short at the end of the filler is padded.
    short = TD3.replace("<<<<<<<<<<<<<<<<<<<\n", "<<<<<<<<<<<<<<<<<<\n")
    assert read_mrz(short, TODAY).valid


def test_years_get_their_century():
    young = belarus_passport().replace("850512", "150512")
    # The check digits no longer agree, but the dates are read.
    mrz = read_mrz(young, TODAY)
    assert mrz.birth_date == date(2015, 5, 12)
    assert mrz.expiry_date == date(2030, 1, 1)


def test_impossible_dates_are_empty():
    mrz = read_mrz(TD3.replace("1204159", "1213159"), TODAY)
    assert mrz.expiry_date is None
    assert "expiry_date" in mrz.failed


def test_country_codes():
    assert country_alpha2("BLR") == "BY"
    assert country_alpha2("D") == "DE"
    assert country_alpha2("GBD") == "GB"
    assert country_alpha2("UTO") is None
    assert country_alpha2("XXA") is None


def test_text_without_a_zone():
    with pytest.raises(MrzNotFound):
        read_mrz("just a photo caption", TODAY)
    with pytest.raises(MrzNotFound):
        read_mrz("", TODAY)
