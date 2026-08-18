"""Structured context produced by deterministic eval fixtures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalFixtureContext:
    """Runtime identifiers created by setup before the Agent is invoked."""

    paper_id: str | None = None
    version_id: str | None = None
