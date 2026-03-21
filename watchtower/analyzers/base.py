"""Base analyzer class for Watchtower analysis scripts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from watchtower.store.journal import Journal


class BaseAnalyzer(ABC):
    """Abstract base for all analyzers."""

    def __init__(self, journal: Journal):
        self._journal = journal

    @abstractmethod
    def analyze(self, **kwargs) -> dict | list:
        """Run analysis and return structured results."""
        ...
