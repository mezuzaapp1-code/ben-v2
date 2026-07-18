"""Minimal BEN acquisition seam (N3.0). RSS adapter lives under services.news."""

from services.acquisition.types import (
    AcquisitionContext,
    AcquisitionError,
    CollectResult,
    FetchResult,
    NormalizedItem,
    PersistResult,
    new_acquisition_id,
)

__all__ = [
    "AcquisitionContext",
    "AcquisitionError",
    "CollectResult",
    "FetchResult",
    "NormalizedItem",
    "PersistResult",
    "new_acquisition_id",
]
