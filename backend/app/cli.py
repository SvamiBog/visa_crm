"""Server commands, run as `uv run python -m app.cli <command>`.

create-admin EMAIL     first platform admin (asks for the password)
generate-master-key    a new VAULT_MASTER_KEY value
rotate-master-key      re-encrypt secrets after setting a new VAULT_MASTER_KEY
                       (see docs/operations.md)
reset-two-factor EMAIL turn off 2FA for a user who lost the phone and recovery codes
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import delete, select

from app.core.crypto import generate_master_key, master_keys
from app.core.db import get_engine, get_sessionmaker, scope_to_system
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.core.two_factor import reset_two_factor
from app.core.vault import reseal_with_current_master_key
from app.models import RecoveryCode, User, UserRole, UserSession


async def create_admin(email: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        if await session.scalar(select(User.id).where(User.email == email)):
            sys.exit(f"User {email} already exists")
        session.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
                must_change_password=False,
            )
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
        await session.commit()
    await get_engine().dispose()


async def rotate_master_key() -> int:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        count = await reseal_with_current_master_key(session)
        await session.commit()
    await get_engine().dispose()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create a platform admin account")
    create.add_argument("email")
    commands.add_parser("generate-master-key", help="print a new VAULT_MASTER_KEY value")
    commands.add_parser("rotate-master-key", help="re-encrypt secrets with the current key")
    reset = commands.add_parser("reset-two-factor", help="turn off 2FA for a user")
    reset.add_argument("email")
    args = parser.parse_args()

    if args.command == "generate-master-key":
        print(generate_master_key())
    elif args.command == "rotate-master-key":
        if len(master_keys()) < 2:
            sys.exit("Set VAULT_MASTER_KEY_PREVIOUS to the old key before rotating")
        print(f"Re-encrypted {asyncio.run(rotate_master_key())} secrets")
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
