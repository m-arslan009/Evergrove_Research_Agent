"""The backend that answers from disk — the committed default (plan section 10).

This is what makes the rest of the build free. SerpAPI's free tier is 250 searches a
month, so a development loop that spent one per run would burn the month re-learning what
a recorded response already says. Every search here is a dictionary lookup: no network, no
key, no quota, and the same query always gives the same answer.

A fixture file describes itself rather than encoding its identity in its filename, so a
recording can be renamed, read and edited by hand:

    {"query": "...", "source_type": "docs", "recorded_from": "serpapi",
     "results": [{"url": "...", "title": "...", "snippet": "..."}]}

`recorded_from` is provenance for a human reading the file; it does not affect the lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.search.base import SearchBackendError, SearchSourceType
from evergrove_agent.search.normalize import RawSource


def _lookup_key(query: str, source_type: str) -> tuple[str, str]:
    """The index key: whitespace collapsed and case folded, so two spellings of one
    question find the same recording.

    Deliberately not `memory.search_cache.normalize_query`, which applies the same rule:
    `memory` imports `search`, so importing it back would be a cycle. One line is the
    cheaper price.
    """
    return " ".join(query.split()).casefold(), source_type


class FixtureSearchBackend:
    """Replays recorded search responses from `SEARCH_FIXTURE_DIR`."""

    name = "fixture"

    def __init__(
        self, settings: Settings | None = None, *, directory: Path | None = None
    ) -> None:
        self._directory = directory or (settings or get_settings()).search_fixture_dir
        self._index: dict[tuple[str, str], tuple[Path, list[RawSource]]] | None = None

    async def search(
        self,
        query: str,
        *,
        source_type: SearchSourceType,
        max_results: int,
    ) -> list[RawSource]:
        index = self._load()
        entry = index.get(_lookup_key(query, source_type))
        if entry is None:
            raise SearchBackendError(
                self.name,
                f"no recorded response for {query!r} (source_type={source_type!r}) in "
                f"{self._directory}. Recorded: {self._recorded_keys(index)}",
                # A file that is not on disk will not appear on a second identical call.
                retryable=False,
            )
        return entry[1][:max_results]

    def _load(self) -> dict[tuple[str, str], tuple[Path, list[RawSource]]]:
        """Index every fixture in the directory, once.

        Lazy so constructing a backend costs nothing and a missing directory is a failed
        search rather than an import-time crash.
        """
        if self._index is not None:
            return self._index

        index: dict[tuple[str, str], tuple[Path, list[RawSource]]] = {}
        for path in sorted(self._directory.glob("*.json")):
            key, results = self._read(path)
            existing = index.get(key)
            if existing is not None:
                raise SearchBackendError(
                    self.name,
                    f"{path.name} and {existing[0].name} both record {key[0]!r} "
                    f"(source_type={key[1]!r}); replay would be ambiguous",
                    retryable=False,
                )
            index[key] = (path, results)

        self._index = index
        return index

    def _read(self, path: Path) -> tuple[tuple[str, str], list[RawSource]]:
        """One fixture file, or a loud failure naming it.

        A corrupt recording is not degraded into a miss the way a stale cache row is: the
        cache can always fall back to searching again, and this backend has nowhere to
        fall back to. Silence here would look exactly like "that query found nothing".
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            results = [
                RawSource.model_validate({**item, "source_backend": self.name})
                for item in payload["results"]
            ]
            key = _lookup_key(payload["query"], payload["source_type"])
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValidationError,
        ) as exc:
            raise SearchBackendError(
                self.name, f"unreadable search fixture {path.name}: {exc}", retryable=False
            ) from exc
        return key, results

    @staticmethod
    def _recorded_keys(index: dict[tuple[str, str], object]) -> str:
        """What *is* recorded, so a miss says how to fix itself."""
        if not index:
            return "nothing"
        return ", ".join(f"{query!r} ({kind})" for query, kind in sorted(index))
