from __future__ import annotations

import argparse

from license_server.app.database import create_all, get_config, session_scope
from license_server.app.services import LicenseService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Auto TikTok Editor license server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create all database tables.")

    create_user = subparsers.add_parser("create-user", help="Create a new user account.")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--password", required=True)
    create_user.add_argument("--admin", action="store_true")

    issue_license = subparsers.add_parser("issue-license", help="Issue a new license to an existing user.")
    issue_license.add_argument("--username", required=True)
    issue_license.add_argument("--days", required=True, type=int)
    issue_license.add_argument("--plan", default="standard")
    issue_license.add_argument("--max-devices", default=1, type=int)
    issue_license.add_argument("--max-concurrent-sessions", default=1, type=int)
    issue_license.add_argument("--notes", default="")
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        create_all()
        print("Database initialized.")
        return 0

    with session_scope() as session:
        service = LicenseService(session, get_config())
        if args.command == "create-user":
            user = service.create_user(args.username, args.password, is_admin=args.admin)
            print("Created user %s (id=%s)." % (user.username, user.id))
            return 0
        if args.command == "issue-license":
            user = service.get_user_by_username(args.username)
            if user is None:
                parser.error("User not found: %s" % args.username)
            license_record = service.issue_license(
                user,
                plan_name=args.plan,
                days=args.days,
                max_devices=args.max_devices,
                max_concurrent_sessions=args.max_concurrent_sessions,
                notes=args.notes or None,
            )
            print(
                "Issued license %s to %s until %s."
                % (license_record.license_code, user.username, license_record.expires_at.isoformat())
            )
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
