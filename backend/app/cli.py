"""Server commands, run as `uv run python -m app.cli <command>`.

create-admin EMAIL     first platform admin (asks for the password)
generate-master-key    a new VAULT_MASTER_KEY value
rotate-master-key      re-encrypt secrets after setting a new VAULT_MASTER_KEY
                       (see docs/operations.md)
reset-two-factor EMAIL turn off 2FA for a user who lost the phone and recovery codes
seed-demo              fill the staging stand with demo specialists and clients
                       (docs/staging.md; refuses in production)
wait-db                wait until the database answers (start.sh, before the migrations)
"""

import argparse
import asyncio
import getpass
import sys
import uuid

from sqlalchemy import delete, select

from app.core.audit import record
from app.core.config import get_settings
from app.core.crypto import generate_master_key, master_keys
from app.core.db import get_engine, get_sessionmaker, scope_to_system, wait_for_database
from app.core.demo import DemoRefused, DemoSettings, already_seeded, check_settings, seed
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.core.two_factor import reset_two_factor
from app.core.vault import reseal_with_current_master_key
from app.models import AuditAction, RecoveryCode, User, UserRole, UserSession


async def create_admin(email: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        if await session.scalar(select(User.id).where(User.email == email)):
            sys.exit(f"User {email} already exists")
        admin = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            must_change_password=False,
        )
        session.add(admin)
        record(
            session,
            AuditAction.ADMIN_CREATED,
            actor=None,
            target_type="user",
            target_id=admin.id,
            details={"email": email, "via": "cli"},
        )
        await session.commit()
    await get_engine().dispose()


async def reset_user_two_factor(email: str) -> None:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"User {email} not found")
        reset_two_factor(user)
        await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        record(
            session,
            AuditAction.TWO_FACTOR_RESET,
            actor=None,
            tenant_id=user.tenant_id,
            target_type="user",
            target_id=user.id,
            details={"email": email, "via": "cli"},
        )
        await session.commit()
    await get_engine().dispose()


async def rotate_master_key() -> int:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        count = await reseal_with_current_master_key(session)
        await session.commit()
    await get_engine().dispose()
    return count


async def seed_demo(specialists: int, clients: int) -> str:
    settings = get_settings()
    check_settings(settings.env, settings.demo_password, settings.demo_totp_secret)
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        if await already_seeded(session):
            return "Demo data is already there"
        totals = await seed(
            session,
            DemoSettings(
                password=settings.demo_password,
                totp_secret=settings.demo_totp_secret,
                specialists=specialists,
                clients=clients,
            ),
        )
        await session.commit()
    await get_engine().dispose()
    return (
        f"Demo data: {totals['specialists']} specialists, {totals['clients']} clients, "
        f"{totals['cases']} cases"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create a platform admin account")
    create.add_argument("email")
    commands.add_parser("generate-master-key", help="print a new VAULT_MASTER_KEY value")
    commands.add_parser("rotate-master-key", help="re-encrypt secrets with the current key")
    reset = commands.add_parser("reset-two-factor", help="turn off 2FA for a user")
    reset.add_argument("email")
    demo = commands.add_parser("seed-demo", help="fill the staging stand with demo data")
    demo.add_argument("--specialists", type=int, default=5)
    demo.add_argument("--clients", type=int, default=300, help="about this many per specialist")
    wait = commands.add_parser("wait-db", help="wait until the database answers")
    # Within Railway's five-minute healthcheck window, with time left for the migrations.
    wait.add_argument("--timeout", type=float, default=180, help="seconds")
    args = parser.parse_args()

    if args.command == "generate-master-key":
        print(generate_master_key())
    elif args.command == "rotate-master-key":
        if len(master_keys()) < 2:
            sys.exit("Set VAULT_MASTER_KEY_PREVIOUS to the old key before rotating")
        print(f"Re-encrypted {asyncio.run(rotate_master_key())} secrets")
    elif args.command == "seed-demo":
        try:
            print(asyncio.run(seed_demo(args.specialists, args.clients)))
        except DemoRefused as error:
            sys.exit(f"Demo data refused: {error}")
    elif args.command == "wait-db":
        url = get_settings().async_database_url
        if not asyncio.run(wait_for_database(url, timeout=args.timeout)):
            sys.exit(f"The database did not answer in {args.timeout:.0f} s")
        print("The database answers")
    elif args.command == "reset-two-factor":
        email = args.email.strip().lower()
        asyncio.run(reset_user_two_factor(email))
        print(f"Two-factor login turned off for {email}; they set it up again on next login")
    else:
        email = args.email.strip().lower()
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords do not match")
        asyncio.run(create_admin(email, password))
        print(f"Admin {email} created")


if __name__ == "__main__":
    main()
