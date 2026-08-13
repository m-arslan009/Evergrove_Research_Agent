"""Shared fixtures. Everything here is offline and model-free."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from evergrove_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Defaults only — `_env_file=None` keeps a developer's real `.env` out of the run."""
    return Settings(_env_file=None)


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a genuine text layer. Offsets are computed, not guessed.

    Shared because both `read_document` (a file on disk) and `fetch_url` (bytes off the
    network) need a real PDF, and a committed binary would be the alternative. An empty
    `lines` yields a page with no extractable text — the scanned-document case.
    """
    show = "\n".join(
        f"BT /F1 12 Tf 72 {720 - 20 * index} Td ({line}) Tj ET"
        for index, line in enumerate(lines)
    ).encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(show), show),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@pytest.fixture
def build_pdf() -> Callable[[list[str]], bytes]:
    """`make_pdf` as a fixture, so tests need no cross-module import."""
    return make_pdf


@pytest.fixture
def valid_report_payload() -> dict[str, Any]:
    """A FocusPreparationReport that must always validate.

    Tests mutate a copy of this to prove each rule rejects what it should.
    """
    return {
        "run_id": "run_a71c3f",
        "generated_at": "2026-08-11T09:00:00Z",
        "model_used": "qwen3:4b",
        "original_task": "Learn PostgreSQL indexing",
        "session_duration_minutes": 25,
        "interpreted_goal": "Understand B-tree indexes well enough to read EXPLAIN output",
        "session_objective": "Read EXPLAIN and tell an index scan from a seq scan",
        "topics_to_cover": ["What an index is", "B-tree basics", "Reading EXPLAIN"],
        "topics_to_skip": ["GIN", "GiST", "BRIN"],
        "resources": [
            {
                "title": "PostgreSQL: Indexes",
                "url": "https://www.postgresql.org/docs/current/indexes.html",
                "why_this_source": "Official documentation for the exact version in use",
                "authority": "official",
            }
        ],
        "practice": {
            "instruction": "Create a table with 10k rows, run EXPLAIN on a filtered SELECT",
            "expected_outcome": "You can point at the line that says Index Scan",
        },
        "success_criteria": "You can explain why one query used the index and another did not",
        "assumptions": ["The user has psql and a local database"],
        "unknowns": ["Which PostgreSQL version the user runs"],
        "hops_used": 1,
        "sources_examined": 2,
    }
