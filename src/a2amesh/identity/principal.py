"""Canonical machine principals and explicit immutable aliases."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_PRINCIPAL_RE = re.compile(r"^(agent|a2a|mcp|system):[A-Za-z0-9._:-]{1,240}$")


@dataclass(frozen=True, slots=True)
class Principal:
    """A transport-independent authenticated machine or agent identity."""

    id: str
    kind: str
    credential_id: str | None = None
    alias_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.id) is not str or not _PRINCIPAL_RE.fullmatch(self.id):
            raise ValueError("invalid canonical principal id")
        if type(self.kind) is not str:
            raise ValueError("principal kind must be a string")
        prefix = self.id.split(":", 1)[0]
        if prefix != self.kind:
            raise ValueError("principal kind must match its ID prefix")
        if self.credential_id is not None and type(self.credential_id) is not str:
            raise ValueError("principal credential_id must be a plain string or None")
        if type(self.alias_generation) is not int or self.alias_generation < 0:
            raise ValueError("alias_generation must be a non-negative integer")


def issuer_hash(issuer: str) -> str:
    """Return the stable non-secret issuer identifier used in MCP principals."""

    normalized = issuer.rstrip("/").lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:24]


class AliasRegistry:
    """Explicit, immutable source-to-canonical-principal aliases.

    Multiple sources may point at one canonical principal. Alias chains, cycles,
    and retargeting are rejected so historical Task ownership remains stable.
    """

    def __init__(self) -> None:
        self._targets: dict[str, tuple[str, int]] = {}
        self._next_generation = 1

    def add(self, source: str, target: str) -> int:
        Principal(source, source.split(":", 1)[0])
        Principal(target, target.split(":", 1)[0])
        if source == target:
            raise ValueError("an alias cannot target itself")
        existing = self._targets.get(source)
        if existing:
            if existing[0] != target:
                raise ValueError("principal aliases are immutable")
            return existing[1]
        if target in self._targets:
            raise ValueError("alias chains are forbidden")
        if source in {value[0] for value in self._targets.values()}:
            raise ValueError("an existing alias target cannot become an alias source")
        generation = self._next_generation
        self._next_generation += 1
        self._targets[source] = (target, generation)
        return generation

    def resolve(self, source: str) -> tuple[str, int]:
        """Resolve a principal once; absence means no alias generation."""

        return self._targets.get(source, (source, 0))
