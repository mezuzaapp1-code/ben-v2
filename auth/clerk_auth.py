import os
from fastapi import HTTPException, status
from clerk_backend_api.security import VerifyTokenOptions, verify_token as _clerk_verify_token
from clerk_backend_api.security.types import TokenVerificationError


def verify_token(token: str) -> dict:
    sk = os.getenv("CLERK_SECRET_KEY")
    if not sk:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "CLERK_SECRET_KEY missing")
    try:
        p = _clerk_verify_token(token, VerifyTokenOptions(secret_key=sk))
    except TokenVerificationError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    o = p.get("o")
    org_id = p.get("org_id") or (o.get("id") if isinstance(o, dict) else None)
    return {"user_id": p.get("sub"), "email": p.get("email"), "org_id": org_id}
