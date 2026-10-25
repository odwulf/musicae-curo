"""Deezer provider adapter stub."""

from curo.core.registry import ProviderRegistry
from curo.providers.base import ProviderStub


class DeezerProvider(ProviderStub):
    """Deezer streaming service adapter."""

    _name = "deezer"
    _display_name = "Deezer"


ProviderRegistry.register("deezer", DeezerProvider)

