"""Immutable versioned contracts; this module is not wired into live routing."""
from .v1 import *  # noqa: F401,F403
from .compatibility import adapt_coding_provider_descriptor, adapt_legacy_provider, adapt_product_provider_entry
