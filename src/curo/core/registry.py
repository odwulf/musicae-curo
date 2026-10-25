"""Provider registry for Musicae Curo.

Allows registering provider adapters and resolving the active provider at runtime.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Type
from curo.core.provider import MusicProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry managing available MusicProvider adapters."""

    _providers: Dict[str, Type[MusicProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: Type[MusicProvider]) -> None:
        """Register a provider adapter class."""
        key = name.strip().lower()
        cls._providers[key] = provider_cls
        logger.debug(f"Registered provider '{key}': {provider_cls.__name__}")

    @classmethod
    def get_provider_class(cls, name: str) -> Optional[Type[MusicProvider]]:
        """Retrieve registered provider class by name."""
        return cls._providers.get(name.strip().lower())

    @classmethod
    def list_providers(cls) -> List[str]:
        """List registered provider names."""
        return sorted(list(cls._providers.keys()))

    @classmethod
    def create_provider(cls, name: str, **kwargs) -> MusicProvider:
        """Instantiate a provider by name."""
        key = name.strip().lower()
        p_cls = cls.get_provider_class(key)
        if not p_cls:
            available = ", ".join(cls.list_providers()) or "none"
            raise ValueError(
                f"Unknown or unsupported provider '{name}'. Available providers: {available}"
            )
        return p_cls(**kwargs)

