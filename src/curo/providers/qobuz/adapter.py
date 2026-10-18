"""Qobuz provider adapter stub."""

from curo.core.registry import ProviderRegistry
from curo.providers.base import ProviderStub


class QobuzProvider(ProviderStub):
    """Qobuz streaming service adapter."""

    _name = "qobuz"
    _display_name = "Qobuz"


ProviderRegistry.register("qobuz", QobuzProvider)

