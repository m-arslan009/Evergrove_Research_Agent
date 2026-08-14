"""Raw source lists in; canonical, deduplicated, authority-ranked sources out.

Every tool that produces URLs runs its results through here first. `web_search` so
official documentation outranks a blog before the model sees the list, `fetch_url` so
the cache is keyed on one form of a URL rather than five. Nothing here touches the
network, a model, or SQLite — a URL's own text and `domains.json` are the only inputs,
which is what makes two identical runs produce identical output.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.schemas import SourceAuthority
from evergrove_agent.search.domains import AUTHORITY_ORDER, UNKNOWN, classify_domain

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

_TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid"}
)
"""Named campaign parameters dropped outright, alongside anything `utm_*`. Nothing else
is guessed at: an unrecognised parameter may well be what selects the content."""


class RawSource(BaseModel):
    """One source exactly as a backend handed it over, before any cleaning."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    title: str = ""
    snippet: str = ""
    source_backend: str = ""


class NormalizedSource(BaseModel):
    """One source after canonicalisation, carrying the metadata later steps need."""

    model_config = ConfigDict(extra="forbid")

    url: str
    domain: str
    domain_class: SourceAuthority
    title: str = ""
    snippet: str = ""
    source_backend: str = ""


class NormalizeSourcesOutput(BaseModel):
    """The result, with the counts a trace row needs to explain what was discarded.

    Defined beside the function rather than beside the tool because `normalize_sources`
    returns it directly — `fetch_url` and `web_search` call the function, not the
    registry.
    """

    model_config = ConfigDict(extra="forbid")

    sources: list[NormalizedSource] = Field(default_factory=list)
    dropped: int = Field(default=0, ge=0)
    duplicates_removed: int = Field(default=0, ge=0)


def canonicalize_url(raw: str) -> str | None:
    """`raw` reduced to one stable form, or `None` if it is not a usable http(s) URL.

    Conservative on purpose. It lowercases the scheme and host, drops the fragment, a
    redundant default port, a trailing slash and tracking parameters — and touches
    nothing else. Surviving query parameters keep their original order and the host
    keeps any `www.` it arrived with, because either rewrite would change which page a
    later fetch actually requests.
    """
    candidate = raw.strip()
    if not candidate:
        return None
    if not _SCHEME_RE.match(candidate):
        # A bare "example.com/x" is a URL missing its scheme; the alternative is
        # silently dropping a source that was only ever written informally.
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return None

    host = _canonical_host(parts)
    if host is None:
        return None

    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), host, path, urlencode(_kept_params(parts.query)), "")
    )


def normalize_sources(sources: Sequence[RawSource]) -> NormalizeSourcesOutput:
    """Canonicalise, deduplicate, classify and rank `sources`.

    Duplicates are decided on the canonical URL exactly: the first occurrence is kept
    and later ones only fill in metadata it was missing. Ranking is authority class
    first, original position second, through a stable sort — so the same input always
    yields the same order, and a source is never dropped for being ranked last.
    """
    kept: dict[str, NormalizedSource] = {}
    dropped = 0
    duplicates = 0

    for source in sources:
        url = canonicalize_url(source.url)
        if url is None:
            dropped += 1
            continue

        existing = kept.get(url)
        if existing is not None:
            duplicates += 1
            _fill_blanks(existing, source)
            continue

        host = urlsplit(url).hostname or ""
        kept[url] = NormalizedSource(
            url=url,
            domain=host,
            domain_class=classify_domain(host),
            title=source.title.strip(),
            snippet=source.snippet.strip(),
            source_backend=source.source_backend,
        )

    # dict preserves insertion order and sorted() is stable, so ties keep input order.
    ranked = sorted(
        kept.values(),
        key=lambda source: AUTHORITY_ORDER.get(
            source.domain_class, AUTHORITY_ORDER[UNKNOWN]
        ),
    )
    return NormalizeSourcesOutput(
        sources=ranked, dropped=dropped, duplicates_removed=duplicates
    )


def _canonical_host(parts: SplitResult) -> str | None:
    """The host lowercased, without a trailing dot or a redundant default port.

    `None` for a URL with no host, a host no resolver could ever accept, an unparseable
    port, or embedded credentials — a source list is no place to be carrying those.
    """
    try:
        if parts.username or parts.password:
            return None
        hostname, port = parts.hostname, parts.port
    except ValueError:  # a non-numeric port
        return None

    host = (hostname or "").rstrip(".")
    # Whitespace only reaches here through the scheme-less branch above, which turns a
    # phrase like "not a url" into a host no name server can hold. Rejecting it is what
    # makes `fetch_url` answer BAD_ARGUMENTS instead of spending two attempts resolving
    # it. Anything else non-ASCII is left alone: an IDN host is legitimate.
    if not host or any(character.isspace() for character in host):
        return None
    if port is not None and port != _DEFAULT_PORTS[parts.scheme.lower()]:
        return f"{host}:{port}"
    return host


def _kept_params(query: str) -> list[tuple[str, str]]:
    """The query parameters worth keeping, in the order they arrived."""
    return [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not (key.lower().startswith("utm_") or key.lower() in _TRACKING_PARAMS)
    ]


def _fill_blanks(target: NormalizedSource, duplicate: RawSource) -> None:
    """Let a duplicate contribute only what the entry we kept is missing.

    Backends often return the same page twice with different halves of its metadata.
    """
    if not target.title:
        target.title = duplicate.title.strip()
    if not target.snippet:
        target.snippet = duplicate.snippet.strip()
    if not target.source_backend:
        target.source_backend = duplicate.source_backend
