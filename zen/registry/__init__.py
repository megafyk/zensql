"""Repo registry — per-repo declarative metadata about Metabase data sources.

The registry is the single source of truth for which Metabase databases each
registered repository may consult. Schema mirrors the debb `debug-repo` skill
shape, narrowed to the `metabase` discriminator branch (zensql only generates
SQL against Metabase data — quickwit/prometheus sources are out of scope).
"""
from __future__ import annotations

from zen.registry.models import (
    ConnectionBlock,
    MetabaseSource,
    MetabaseSourceMetadata,
    RegistryDocument,
    RepoEntry,
)
from zen.registry.store import (
    DuplicateRepoError,
    RegistryStore,
    RepoNotFoundError,
)

__all__ = [
    "ConnectionBlock",
    "DuplicateRepoError",
    "MetabaseSource",
    "MetabaseSourceMetadata",
    "RegistryDocument",
    "RegistryStore",
    "RepoEntry",
    "RepoNotFoundError",
]
