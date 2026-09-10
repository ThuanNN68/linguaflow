#!/usr/bin/env python3
"""Seed development users script.

This script creates test users for development and testing purposes.
Run this once to set up initial users.

Usage:
    python scripts/development/seed_dev_users.py

Environment variables required:
    - DATABASE_URL (defaults to sqlite:///./data/app.db)
    - JWT_SECRET (required for the app to start)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from src.core.security import get_password_hash
from src.database import get_async_session_maker
from src.database.models import User


async def seed_users(
    member_email: str = "member@test.com",
    member_password: str = "testpass123",
    member_language: str = "en",
    admin_email: str = "admin@test.com",
    admin_password: str = "adminpass123",
    admin_language: str = "vi",
    force: bool = False,
) -> None:
    """Create test users if they don't exist.

    The two users get different languages by default, because two accounts that
    read the same language cannot demonstrate a translation at all.

    Args:
        member_email: Email for member user
        member_password: Password for member user
        member_language: ISO 639-1 code the member reads in
        admin_email: Email for admin user
        admin_password: Password for admin user
        admin_language: ISO 639-1 code the admin reads in
        force: If True, update existing users
    """
    # The schema is Alembic's: run `alembic upgrade head` (or `make migrate`)
    # before seeding. Creating tables here would build a schema no migration
    # knows about, and the next `alembic upgrade` would fail on it.
    session_maker = get_async_session_maker()
    async with session_maker() as session:
        users_to_create = [
            {
                "email": member_email,
                "password": member_password,
                "role": "member",
                "language": member_language,
            },
            {
                "email": admin_email,
                "password": admin_password,
                "role": "admin",
                "language": admin_language,
            },
        ]

        for user_data in users_to_create:
            # Check if user exists
            result = await session.execute(
                select(User).where(User.email == user_data["email"])
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                if force:
                    # Update password
                    existing_user.password_hash = get_password_hash(user_data["password"])
                    existing_user.role = user_data["role"]
                    existing_user.preferred_language = user_data["language"]
                    print(f"Updated {user_data['role']}: {user_data['email']}")
                else:
                    print(f"User already exists: {user_data['email']} (use --force to update)")
                continue

            # Create new user
            new_user = User(
                email=user_data["email"],
                password_hash=get_password_hash(user_data["password"]),
                role=user_data["role"],
                preferred_language=user_data["language"],
            )
            session.add(new_user)
            print(f"Created {user_data['role']}: {user_data['email']}")

        await session.commit()

    print("\nTest users seeded successfully!")
    print(f"  Member: {member_email} / {member_password}  (reads {member_language})")
    print(f"  Admin:  {admin_email} / {admin_password}  (reads {admin_language})")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Seed development test users"
    )
    parser.add_argument(
        "--member-email",
        default="member@test.com",
        help="Email for member user (default: member@test.com)",
    )
    parser.add_argument(
        "--member-password",
        default="testpass123",
        help="Password for member user (default: testpass123)",
    )
    parser.add_argument(
        "--member-language",
        default="en",
        help="ISO 639-1 code the member reads in (default: en)",
    )
    parser.add_argument(
        "--admin-email",
        default="admin@test.com",
        help="Email for admin user (default: admin@test.com)",
    )
    parser.add_argument(
        "--admin-password",
        default="adminpass123",
        help="Password for admin user (default: adminpass123)",
    )
    parser.add_argument(
        "--admin-language",
        default="vi",
        help="ISO 639-1 code the admin reads in (default: vi)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force update existing users",
    )

    args = parser.parse_args()

    asyncio.run(
        seed_users(
            member_email=args.member_email,
            member_password=args.member_password,
            member_language=args.member_language,
            admin_email=args.admin_email,
            admin_password=args.admin_password,
            admin_language=args.admin_language,
            force=args.force,
        )
    )


if __name__ == "__main__":
    main()
