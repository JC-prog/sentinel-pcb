"""Create (or promote) an Admin user - the public /api/auth/register endpoint deliberately
refuses to hand out the Admin role (app/auth/schemas.py), except automatically to the very first
user ever registered. This script is the intended way to create any Admin after that point,
without building a whole admin-management UI/endpoint for it.

If the given email already exists, it's promoted to Admin in place (rather than erroring) - this
doubles as the "make an existing user an Admin" tool.

Usage (run as a module, not a script path - it needs the repo root on sys.path to import `app`):
    uv run python -m scripts.create_admin_user \\
        --name "Jane Doe" --email jane@example.com --employee-id EMP-042 \\
        --department-shift "QA Day Shift"

Prompts for the password (not passed as a flag, so it never ends up in shell history).
"""

import argparse
import asyncio
import getpass

from app.auth import repository
from app.auth.security import hash_password
from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import async_session_factory, engine


async def create_or_promote_admin(
    name: str, email: str, password: str, employee_id: str, department_shift: str
) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        existing = await repository.get_user_by_email(session, email)
        if existing is not None:
            existing.role = UserRole.ADMIN
            await repository.save_user(session, existing)
            print(f"Promoted existing user {email!r} to Admin.")
            return

        user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            employee_id=employee_id,
            department_shift=department_shift,
            role=UserRole.ADMIN,
        )
        await repository.add_user(session, user)
        print(f"Created Admin user {email!r}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--department-shift", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords did not match.")

    asyncio.run(
        create_or_promote_admin(
            name=args.name,
            email=args.email,
            password=password,
            employee_id=args.employee_id,
            department_shift=args.department_shift,
        )
    )


if __name__ == "__main__":
    main()
