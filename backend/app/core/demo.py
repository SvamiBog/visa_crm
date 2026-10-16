"""Demo data for the staging stand (docs/staging.md): specialists with a few hundred
fictional clients each — consultations, cases at every stage, queues, applications,
vault accounts and bookings — so the system can be tried, and attacked, without real data.

Everything is made up: names from lists, phones from random digits, emails on example.com.
Refuses to run in production. Passwords and the shared authenticator secret come from the
DEMO_PASSWORD and DEMO_TOTP_SECRET variables of the stand, never from the code.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dictionaries import add_default_dictionaries
from app.core.modules import DEFAULT_MODULES
from app.core.passwords import PasswordRules, generate_password
from app.core.security import hash_password
from app.core.two_factor import seal_secret
from app.core.vault import encrypt_for_tenant, get_tenant_key
from app.models import (
    ApplicationAttempt,
    AttemptOutcome,
    Booking,
    BookingKind,
    BookingStatus,
    BookingType,
    CaseStage,
    Consultation,
    Contact,
    ContactNote,
    ContactRelation,
    Country,
    CountryRule,
    Decision,
    EmailOwner,
    LeadChannel,
    Messenger,
    Partner,
    PartnerTerms,
    PartnerTermsType,
    Registrar,
    RegistrarKind,
    RegistrarPrice,
    SubmissionGroup,
    Tenant,
    User,
    UserRole,
    VaultEntry,
    VaultSystem,
    VaultSystemKind,
    VisaCase,
    VisaCenter,
    VisaEntries,
    VisaOutcome,
    VisaPurpose,
    WaitingListEntry,
)
from app.models.dictionaries import Hall

DOMAIN = "demo.visacrm.test"
ADMIN_EMAIL = f"admin@{DOMAIN}"
ALLOWED_ENVS = ("local", "staging", "test")

# Where the demo clients go, most often first.
DESTINATIONS = ("PL", "PL", "PL", "FR", "IT", "LV", "LT", "ES", "DE", "GR", "HU")
CITIES = {"PL": ("Минск", "Гродно", "Брест"), "IT": ("Минск",), "LV": ("Минск",)}

MALE = (
    "Александр",
    "Дмитрий",
    "Сергей",
    "Андрей",
    "Алексей",
    "Иван",
    "Максим",
    "Павел",
    "Николай",
    "Владимир",
    "Егор",
    "Артём",
    "Кирилл",
    "Михаил",
    "Денис",
    "Виктор",
)
FEMALE = (
    "Анна",
    "Мария",
    "Екатерина",
    "Ольга",
    "Елена",
    "Наталья",
    "Татьяна",
    "Ирина",
    "Юлия",
    "Светлана",
    "Дарья",
    "Алина",
    "Виктория",
    "Полина",
    "Ксения",
    "Вероника",
)
SURNAMES = (
    "Ковалёв",
    "Новиков",
    "Морозов",
    "Волков",
    "Лебедев",
    "Козлов",
    "Соколов",
    "Попов",
    "Кузнецов",
    "Павлов",
    "Семёнов",
    "Егоров",
    "Орлов",
    "Макаров",
    "Захаров",
    "Зайцев",
    "Борисов",
    "Яковлев",
    "Григорьев",
    "Романов",
    "Комаров",
    "Белов",
    "Тарасов",
    "Жуков",
    "Ершов",
    "Гусев",
    "Титов",
    "Карпов",
    "Фролов",
)
SPECIALIST_NAMES = (
    "Визы Анны",
    "Шенген-Гродно",
    "Визовый центр Ольги",
    "Виза Плюс Брест",
    "Визы Максима",
)
TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "i",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "kh",
            "ts",
            "ch",
            "sh",
            "shch",
            "",
            "y",
            "",
            "e",
            "iu",
            "ia",
        ],
        strict=True,
    )
)
NOTES = (
    "Прислала сканы паспорта в Telegram.",
    "Ждёт справку с работы.",
    "Просит подачу в Гродно.",
    "Перезвонить после отпуска.",
    "Уточнить даты поездки с мужем.",
    "Оплатил консультацию наличными.",
)


# How many days back the first contact of a client whose case is at this stage can be.
FIRST_SEEN_DAYS = {
    CaseStage.WAITING: (10, 250),
    CaseStage.DOCUMENTS: (90, 300),
    CaseStage.SUBMISSION: (90, 300),
    CaseStage.REVIEW: (100, 320),
    CaseStage.RESULT: (150, 600),
}


class DemoRefused(RuntimeError):
    """The environment or the variables do not allow demo data."""


@dataclass(frozen=True)
class DemoSettings:
    password: str
    totp_secret: str
    specialists: int = 5
    clients: int = 300


def check_settings(env: str, password: str, totp_secret: str) -> None:
    if env not in ALLOWED_ENVS:
        raise DemoRefused(f"demo data is only for {', '.join(ALLOWED_ENVS)}, not {env!r}")
    if len(password) < 10:
        raise DemoRefused("DEMO_PASSWORD must be at least 10 characters")
    try:
        pyotp.TOTP(totp_secret).now()
    except Exception as error:  # noqa: BLE001 — any malformed secret
        raise DemoRefused("DEMO_TOTP_SECRET must be a base32 authenticator secret") from error


def latin(name: str) -> str:
    return " ".join(
        "".join(TRANSLIT.get(c, c) for c in part.lower()).upper() for part in name.split()
    )


def female(surname: str) -> str:
    return surname + "а"


def specialist_email(index: int) -> str:
    return f"visa{index}@{DOMAIN}"


def _user(
    email: str,
    role: UserRole,
    settings: DemoSettings,
    tenant_id: uuid.UUID | None,
    now: datetime,
    modules: list[str] | None = None,
) -> User:
    user_id = uuid.uuid4()
    two_factor = role != UserRole.EMPLOYEE
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(settings.password),
        role=role,
        must_change_password=False,
        totp_secret_encrypted=seal_secret(user_id, settings.totp_secret) if two_factor else None,
        totp_enabled_at=now if two_factor else None,
        modules=modules or [],
    )


async def already_seeded(session: AsyncSession) -> bool:
    return await session.scalar(select(User.id).where(User.email == ADMIN_EMAIL)) is not None


async def _ensure_visa_centers(session: AsyncSession, rules: dict[str, CountryRule]) -> None:
    """Demo cases name a city of submission; on a fresh stand the reference has none."""
    for code, cities in CITIES.items():
        rule = rules.get(code)
        if rule is None:
            continue
        have = set(
            await session.scalars(select(VisaCenter.city_ru).where(VisaCenter.rule_id == rule.id))
        )
        for city in cities:
            if city not in have:
                session.add(VisaCenter(rule_id=rule.id, city_ru=city, city_en=latin(city).title()))
    await session.flush()


async def seed(session: AsyncSession, settings: DemoSettings) -> dict[str, int]:
    """Create the demo admin and specialists. The session must be in the system scope."""
    now = datetime.now(UTC)
    countries = {c.code: c for c in await session.scalars(select(Country))}
    code_of = {c.id: c.code for c in countries.values()}
    # The demo clients apply from Belarus.
    rules = {
        code_of[rule.destination_id]: rule
        for rule in await session.scalars(
            select(CountryRule).where(CountryRule.submission_id == countries["BY"].id)
        )
    }
    await _ensure_visa_centers(session, rules)
    centers: dict[str, list[VisaCenter]] = {}
    for code, rule in rules.items():
        centers[code] = list(
            await session.scalars(
                select(VisaCenter).where(VisaCenter.rule_id == rule.id, VisaCenter.is_active)
            )
        )

    session.add(_user(ADMIN_EMAIL, UserRole.ADMIN, settings, None, now))
    totals = {"specialists": 0, "clients": 0, "cases": 0}
    for index in range(1, settings.specialists + 1):
        rng = random.Random(index)
        clients = int(settings.clients * rng.uniform(0.8, 1.25))
        counts = await _workspace(
            session, settings, index, clients, rng, countries, rules, centers, now
        )
        totals["specialists"] += 1
        totals["clients"] += counts["clients"]
        totals["cases"] += counts["cases"]
    await session.flush()
    return totals


async def _workspace(
    session: AsyncSession,
    settings: DemoSettings,
    index: int,
    clients: int,
    rng: random.Random,
    countries: dict[str, Country],
    rules: dict[str, CountryRule],
    centers: dict[str, list[VisaCenter]],
    now: datetime,
) -> dict[str, int]:
    today = now.date()
    name = SPECIALIST_NAMES[(index - 1) % len(SPECIALIST_NAMES)]
    tenant = Tenant(id=uuid.uuid4(), name=f"{name} (демо {index})", modules=list(DEFAULT_MODULES))
    session.add(tenant)
    await session.flush()
    owner = _user(specialist_email(index), UserRole.SPECIALIST, settings, tenant.id, now)
    staff = _user(
        f"visa{index}.staff@{DOMAIN}",
        UserRole.EMPLOYEE,
        settings,
        tenant.id,
        now,
        modules=["clients", "cases"],
    )
    session.add_all([owner, staff])
    add_default_dictionaries(session, tenant.id)
    await session.flush()
    t = tenant.id

    purposes = list(await session.scalars(select(VisaPurpose).where(VisaPurpose.tenant_id == t)))
    channels = list(await session.scalars(select(LeadChannel).where(LeadChannel.tenant_id == t)))
    systems = {
        s.kind: s
        for s in await session.scalars(select(VaultSystem).where(VaultSystem.tenant_id == t))
    }
    registrars = [
        Registrar(tenant_id=t, name=n, kind=k)
        for n, k in (
            ("Иван", RegistrarKind.INDIVIDUAL),
            ("ВизаСервис", RegistrarKind.COMPANY),
            ("Ольга", RegistrarKind.INDIVIDUAL),
        )
    ]
    partners = [Partner(tenant_id=t, name=n) for n in ("Легализатор Пётр", "Коллега Марина")]
    session.add_all(registrars + partners)
    await session.flush()
    for registrar in registrars:
        for code in ("PL", "IT", "LV"):
            session.add(
                RegistrarPrice(
                    tenant_id=t,
                    registrar_id=registrar.id,
                    country_id=countries[code].id,
                    hall=Hall.STANDARD,
                    amount=Decimal(rng.choice((40, 50, 60, 80))),
                    currency="EUR",
                )
            )
    session.add_all(
        [
            PartnerTerms(
                tenant_id=t,
                partner_id=partners[0].id,
                terms_type=PartnerTermsType.PERCENT,
                percent=Decimal("10"),
            ),
            PartnerTerms(
                tenant_id=t,
                partner_id=partners[1].id,
                terms_type=PartnerTermsType.FIXED,
                amount=Decimal("50"),
                currency="BYN",
            ),
        ]
    )

    key = await get_tenant_key(session, t)

    def vault(
        system_kind: VaultSystemKind,
        login: str,
        contact_id: uuid.UUID | None = None,
        case_id: uuid.UUID | None = None,
        owner: EmailOwner | None = None,
    ) -> VaultEntry:
        system = systems[system_kind]
        entry = VaultEntry(
            id=uuid.uuid4(),
            tenant_id=t,
            system_id=system.id,
            contact_id=contact_id,
            case_id=case_id,
            login=login,
            email_owner=owner,
            created_manually=rng.random() < 0.5,
            password_updated_at=now,
        )
        password = generate_password(
            PasswordRules(
                length=system.password_length,
                lower=system.password_lower,
                upper=system.password_upper,
                digits=system.password_digits,
                symbols=system.password_symbols,
            )
        )
        entry.password_encrypted = encrypt_for_tenant(key, t, entry.id, password)
        session.add(entry)
        return entry

    backups = [vault(VaultSystemKind.EMAIL, f"backup{n}.visa{index}@example.com") for n in (1, 2)]
    booking_account = vault(VaultSystemKind.BOOKING, f"booking.visa{index}@example.com")

    phones: set[str] = set()

    def phone() -> str:
        while True:
            number = "+375" + rng.choice(("29", "33", "44", "25")) + f"{rng.randrange(10**7):07d}"
            if number not in phones:
                phones.add(number)
                return number

    def person(surname: str | None = None, is_female: bool | None = None) -> tuple[str, bool]:
        is_female = rng.random() < 0.6 if is_female is None else is_female
        base = surname or rng.choice(SURNAMES)
        first = rng.choice(FEMALE if is_female else MALE)
        return f"{female(base) if is_female else base} {first}", is_female

    def contact(full_name: str, first_seen: date, channel: LeadChannel) -> Contact:
        n = len(phones)
        c = Contact(
            id=uuid.uuid4(),
            tenant_id=t,
            full_name=full_name,
            full_name_latin=latin(full_name) if rng.random() < 0.6 else None,
            phone=phone(),
            telegram=f"demo_{index}_{n}" if rng.random() < 0.7 else None,
            messenger=None,
            email=f"client{index}.{n}@example.com" if rng.random() < 0.5 else None,
            birth_date=date(rng.randrange(1960, 2015), rng.randrange(1, 13), rng.randrange(1, 29)),
            lead_channel_id=channel.id,
            partner_id=rng.choice(partners).id if rng.random() < 0.15 else None,
            consent_signed_on=first_seen if rng.random() < 0.7 else None,
            created_by=owner.id,
            created_at=datetime.combine(first_seen, datetime.min.time(), UTC),
        )
        if rng.random() < 0.1:
            c.messenger, c.messenger_contact = Messenger.VIBER, c.phone
        session.add(c)
        return c

    number = 0
    made = {"clients": 0, "cases": 0}
    while made["clients"] < clients:
        decision = rng.choices(
            (Decision.YES, Decision.THINKING, Decision.NO, None), weights=(55, 15, 10, 20)
        )[0]
        stage = rng.choices(list(CaseStage), weights=(30, 20, 8, 15, 27))[0]
        # The first contact is far enough back for the case to have got to its stage.
        low, high = FIRST_SEEN_DAYS[stage] if decision == Decision.YES else (0, 600)
        first_seen = today - timedelta(days=rng.randrange(low, high))
        channel = rng.choice(channels)
        surname = rng.choice(SURNAMES)
        full_name, is_female = person(surname)
        anchor = contact(full_name, first_seen, channel)
        made["clients"] += 1
        # Rows without ORM relationships are inserted in the order they are flushed.
        await session.flush()
        code = rng.choice(DESTINATIONS)
        consultation = Consultation(
            id=uuid.uuid4(),
            tenant_id=t,
            contact_id=anchor.id,
            held_on=first_seen,
            decision=decision,
            lead_channel_id=channel.id,
            destination_ids=[countries[code].id],
            trip_planned_on=first_seen + timedelta(days=rng.randrange(30, 200)),
            created_by=owner.id,
        )
        if rng.random() < 0.6:
            consultation.fee_amount, consultation.fee_currency = Decimal("90"), "BYN"
            consultation.fee_paid = rng.random() < 0.8
        session.add(consultation)
        if rng.random() < 0.3:
            session.add(
                ContactNote(
                    tenant_id=t, contact_id=anchor.id, body=rng.choice(NOTES), author_id=owner.id
                )
            )
        if decision != Decision.YES:
            continue

        # A family applies together now and then.
        family = [anchor]
        if rng.random() < 0.15 and made["clients"] < clients:
            for label, reverse in rng.sample(
                (("жена", "муж"), ("сын", "мать"), ("дочь", "мать")), rng.randrange(1, 3)
            ):
                relative_name, _ = person(surname, is_female=label in ("жена", "дочь"))
                relative = contact(relative_name, first_seen, channel)
                made["clients"] += 1
                await session.flush()
                session.add_all(
                    [
                        ContactRelation(
                            tenant_id=t, contact_id=anchor.id, related_id=relative.id, label=label
                        ),
                        ContactRelation(
                            tenant_id=t, contact_id=relative.id, related_id=anchor.id, label=reverse
                        ),
                    ]
                )
                family.append(relative)
        await session.flush()

        rule = rules.get(code)
        submission_on = _submission(stage, today, first_seen, rng)
        center = rng.choice(centers[code]) if centers.get(code) else None
        group = None
        if len(family) > 1:
            group = SubmissionGroup(id=uuid.uuid4(), tenant_id=t)
            session.add(group)
            await session.flush()
        outcome = _outcome(stage, rng)
        purpose = rng.choice(purposes)
        registrar = (
            rng.choice(registrars)
            if rule and rule.booking_type == BookingType.WAITING_LIST
            else None
        )
        for member in family:
            number += 1
            case = VisaCase(
                id=uuid.uuid4(),
                tenant_id=t,
                number=number,
                contact_id=member.id,
                group_id=group.id if group else None,
                consultation_id=consultation.id,
                destination_id=countries[code].id,
                purpose_id=purpose.id,
                submission_country_id=countries["BY"].id,
                visa_center_id=center.id if center else None,
                registrar_id=registrar.id if registrar else None,
                hall=Hall.STANDARD if registrar else None,
                stage=stage,
                submission_on=submission_on,
                outcome=outcome,
                decided_on=(submission_on or today) + timedelta(days=rng.randrange(5, 20))
                if outcome in (VisaOutcome.VISA, VisaOutcome.REFUSAL)
                else None,
                vac_number=f"{code}-{rng.randrange(10000, 99999)}" if rng.random() < 0.7 else None,
                created_by=owner.id,
            )
            if case.decided_on and case.decided_on > today:
                case.decided_on = today
            if outcome == VisaOutcome.VISA:
                case.visa_entries = rng.choice(list(VisaEntries))
                case.visa_valid_from = case.decided_on
                case.visa_valid_to = (case.decided_on or today) + timedelta(
                    days=rng.choice((90, 180, 365))
                )
                case.stay_days = rng.choice((15, 30, 90))
            session.add(case)
            made["cases"] += 1
            await session.flush()
            _appointment(session, case, rule, today, first_seen, rng)
            if stage != CaseStage.WAITING and rng.random() < 0.6:
                owner_kind = rng.choice(list(EmailOwner))
                login = (
                    rng.choice(backups).login
                    if owner_kind == EmailOwner.SPECIALIST_BACKUP
                    else member.email or f"vac.{case.id.hex[:8]}@example.com"
                )
                vault(VaultSystemKind.VISA_CENTER, login, member.id, case.id, owner_kind)
            if member is anchor:
                _bookings(session, case, booking_account, today, rng)
    await session.flush()
    return made


def _submission(stage: CaseStage, today: date, since: date, rng: random.Random) -> date | None:
    match stage:
        case CaseStage.WAITING:
            return None
        case CaseStage.DOCUMENTS:
            return today + timedelta(days=rng.randrange(7, 60))
        case CaseStage.SUBMISSION:
            return today + timedelta(days=rng.randrange(0, 4))
        case CaseStage.REVIEW:
            return today - timedelta(days=rng.randrange(1, 20))
        case _:
            return today - timedelta(days=rng.randrange(20, (today - since).days - 60))


def _outcome(stage: CaseStage, rng: random.Random) -> VisaOutcome | None:
    if stage != CaseStage.RESULT:
        return None
    return rng.choices(list(VisaOutcome), weights=(80, 15, 5))[0]


def _appointment(
    session: AsyncSession,
    case: VisaCase,
    rule: CountryRule | None,
    today: date,
    since: date,
    rng: random.Random,
) -> None:
    """Queued or applied after the consultation, the letter before the submission."""
    if rule is None:
        return
    if rule.booking_type == BookingType.WAITING_LIST:
        queued = min(today, since + timedelta(days=rng.randrange(1, 15)))
        letter = None
        if case.submission_on is not None:
            letter = min(today, case.submission_on - timedelta(days=rng.randrange(7, 30)))
            letter = max(letter, queued)
        session.add(
            WaitingListEntry(
                tenant_id=case.tenant_id, case_id=case.id, queued_on=queued, letter_on=letter
            )
        )
    elif rule.booking_type == BookingType.WINDOWED_APPLICATION and rule.window_days:
        window = timedelta(days=rule.window_days)
        sent = max(since + timedelta(days=1), today - timedelta(days=rng.randrange(5, 50)))
        expired = rng.random() < 0.4 and sent - window - timedelta(days=20) >= since
        if expired:
            first = sent - window - timedelta(days=rng.randrange(1, 20))
            session.add(
                ApplicationAttempt(
                    tenant_id=case.tenant_id,
                    case_id=case.id,
                    number=1,
                    sent_on=first,
                    deadline_on=first + window,
                    outcome=AttemptOutcome.EXPIRED,
                    closed_on=first + window,
                )
            )
        dated = case.submission_on is not None
        session.add(
            ApplicationAttempt(
                tenant_id=case.tenant_id,
                case_id=case.id,
                number=2 if expired else 1,
                sent_on=sent,
                deadline_on=sent + window,
                outcome=AttemptOutcome.DATE_RECEIVED if dated else None,
                closed_on=min(today, sent + timedelta(days=rng.randrange(3, 40)))
                if dated
                else None,
            )
        )


def _bookings(
    session: AsyncSession, case: VisaCase, account: VaultEntry, today: date, rng: random.Random
) -> None:
    if case.stage not in (
        CaseStage.DOCUMENTS,
        CaseStage.SUBMISSION,
        CaseStage.REVIEW,
        CaseStage.RESULT,
    ):
        return
    trip = (case.submission_on or today) + timedelta(days=rng.randrange(20, 60))
    for n in range(rng.randrange(1, 3)):
        status = BookingStatus.ACTIVE
        if case.stage == CaseStage.RESULT:
            status = BookingStatus.CANCELLED if rng.random() < 0.85 else BookingStatus.ACTIVE
        start = trip + timedelta(days=4 * n)
        session.add(
            Booking(
                tenant_id=case.tenant_id,
                case_id=case.id,
                kind=BookingKind.HOTEL,
                status=status,
                place=rng.choice(
                    (
                        "Отель, Краков",
                        "Отель, Варшава",
                        "Отель, Рим",
                        "Отель, Рига",
                        "Отель, Париж",
                        "Апартаменты, Вильнюс",
                    )
                ),
                starts_on=start,
                ends_on=start + timedelta(days=rng.randrange(2, 6)),
                reference=str(rng.randrange(1000000, 9999999)),
                free_cancel_until=today + timedelta(days=rng.randrange(-3, 25)),
                vault_entry_id=account.id,
            )
        )
    if rng.random() < 0.3:
        session.add(
            Booking(
                tenant_id=case.tenant_id,
                case_id=case.id,
                kind=BookingKind.FLIGHT,
                status=BookingStatus.ACTIVE,
                place="Минск — Варшава",
                starts_on=trip,
                ends_on=trip + timedelta(days=10),
                valid_until=today + timedelta(days=rng.randrange(5, 40)),
            )
        )
