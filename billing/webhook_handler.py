"""Verify Stripe webhooks; handle checkout.session.completed."""
import os

import stripe


def handle_webhook(payload: bytes, sig_header: str | None) -> dict:
    secret = os.environ["STRIPE_WEBHOOK_SECRET"]
    try:
        event = stripe.Webhook.construct_event(payload, sig_header or "", secret)
    except ValueError:
        return {"ok": False, "error": "invalid_payload"}
    except stripe.error.SignatureVerificationError:
        return {"ok": False, "error": "invalid_signature"}
    et = event.get("type", "")
    if et == "checkout.session.completed":
        obj = event.get("data", {}).get("object") or {}
        return {
            "ok": True,
            "type": et,
            "session_id": obj.get("id"),
            "customer": obj.get("customer"),
            "subscription": obj.get("subscription"),
            "metadata": obj.get("metadata") or {},
        }
    return {"ok": True, "type": et, "handled": False}
