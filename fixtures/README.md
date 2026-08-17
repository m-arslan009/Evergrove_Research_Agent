# Fixtures — offline replay data

Everything the tools need to work in a fresh clone with no key, no network and no quota.
`SEARCH_BACKEND=fixture` is the committed default, and this directory is what makes that
default true.

```
fixtures/
├── search/       recorded search responses, replayed by FixtureSearchBackend
├── documents/    attachments read_document can open (ALLOWED_ATTACHMENT_DIR points here)
└── html/         page markup for extract_html and offline fetch tests
```

`ALLOWED_ATTACHMENT_DIR` defaults to this directory, so `read_document` accepts a path
relative to it (`documents/indexing-brief.md`). `SEARCH_FIXTURE_DIR` defaults to
`fixtures/search`.

## Provenance: handwritten is not knowledge

Every search recording carries `recorded_from`:

| Value | Meaning |
| --- | --- |
| `handwritten` | **Seed data.** Written by hand to be the right *shape*, not to be true. |
| `serpapi` | Captured from a real SerpAPI response. |
| `academic` | Captured from OpenAlex, Crossref or arXiv. |

Handwritten fixtures exist so the pipeline can be exercised end to end offline. They are
**not** research material: no report may cite one, and nothing in them should be treated as
a fact about the world. The URLs are real pages, the titles and snippets are plausible
paraphrases — that combination is exactly enough to test ranking, dedup, authority
classification and passage selection, and not enough to answer a question.

**12 recordings are `serpapi`** — captured live during the Day 3 S14 acceptance step, each in
the same session as the call it came from, including the calls spent by runs that later failed
or were killed. The remaining 5 are the original `handwritten` seeds, and the prohibition above
still applies to those alone: no report may cite a handwritten fixture.

Further real captures land in `search/` the same way, one file per live query, in the same
session as the call — an uncaptured live search burns SerpAPI quota twice.

Recordings are written from the `search_cache` table, whose `results_json` already holds exactly
what the backend returned as `RawSource`, so a recording is a re-serialisation and never a
reconstruction. A `(query, source_type)` already on disk is skipped rather than duplicated —
two files claiming one key is a loud `SearchBackendError` that breaks every offline run.

## Search fixture format

A file describes itself; the filename is not the key. Rename it freely.

```json
{
  "query": "how to read a postgresql explain plan",
  "source_type": "general",
  "recorded_from": "handwritten",
  "note": "optional, for humans",
  "results": [
    {"url": "https://...", "title": "...", "snippet": "..."}
  ]
}
```

`FixtureSearchBackend` indexes every `*.json` here once, keyed on
`(query with whitespace collapsed and case folded, source_type)`. So `"  POSTGRESQL   B-Tree
Index "` finds the recording filed under `postgresql b-tree index`, while the same query
under a different `source_type` does not — a `docs` request must never replay a `general`
recording.

A result item may carry **only** `url`, `title` and `snippet`; `RawSource` forbids anything
else, and `source_backend` is stamped `"fixture"` by the backend at load time (where *this*
run's source came from, which is not the same thing as `recorded_from`).

Three ways a recording fails, all loud, all `SearchBackendError(retryable=False)`:

- **no match** — the message lists every query that *is* recorded;
- **unreadable** — bad JSON, a missing key, or a result with an unexpected field, named by
  filename;
- **duplicate** — two files claiming the same `(query, source_type)`, both named.

None of these degrades to an empty list, because an empty list means "this query found
nothing" — which is a success, and is recorded deliberately in `no-results.json`.

## Adding a recording

1. Drop the JSON in `search/`, with a descriptive filename and honest `recorded_from`.
2. Keep one file per `(query, source_type)`.
3. Run `uv run pytest tests/unit/test_search_backends.py` — the committed set is loaded and
   replayed there, so a typo fails a test instead of surfacing mid-run.

## Binaries

There are none, deliberately. PDFs and DOCX files are not human-readable, do not diff, and
both test suites already build them in memory (`make_pdf` in `tests/conftest.py`,
`build_docx` in `tests/unit/test_read_document.py`). Generate one into a temporary
directory rather than committing it.
