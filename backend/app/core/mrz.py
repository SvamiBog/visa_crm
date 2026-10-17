"""The machine-readable zone of a passport or an ID card (backlog 2.5; ICAO Doc 9303).

Takes the text of the zone — typed, pasted or recognised from a photo in the browser —
and returns the fields with their check digits verified. The photo itself never reaches
the server (docs/decisions.md → «Распознавание паспорта»).

Formats: TD3 (passports: two lines of 44), TD2 (two lines of 36) and TD1 (ID cards: three
lines of 30). Recognition mixes up look-alike characters, so each field is read by its
type: digits in dates and check digits, letters in names and country codes. A document
number is alphanumeric; a look-alike there is swapped only when the check digit agrees.
"""

import re
from dataclasses import dataclass, field
from datetime import date

FILLER = "<"
ALLOWED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")
# What recognition reads instead of a digit, and instead of a letter.
TO_DIGIT = str.maketrans("OQDILZSGB", "000112568")
TO_LETTER = str.maketrans("0125689", "OIZSGBB")
LOOK_ALIKE = {
    "0": "O",
    "O": "0",
    "1": "I",
    "I": "1",
    "2": "Z",
    "Z": "2",
    "5": "S",
    "S": "5",
    "6": "G",
    "G": "6",
    "8": "B",
    "B": "8",
}
WEIGHTS = (7, 3, 1)
# Checks in the order they are reported.
CHECKS = ("document_number", "birth_date", "expiry_date", "personal_number", "composite")


class MrzNotFound(ValueError):
    """No lines of a machine-readable zone in the text."""


def _value(char: str) -> int:
    if char.isdigit():
        return int(char)
    if char == FILLER:
        return 0
    return ord(char) - ord("A") + 10


def check_digit(data: str) -> int:
    """ICAO 9303: weights 7, 3, 1; letters A–Z are 10–35, the filler is 0."""
    return sum(_value(c) * WEIGHTS[i % 3] for i, c in enumerate(data)) % 10


def _digit_char(char: str) -> int | None:
    char = char.translate(TO_DIGIT)
    if char == FILLER:
        return 0
    return int(char) if char.isdigit() else None


def _checks(data: str, digit: str) -> bool:
    return _digit_char(digit) == check_digit(data)


@dataclass
class MrzReading:
    format: str  # TD3, TD2 or TD1
    document_type: str
    issuing_state: str  # ICAO code: BLR, D for Germany, UTO for the specimen
    surname: str
    given_names: str
    document_number: str
    nationality: str
    birth_date: date | None
    sex: str | None  # M, F or X
    expiry_date: date | None
    personal_number: str | None
    lines: list[str]
    # Checks that did not agree, in CHECKS order.
    failed: list[str] = field(default_factory=list)
    # Fields fixed by swapping a look-alike so that the check digit agrees.
    corrected: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.failed


# --- Finding the lines -----------------------------------------------------------------


def _clean(line: str) -> str:
    line = line.upper().replace("«", FILLER + FILLER).replace("‹", FILLER)
    return "".join(c for c in line if c in ALLOWED)


def _fit(line: str, length: int) -> str | None:
    """A names line a couple of fillers short or long still fits: its end is filler."""
    if len(line) == length:
        return line
    if length - 2 <= len(line) < length:
        return line + FILLER * (length - len(line))
    if length < len(line) <= length + 2 and line[length:].strip(FILLER) == "":
        return line[:length]
    return None


def _find(text: str) -> tuple[str, list[str]]:
    lines = [c for c in (_clean(raw) for raw in text.splitlines()) if len(c) >= 28]
    for i in range(len(lines)):
        # TD3 and TD2: the names line, then the data line of exact length.
        for kind, length in (("TD3", 44), ("TD2", 36)):
            if i + 1 < len(lines) and len(lines[i + 1]) == length:
                first = _fit(lines[i], length)
                if first is not None:
                    return kind, [first, lines[i + 1]]
        # TD1: two data lines of 30, then the names.
        if i + 2 < len(lines) and len(lines[i]) == 30 and len(lines[i + 1]) == 30:
            third = _fit(lines[i + 2], 30)
            if third is not None:
                return "TD1", [lines[i], lines[i + 1], third]
    raise MrzNotFound


# --- Reading the fields ----------------------------------------------------------------


def _letters(value: str) -> str:
    return value.translate(TO_LETTER)


def _digits(value: str) -> str:
    return value.translate(TO_DIGIT)


def _names(value: str) -> tuple[str, str]:
    """Surname and given names. Three fillers in a row start the padding: anything after
    them is the padding read as letters."""
    value = _letters(value)
    end = value.find(FILLER * 3)
    if end > 0:
        value = value[:end]
    surname, _, given = value.partition(FILLER * 2)
    return surname.replace(FILLER, " ").strip(), given.replace(FILLER, " ").strip()


def _date(value: str, today: date, *, birth: bool) -> date | None:
    if not value.isdigit():
        return None
    yy, mm, dd = int(value[0:2]), int(value[2:4]), int(value[4:6])
    for century in (2000, 1900):
        try:
            day = date(century + yy, mm, dd)
        except ValueError:
            return None
        # A birth date is never in the future; an expiry date is this century unless
        # that puts it more than 50 years ahead.
        if (birth and day <= today) or (not birth and day.year <= today.year + 50):
            return day
    return None


def _sex(value: str) -> str | None:
    return {"M": "M", "F": "F", FILLER: "X", "X": "X"}.get(value)


def _fix_number(number: str, digit: str, still_ok) -> tuple[str, bool]:
    """Swap one look-alike in the number if exactly one swap makes the checks agree."""
    if _checks(number, digit):
        return number, False
    found = []
    for i, char in enumerate(number):
        if char in LOOK_ALIKE:
            candidate = number[:i] + LOOK_ALIKE[char] + number[i + 1 :]
            if _checks(candidate, digit):
                found.append(candidate)
    if len(found) > 1:
        found = [c for c in found if still_ok(c)]
    return (found[0], True) if len(found) == 1 else (number, False)


@dataclass
class _Layout:
    """Where the data line keeps its fields (slices into the data line)."""

    number: slice
    number_check: int
    nationality: slice
    birth: slice
    birth_check: int
    sex: int
    expiry: slice
    expiry_check: int
    optional: slice
    optional_check: int | None
    composite_check: int
    composite: tuple[slice, ...]


TD3 = _Layout(
    number=slice(0, 9),
    number_check=9,
    nationality=slice(10, 13),
    birth=slice(13, 19),
    birth_check=19,
    sex=20,
    expiry=slice(21, 27),
    expiry_check=27,
    optional=slice(28, 42),
    optional_check=42,
    composite_check=43,
    composite=(slice(0, 10), slice(13, 20), slice(21, 43)),
)
TD2 = _Layout(
    number=slice(0, 9),
    number_check=9,
    nationality=slice(10, 13),
    birth=slice(13, 19),
    birth_check=19,
    sex=20,
    expiry=slice(21, 27),
    expiry_check=27,
    optional=slice(28, 35),
    optional_check=None,
    composite_check=35,
    composite=(slice(0, 10), slice(13, 20), slice(21, 35)),
)


def _splice(line: str, where: slice | int, value: str) -> str:
    start = where.start if isinstance(where, slice) else where
    return line[:start] + value + line[start + len(value) :]


def read_mrz(text: str, today: date) -> MrzReading:
    kind, lines = _find(text)
    if kind == "TD1":
        return _read_td1(lines, today)
    layout = TD3 if kind == "TD3" else TD2
    names, data = lines
    # Digits where digits must be, so the check digits are counted on what was meant.
    for part in (layout.birth, layout.expiry):
        data = _splice(data, part, _digits(data[part]))
    data = _splice(data, layout.nationality, _letters(data[layout.nationality]))

    def composite_ok(line: str) -> bool:
        return _checks("".join(line[s] for s in layout.composite), line[layout.composite_check])

    number, fixed = _fix_number(
        data[layout.number],
        data[layout.number_check],
        lambda candidate: composite_ok(_splice(data, layout.number, candidate)),
    )
    data = _splice(data, layout.number, number)
    optional = data[layout.optional]
    checks = {
        "document_number": _checks(number, data[layout.number_check]),
        "birth_date": _checks(data[layout.birth], data[layout.birth_check]),
        "expiry_date": _checks(data[layout.expiry], data[layout.expiry_check]),
        "personal_number": layout.optional_check is None
        or _checks(optional, data[layout.optional_check]),
        "composite": composite_ok(data),
    }
    surname, given = _names(names[5:])
    reading = MrzReading(
        format=kind,
        document_type=_letters(names[0:2]).strip(FILLER),
        issuing_state=_letters(names[2:5]).strip(FILLER),
        surname=surname,
        given_names=given,
        document_number=number.strip(FILLER),
        nationality=data[layout.nationality].strip(FILLER),
        birth_date=_date(data[layout.birth], today, birth=True),
        sex=_sex(data[layout.sex]),
        expiry_date=_date(data[layout.expiry], today, birth=False),
        personal_number=optional.strip(FILLER) or None,
        lines=[names, data],
        corrected=["document_number"] if fixed else [],
    )
    return _finish(reading, checks)


def _read_td1(lines: list[str], today: date) -> MrzReading:
    first, data, names = lines
    for part in (slice(0, 6), slice(8, 14)):
        data = _splice(data, part, _digits(data[part]))
    data = _splice(data, slice(15, 18), _letters(data[15:18]))

    def composite_ok(line1: str) -> bool:
        return _checks(line1[5:30] + data[0:7] + data[8:15] + data[18:29], data[29])

    number, fixed = _fix_number(
        first[5:14], first[14], lambda candidate: composite_ok(_splice(first, 5, candidate))
    )
    first = _splice(first, 5, number)
    checks = {
        "document_number": _checks(number, first[14]),
        "birth_date": _checks(data[0:6], data[6]),
        "expiry_date": _checks(data[8:14], data[14]),
        "personal_number": True,
        "composite": composite_ok(first),
    }
    surname, given = _names(names)
    optional = first[15:30].strip(FILLER) or data[18:29].strip(FILLER)
    reading = MrzReading(
        format="TD1",
        document_type=_letters(first[0:2]).strip(FILLER),
        issuing_state=_letters(first[2:5]).strip(FILLER),
        surname=surname,
        given_names=given,
        document_number=number.strip(FILLER),
        nationality=data[15:18].strip(FILLER),
        birth_date=_date(data[0:6], today, birth=True),
        sex=_sex(data[7]),
        expiry_date=_date(data[8:14], today, birth=False),
        personal_number=optional or None,
        lines=[first, data, names],
        corrected=["document_number"] if fixed else [],
    )
    return _finish(reading, checks)


def _finish(reading: MrzReading, checks: dict[str, bool]) -> MrzReading:
    if reading.birth_date is None:
        checks["birth_date"] = False
    if reading.expiry_date is None:
        checks["expiry_date"] = False
    reading.failed = [name for name in CHECKS if not checks[name]]
    return reading


# --- Country codes ---------------------------------------------------------------------

# ISO 3166-1 alpha-3 → alpha-2, as in the countries reference.
_ISO = """
AFG AF ALA AX ALB AL DZA DZ ASM AS AND AD AGO AO AIA AI ATA AQ ATG AG ARG AR ARM AM ABW AW
AUS AU AUT AT AZE AZ BHS BS BHR BH BGD BD BRB BB BLR BY BEL BE BLZ BZ BEN BJ BMU BM BTN BT
BOL BO BES BQ BIH BA BWA BW BVT BV BRA BR IOT IO BRN BN BGR BG BFA BF BDI BI CPV CV KHM KH
CMR CM CAN CA CYM KY CAF CF TCD TD CHL CL CHN CN CXR CX CCK CC COL CO COM KM COG CG COD CD
COK CK CRI CR CIV CI HRV HR CUB CU CUW CW CYP CY CZE CZ DNK DK DJI DJ DMA DM DOM DO ECU EC
EGY EG SLV SV GNQ GQ ERI ER EST EE SWZ SZ ETH ET FLK FK FRO FO FJI FJ FIN FI FRA FR GUF GF
PYF PF ATF TF GAB GA GMB GM GEO GE DEU DE GHA GH GIB GI GRC GR GRL GL GRD GD GLP GP GUM GU
GTM GT GGY GG GIN GN GNB GW GUY GY HTI HT HMD HM VAT VA HND HN HKG HK HUN HU ISL IS IND IN
IDN ID IRN IR IRQ IQ IRL IE IMN IM ISR IL ITA IT JAM JM JPN JP JEY JE JOR JO KAZ KZ KEN KE
KIR KI PRK KP KOR KR KWT KW KGZ KG LAO LA LVA LV LBN LB LSO LS LBR LR LBY LY LIE LI LTU LT
LUX LU MAC MO MDG MG MWI MW MYS MY MDV MV MLI ML MLT MT MHL MH MTQ MQ MRT MR MUS MU MYT YT
MEX MX FSM FM MDA MD MCO MC MNG MN MNE ME MSR MS MAR MA MOZ MZ MMR MM NAM NA NRU NR NPL NP
NLD NL NCL NC NZL NZ NIC NI NER NE NGA NG NIU NU NFK NF MKD MK MNP MP NOR NO OMN OM PAK PK
PLW PW PSE PS PAN PA PNG PG PRY PY PER PE PHL PH PCN PN POL PL PRT PT PRI PR QAT QA REU RE
ROU RO RUS RU RWA RW BLM BL SHN SH KNA KN LCA LC MAF MF SPM PM VCT VC WSM WS SMR SM STP ST
SAU SA SEN SN SRB RS SYC SC SLE SL SGP SG SXM SX SVK SK SVN SI SLB SB SOM SO ZAF ZA SGS GS
SSD SS ESP ES LKA LK SDN SD SUR SR SJM SJ SWE SE CHE CH SYR SY TWN TW TJK TJ TZA TZ THA TH
TLS TL TGO TG TKL TK TON TO TTO TT TUN TN TUR TR TKM TM TCA TC TUV TV UGA UG UKR UA ARE AE
GBR GB USA US UMI UM URY UY UZB UZ VUT VU VEN VE VNM VN VGB VG VIR VI WLF WF ESH EH YEM YE
ZMB ZM ZWE ZW
"""
_PAIRS = _ISO.split()
ALPHA2 = dict(zip(_PAIRS[0::2], _PAIRS[1::2], strict=True))
# Codes ICAO uses besides ISO: Germany is D, British nationals have their own codes,
# Kosovo is RKS. Stateless persons, refugees, UN documents and the specimen: no country.
ALPHA2.update({"D": "DE", "GBD": "GB", "GBN": "GB", "GBO": "GB", "GBP": "GB", "GBS": "GB"})
ALPHA2["RKS"] = "XK"


def country_alpha2(code: str) -> str | None:
    return ALPHA2.get(re.sub(f"{FILLER}+$", "", code))
