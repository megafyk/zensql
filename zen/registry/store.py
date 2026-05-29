"""Atomic JSON-backed CRUD over the repo registry."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from zen.registry.models import RegistryDocument, RepoEntry


class RegistryError(Exception):
    """Base error for registry operations."""


class DuplicateRepoError(RegistryError):
    pass


class RepoNotFoundError(RegistryError):
    pass


class RegistryStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> RegistryDocument:
        if not self._path.exists():
            return RegistryDocument()
        try:
            data = RegistryDocument.model_validate_json(self._path.read_text(encoding="utf-8"))
        except ValidationError as e:
            raise RegistryError(
                f"Registry at {self._path} is malformed: {e.error_count()} validation errors"
            ) from e
        return data

    def save(self, doc: RegistryDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # by_alias=True so MetabaseSourceMetadata.schema_ serializes as "schema"
        payload = doc.model_dump(by_alias=True, mode="json", exclude_none=False)
        fd, tmp = tempfile.mkstemp(prefix=".registry-", suffix=".json", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                import json

                json.dump(payload, fh, indent=2, sort_keys=False)
                fh.write("\n")
            os.replace(tmp, self._path)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    # CRUD ----------------------------------------------------------------

    def register(self, entry: RepoEntry) -> RepoEntry:
        doc = self.load()
        if any(r.name == entry.name for r in doc.repos):
            raise DuplicateRepoError(f"repo {entry.name!r} already registered")
        doc.repos.append(entry)
        self.save(doc)
        return entry

    def list_repos(self) -> list[RepoEntry]:
        return self.load().repos

    def get(self, name: str) -> RepoEntry:
        doc = self.load()
        for r in doc.repos:
            if r.name == name:
                return r
        raise RepoNotFoundError(f"repo {name!r} not found")

    def update(self, name: str, patch: dict[str, Any]) -> RepoEntry:
        doc = self.load()
        for i, r in enumerate(doc.repos):
            if r.name == name:
                merged = r.model_dump(by_alias=True, mode="json")
                merged.update(patch)
                if (
                    "name" in patch
                    and patch["name"] != name
                    and any(
                        other.name == patch["name"]
                        for other in doc.repos
                        if other.name != name
                    )
                ):
                    raise DuplicateRepoError(
                        f"cannot rename to {patch['name']!r}: already in use"
                    )
                updated = RepoEntry.model_validate(merged)
                doc.repos[i] = updated
                self.save(doc)
                return updated
        raise RepoNotFoundError(f"repo {name!r} not found")

    def delete(self, name: str) -> RepoEntry:
        doc = self.load()
        for i, r in enumerate(doc.repos):
            if r.name == name:
                removed = doc.repos.pop(i)
                self.save(doc)
                return removed
        raise RepoNotFoundError(f"repo {name!r} not found")
