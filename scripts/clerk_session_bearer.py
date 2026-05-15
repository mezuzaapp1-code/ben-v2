"""Obtain a short-lived Clerk session JWT for prod shadow testing (never prints token)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_clerk_secret() -> str | None:
    sk = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if sk:
        return sk
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CLERK_SECRET_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def get_bearer() -> str | None:
    sk = _load_clerk_secret()
    if not sk:
        print("clerk_secret=missing", file=sys.stderr)
        return None
    from clerk_backend_api import Clerk
    from clerk_backend_api.models.getuserlistop import GetUserListRequest

    with Clerk(bearer_auth=sk) as clerk:
        users = clerk.users.list(request=GetUserListRequest(limit=1))
        if not users:
            print("clerk_users=none", file=sys.stderr)
            return None
        user_id = users[0].id
        session = clerk.sessions.create(request={"user_id": user_id})
        token = clerk.sessions.create_token(session_id=session.id)
        jwt = getattr(token, "jwt", None) or (token.get("jwt") if isinstance(token, dict) else None)
        if not jwt:
            print("clerk_jwt=missing", file=sys.stderr)
            return None
        return f"Bearer {jwt}"


if __name__ == "__main__":
    bearer = get_bearer()
    if not bearer:
        raise SystemExit(1)
    # Pipe-only: do not log stdout when running this module directly.
    sys.stdout.write(bearer)
    sys.stdout.flush()
