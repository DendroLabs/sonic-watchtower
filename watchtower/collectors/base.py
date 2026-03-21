"""Base collector class for Watchtower data collection scripts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import redis


class RedisReader:
    """Thin wrapper around redis-py for reading SONiC Redis databases.

    SONiC uses multiple Redis database instances (typically db 0-6).
    This reader connects to a specific database index.
    """

    # SONiC Redis database indices
    APPL_DB = 0
    ASIC_DB = 1
    COUNTERS_DB = 2
    CONFIG_DB = 4
    STATE_DB = 6

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, db: int = 0):
        self._client = redis.Redis(host=host, port=port, db=db, decode_responses=True)

    @classmethod
    def from_client(cls, client: redis.Redis) -> "RedisReader":
        """Create a RedisReader from an existing Redis client (useful for testing)."""
        reader = cls.__new__(cls)
        reader._client = client
        return reader

    def hgetall(self, key: str) -> dict[str, str]:
        """Get all fields of a hash."""
        return self._client.hgetall(key)

    def keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern."""
        return self._client.keys(pattern)

    def get(self, key: str) -> str | None:
        """Get a string value."""
        return self._client.get(key)


class BaseCollector(ABC):
    """Abstract base for all collectors.

    Each collector reads from one or more Redis databases and returns
    structured data as a dict/list.
    """

    def __init__(self, readers: dict[int, RedisReader] | None = None):
        self._readers = readers or {}

    def _reader(self, db: int) -> RedisReader:
        """Get the RedisReader for a specific database."""
        if db not in self._readers:
            raise RuntimeError(f"No RedisReader configured for database {db}")
        return self._readers[db]

    @abstractmethod
    def collect(self, **kwargs) -> dict | list:
        """Collect data and return structured results."""
        ...
