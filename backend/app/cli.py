"""Server commands, run as `uv run python -m app.cli <command>`.

create-admin EMAIL     first platform admin (asks for the password)
generate-master-key    a new VAULT_MASTER_KEY value
rotate-master-key      re-encrypt secrets after setting a new VAULT_MASTER_KEY
                       (see docs/operations.md)
rekey-after-leak       after a leak of VAULT_MASTER_KEY: new data keys for every
                       workspace, vault and files encrypted again, 2FA off for everyone
                       (see docs/operations.md)
upgrade-recovery-codes keep the recovery codes made before backlog S16 as HMAC too
reset-two-factor EMAIL turn off 2FA for a user who lost the phone and recovery codes
seed-demo              fill the staging stand with demo specialists and clients
                       (docs/staging.md; refuses in production)
wait-db                wait until the database answers (start.sh, before the migrations)
check-error-reports    send one test report to Sentry from this server
                       (docs/operations.md)
"""

import argparse
import asyncio
import getpass
import sys
import uuid

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import DBAPIError

from app.core.audit import record
from app.core.config import get_settings
from app.core.crypto import generate_master_key, master_keys
from app.core.db import get_engine, get_sessionmaker, scope_to_system, wait_for_database
from app.core.demo import DemoRefused, DemoSettings, already_seeded, check_settings, seed
from app.core.error_reports import send_test_report
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.core.storage import StorageFailed
from app.core.two_factor import reset_two_factor, upgrade_legacy_recovery_codes
from app.core.vault import RekeyFailed, rekey_workspace, reseal_with_current_master_key
from app.models import AuditAction, RecoveryCode, Tenant, User, UserRole, UserSession


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


async def _reset_everyones_two_factor() -> int:
    """Two-factor login off for every user, every session ended (logins waiting for their
    code too, audit h02.8). The leaked master key opened every authenticator secret."""
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        users = await session.scalars(
            select(User)
            .where(or_(User.totp_secret_encrypted.is_not(None), User.totp_enabled_at.is_not(None)))
            .with_for_update()
        )
        count = 0
        for user in users:
            if user.totp_enabled_at is not None:
                record(
                    session,
                    AuditAction.TWO_FACTOR_RESET,
                    actor=None,
                    tenant_id=user.tenant_id,
                    target_type="user",
                    target_id=user.id,
                    details={"via": "cli", "reason": "rekey"},
                )
                count += 1
            reset_two_factor(user)
        await session.execute(delete(RecoveryCode))
        await session.execute(delete(UserSession))
        await session.commit()
    return count


async def rekey_after_leak() -> str:
    """The response to a leaked VAULT_MASTER_KEY (docs/operations.md): every workspace gets
    a new data key, its vault passwords and files are encrypted again, and two-factor login
    is turned off for everyone. Each workspace is its own transaction: a run stopped by an
    error is run again and goes on."""
    try:
        users = await _reset_everyones_two_factor()
        async with get_sessionmaker()() as session:
            scope_to_system(session)
            tenant_ids = list(
                await session.scalars(
                    select(Tenant.id).where(Tenant.vault_key_encrypted.is_not(None))
                )
            )
        entries = files = 0
        for tenant_id in tenant_ids:
            async with get_sessionmaker()() as session:
                scope_to_system(session)
                done = await rekey_workspace(session, tenant_id)
                await session.commit()
            entries += done.vault_entries
            files += done.files
    finally:
        await get_engine().dispose()
    return (
        f"New keys for {len(tenant_ids)} workspaces: {entries} vault passwords and "
        f"{files} files encrypted again; two-factor login turned off for {users} users, "
        "everyone signed out"
    )


async def upgrade_recovery_codes() -> int:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        count = await upgrade_legacy_recovery_codes(session)
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
    rekey = commands.add_parser(
        "rekey-after-leak", help="new data keys for every workspace after a key leak"
    )
    rekey.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    commands.add_parser(
        "upgrade-recovery-codes", help="keep recovery codes made before S16 as HMAC"
    )
    reset = commands.add_parser("reset-two-factor", help="turn off 2FA for a user")
    reset.add_argument("email")
    demo = commands.add_parser("seed-demo", help="fill the staging stand with demo data")
    demo.add_argument("--specialists", type=int, default=5)
    demo.add_argument("--clients", type=int, default=300, help="about this many per specialist")
    wait = commands.add_parser("wait-db", help="wait until the database answers")
    # Within Railway's five-minute healthcheck window, with time left for the migrations.
    wait.add_argument("--timeout", type=float, default=180, help="seconds")
    commands.add_parser("check-error-reports", help="send one test report to Sentry")
    args = parser.parse_args()

    if args.command == "generate-master-key":
        print(generate_master_key())
    elif args.command == "rotate-master-key":
        if len(master_keys()) < 2:
            sys.exit("Set VAULT_MASTER_KEY_PREVIOUS to the old key before rotating")
        print(f"Re-encrypted {asyncio.run(rotate_master_key())} secrets")
    elif args.command == "rekey-after-leak":
        keys = master_keys()
        if len(keys) < 2 or keys[0] == keys[1]:
            sys.exit(
                "Set VAULT_MASTER_KEY to a new key and VAULT_MASTER_KEY_PREVIOUS to the "
                "leaked one first"
            )
        if not args.yes:
            print(
                "Every workspace gets a new key; two-factor login is turned off for every "
                "user and everyone is signed out. Each workspace stops while its files are "
                "encrypted again."
            )
            if input("Type REKEY to go on: ") != "REKEY":
                sys.exit("Nothing changed")
        try:
            print(asyncio.run(rekey_after_leak()))
        except RekeyFailed as error:
            sys.exit(f"Stopped: a file opens with neither key ({error}); see docs/operations.md")
        except StorageFailed as error:
            # The workspace in progress is left as it was, the next run goes on.
            sys.exit(f"Stopped by the storage ({error}); run the command again to go on")
        except DBAPIError as error:
            # A lock wait that ran out: a workspace busy, e.g. with an import. Only the
            # error's name: its text quotes the statement.
            sys.exit(f"Stopped by {type(error).__name__}; run the command again to go on")
    elif args.command == "upgrade-recovery-codes":
        print(f"Kept {asyncio.run(upgrade_recovery_codes())} recovery codes as HMAC")
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
    elif args.command == "check-error-reports":
        settings = get_settings()
        event_id = send_test_report(settings)
        if event_id is None:
            sys.exit("SENTRY_DSN is not set: error reports are off")
        print(f"Sent test report {event_id} ({settings.env}): ErrorReportCheck in Sentry Issues")
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
