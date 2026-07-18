"""N3.0 SourceAdapter protocol."""
from __future__ import annotations

from typing import Protocol

from services.acquisition.types import (
    AcquisitionContext,
    AcquisitionError,
    FetchResult,
    NormalizedItem,
)


class AdapterParseError(Exception):
    def __init__(self, error: AcquisitionError):
        self.error = error
        super().__init__(error.message)


class SourceAdapter(Protocol):
    @property
    def name(self) -> str:
        """Stable adapter id; N3.0: 'rss_atom'."""
        ...

    def parse(
        self,
        ctx: AcquisitionContext,
        fetch: FetchResult,
    ) -> list[NormalizedItem]:
        """Parse+normalize. No network I/O. No DB writes."""
        ...
