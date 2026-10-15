"""Admin commands. First platform admin: uv run python -m app.cli create-admin you@example.com"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker, scope_to_system
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.models import User, UserRole


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create a platform admin account")
    create.add_argument("email")
    args = parser.parse_args()

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
