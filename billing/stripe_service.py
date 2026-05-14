"""Stripe Checkout for BEN Pro ($15/mo). Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID_PRO in .env."""
import os

import stripe

SUCCESS = "http://localhost:5173/success"
CANCEL = "http://localhost:5173/cancel"


def create_checkout_session(user_id: str, tier: str) -> str:
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    price_id = os.environ["STRIPE_PRICE_ID_PRO"]
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=SUCCESS,
        cancel_url=CANCEL,
        client_reference_id=user_id[:500] if user_id else None,
        subscription_data={"metadata": {"user_id": user_id or "", "tier": tier or "pro"}},
        metadata={"user_id": user_id or "", "tier": tier or "pro"},
    )
    return session.url or ""
