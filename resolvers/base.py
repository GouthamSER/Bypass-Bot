from dataclasses import dataclass
from typing import Optional


@dataclass
class ResolveResult:
    success: bool
    title: Optional[str] = None
    size: Optional[str] = None
    direct_url: Optional[str] = None
    source: Optional[str] = None
    error: Optional[str] = None
    url: Optional[str] = None


class BaseResolver:
    name = "Base"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return False

    async def resolve(self, url: str) -> dict:
        raise NotImplementedError
