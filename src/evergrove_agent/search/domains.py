"""The one place a domain is turned into an authority class.

`domains.json` holds the map itself; this module loads it once, validates it, and answers
`classify_domain`. Source normalisation and, later, search ranking both call in here — the
map is never copied into either of them, so a domain is reclassified in exactly one place.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from evergrove_agent.schemas import SourceAuthority

_DOMAINS_FILE = Path(__file__).with_name("domains.json")

UNKNOWN: SourceAuthority = "unknown"

AUTHORITY_ORDER: dict[SourceAuthority, int] = {
    "official": 0,
    "standards": 1,
    "primary": 2,
    "secondary": 3,
    "unknown": 4,
}
"""Rank order for the classes: lower sorts first. Also the set of names `domains.json`
is allowed to use."""


@lru_cache(maxsize=1)
def _domain_classes() -> dict[str, SourceAuthority]:
    """`domains.json` inverted into `suffix -> class`, read once per process.

    The file is grouped by class because that is how a human edits it; lookups need the
    opposite shape. A typo'd class name or a domain listed twice raises here rather than
    quietly misclassifying every source from that domain.
    """
    payload = json.loads(_DOMAINS_FILE.read_text(encoding="utf-8"))

    table: dict[str, SourceAuthority] = {}
    for authority, suffixes in payload.items():
        if authority.startswith("_"):  # the self-documenting "_comment" key
            continue
        if authority not in AUTHORITY_ORDER or authority == UNKNOWN:
            raise ValueError(
                f"{_DOMAINS_FILE.name}: {authority!r} is not a source authority class"
            )
        for suffix in suffixes:
            key = suffix.strip().lower().strip(".")
            if not key:
                raise ValueError(
                    f"{_DOMAINS_FILE.name}: empty suffix under {authority!r}"
                )
            if key in table:
                raise ValueError(
                    f"{_DOMAINS_FILE.name}: {key!r} is listed under both "
                    f"{table[key]!r} and {authority!r}"
                )
            table[key] = authority  # type: ignore[assignment]
    return table


def _lookup_host(host: str) -> str:
    """The host reduced to the form the map is keyed on.

    This is the *only* place `www.` equivalence is applied. Canonical URLs keep whatever
    host they arrived with, because dropping `www.` there would change which page a later
    fetch requests; for deciding whether a domain is authoritative it makes no difference.
    """
    normalised = host.strip().lower().rstrip(".")
    return normalised[4:] if normalised.startswith("www.") else normalised


def classify_domain(host: str) -> SourceAuthority:
    """`host`'s authority class, or `"unknown"` when nothing in the map matches.

    Entries match the host itself and any subdomain of it, longest suffix first — so
    `nist.gov` is standards while every other `.gov` falls through to the broad rule.
    """
    normalised = _lookup_host(host)
    if not normalised:
        return UNKNOWN

    table = _domain_classes()
    labels = normalised.split(".")
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        if candidate in table:
            return table[candidate]
    return UNKNOWN
