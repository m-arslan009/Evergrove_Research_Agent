# Capability context — Research Tools

Navigation map for every subtask of the **Research Tools** capability. Read this file first,
then open only the current subtask's *inspect first* files. Do not scan the repository, and do
not open a file listed here just for general understanding.

This is a map, not a reading list. It deliberately does not restate the architecture plan
(`Evergrove_Research_Agent_Architecture_and_7_Day_Plan.NEW.docx`) or anything the code already
says.

## Purpose and scope

Every deterministic tool the agent will call, working and tested **before any agent or model
exists** — so when the research loop later misbehaves, the tools are already ruled out.

**In scope:** the tool registry and shared contract · document readers and `read_document` ·
URL normalisation and domain classification · SQLite connection plus the source cache ·
`fetch_url` · `web_search` with its pluggable backends, search cache and monthly quota guard ·
the deterministic passage selector · a tools CLI and recorded fixtures.

**Out of scope (later capabilities):** the agent loop and tool-calling integration · session and
persistent memory (`recall_previous_preparation`, `save_preparation`) · hooks, tracing and budget
counters (the registry's hook lists stay empty here) · the supervisor/worker split · MCP · evals.

`validate_report` is **not** part of this capability — plan §23 feature 5 places it in the
single-agent loop. (`README.md` calls it a Day 2 item; the plan wins. Raise it if it is wanted
here.)

**Branch:** `feature/research-tools` (all subtasks stay on it; a subtask never gets its own
branch).

## Relevant folders and files

| Path | Role |
| --- | --- |
| `src/evergrove_agent/tools/` | `base.py` (contract), `registry.py` (the only call path), the tools themselves |
| `src/evergrove_agent/documents/` | `excerpt.py`, `base.py`, `reader.py`, the format readers `text.py`/`pdf.py`/`docx.py`, and `html.py` (S6) |
| `src/evergrove_agent/search/` | `domains.json`/`domains.py` and `normalize.py` (S4), `base.py` (the backend contract); `fixture.py`, `serpapi.py`, `academic.py` land with S7 |
| `src/evergrove_agent/memory/` | `db.py` (all DDL), `cache.py`, `search_cache.py`, `budget.py` |
| `src/evergrove_agent/schemas/tools.py` | `ToolResult`, `ToolError`, `ErrorCode` — the envelope every tool returns |
| `src/evergrove_agent/config.py` | every budget, path, TTL and backend switch |
| `tests/unit/`, `tests/integration/`, `tests/conftest.py` | offline suites — units, then the assembled stack (S10); `settings` fixture is `Settings(_env_file=None)` |
| `fixtures/` | offline replay data: `search/` recordings, `documents/` attachments, `html/` markup, and `README.md` (the provenance rules) |
| `.env.example` | the documented setting set; committed defaults must stay offline |

## Do not inspect by default

- `frontend/`, `backend/`, and the repository-root Evergrove docs (`project_idea.md`,
  `product_analysis.md`, `backend_architecture.md`, `CONTRACT.md`) — a different product.
- `src/evergrove_agent/llm/` and `schemas/report.py` — no tool talks to a model or builds a
  report.
- `src/evergrove_agent/main.py` — the CLI wires tools in only at the CLI subtask.
- `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `uv.lock`, the `.docx` plan (extract a section only
  when a decision is genuinely unclear).
- Tests belonging to other subtasks.

## Subtasks

Order and dependencies:

```
S1 registry ──┬─► S3 read_document ──┐
S2 excerpt ───┘                      │
S1 ──► S4 normalize_sources ──┬──────┼─► S8 tools CLI + fixtures ──► S10 acceptance
config ─► S5 sqlite cache ────┴─► S6 fetch_url ─┤
                              └─► S7 web_search ┘
```

`S3`–`S5` are independent of each other and may be done in any order. `S6` needs `S3`'s PDF
reader, `S4` and `S5`. `S7` needs `S4` and `S5`. `S9` assembles the finished tools into one
registry and comes before `S8`, which is a thin surface over it. `S10` is the offline
acceptance pass over everything above it and is last.

---

### S1 — Tool registry and shared contract · **complete**

*Inspect first:* `tools/base.py`, `tools/registry.py`
*Only if needed:* `tests/unit/test_tool_registry.py`, `schemas/tools.py`

**Provides:** `ToolRegistry.register/get/names/call`, `add_pre_hook`, `add_post_hook`;
`Tool` protocol, `RunContext`, `ToolInvocation`, `PreToolHook`, `PostToolHook`.
Call order: resolve → validate args → pre-hooks → `Tool.run` → post-hooks. `call` never raises;
it times the call and stamps `duration_ms` centrally.

**Decisions:** hook lists exist but stay empty until the tracing capability · a pre-hook
returning a `ToolResult` short-circuits the tool (how cache hits and budget refusals will work) ·
duplicate registration raises at wiring time, everything at call time is a `ToolResult`.

**Must not change:** the `Tool` protocol or `ToolResult` shape (breaks every tool, the future
hooks and both future workers) · the registry must stay free of models, HTTP, SQLite and any
specific tool — which is why the tools are assembled into it from outside, in `S9`.

---

### S2 — Deterministic passage selector · **complete**

*Inspect first:* `documents/excerpt.py`
*Only if needed:* `tests/unit/test_excerpt.py`, `config.py` (`source_excerpt_chars`)

**Provides:** `select_passages(text, question, *, max_chars=None) -> str` and `GAP_MARKER`,
re-exported from `documents/__init__.py`. Scores paragraph blocks by keyword overlap, returns the
best of them in document order with heading context, under `SOURCE_EXCERPT_CHARS`.

**Decisions:** keyword overlap only — no model, embeddings, network or database, so the same page
and question always give the same excerpt · text shorter than the limit is returned unchanged ·
`SOURCE_EXCERPT_CHARS` (what the model sees) stays separate from each reader's `max_chars` (what
is extracted and cached).

**Must not change:** the scoring weights or the signature without a stated reason — both readers
and `fetch_url` end in this function.

---

### S3 — Document readers and `read_document` · **complete**

*Inspect first:* `documents/reader.py`, `tools/read_document.py`
*Only if needed:* `documents/base.py`, the format module in question (`text.py`, `pdf.py`,
`docx.py`), `tests/unit/test_read_document.py`

**Provides:** re-exported from `documents/__init__.py` — `read_document_file(path, *,
settings=None) -> ParsedDocument` (path guard → size guard → suffix routing),
`select_section(document, hint) -> (OutlineEntry, str) | None`, the readers `read_text` /
`read_pdf` / `read_docx`, the `READERS` suffix map, and the types `ParsedDocument{text, outline,
page_count}`, `OutlineEntry{title, level, char_offset, page}`, `Block`, `DocumentReadError`. The
registered tool is `ReadDocumentTool` in `tools/read_document.py` (`ReadDocumentInput{path,
mode=full, section_hint?}` → `ReadDocumentOutput{path, file_type, page_count, outline, text,
truncated}`). Also added: `MAX_DOCUMENT_BYTES` (config + `.env.example`), `ErrorCode.CORRUPT_DOCX`,
and `pypdf` as a runtime dependency.

**Contracts:** `.txt`/`.md`/`.pdf`/`.docx` · `path` resolves inside `ALLOWED_ATTACHMENT_DIR`
(relative paths are taken as relative to *it*, not the cwd; containment is checked before
existence, so a probe outside cannot learn what is there) · every failure is its own `ErrorCode`
(`NOT_FOUND`, `PATH_NOT_ALLOWED`, `UNSUPPORTED_TYPE`, `EMPTY_FILE`, `BUDGET_EXCEEDED` for oversize,
`CORRUPT_PDF`, `ENCRYPTED_PDF`, `NO_TEXT_LAYER`, `CORRUPT_DOCX`), always `retryable=False` · readers
raise `DocumentReadError`, the tool converts it once — the same split as `SearchBackendError` ·
`full`/`section` end in `select_passages`, `outline` returns no body text.

**Decisions:** `.docx` was added to the S3 scope on request · **no `python-docx`** — a `.docx` is a
zip of XML, so stdlib `zipfile` + `ElementTree` read `word/document.xml` directly, and `outline`
/`section` need only paragraphs and their Word heading styles; `lxml` is not worth it · the outline
is never guessed: Markdown ATX/setext headings, Word heading styles, PDF bookmarks — a `.txt`, a
style-less `.docx` and a bookmark-less PDF all get an empty outline, and `section` then falls back
to keyword selection over the whole text rather than slicing at an invented boundary · a section
runs to the next heading at the same or a higher level, so subsections stay inside their parent ·
`select_section` matches headings by exact/substring/word overlap — heading *lookup*, deliberately
not passage scoring, which stays `select_passages`' job · `full` with a `section_hint` steers the
trimming; without one, `select_passages` falls through to its own truncation · `truncated` means
the returned text is shorter than what was extracted · a PDF page that fails to extract is skipped,
not fatal; a malformed bookmark tree yields no outline rather than losing the document · bad bytes
in text/Markdown decode with `errors="replace"`, never raise.

**Must not use or change:** no OCR · no new `ErrorCode` unless a failure genuinely has none · one
tool with a `mode` enum, not three tools · `pypdf` is the PDF dependency named by the plan · S6
reuses `read_pdf`/`read_document_file` — do not write a second PDF path for `fetch_url`.

---

### S4 — `normalize_sources` · **complete**

*Inspect first:* `search/normalize.py`, `search/domains.py`
*Only if needed:* `search/domains.json`, `tools/normalize_sources.py`,
`tests/unit/test_normalize_sources.py`

**Provides:** re-exported from `search/__init__.py` —
`canonicalize_url(raw) -> str | None`, `classify_domain(host) -> SourceAuthority`,
`normalize_sources(sources) -> NormalizeSourcesOutput`, the models `RawSource`,
`NormalizedSource`, `NormalizeSourcesOutput{sources, dropped, duplicates_removed}`, and
`AUTHORITY_ORDER`. `NormalizedSource` carries `url`, `domain`, `domain_class`, `title`,
`snippet`, `source_backend`. The registered tool is `NormalizeSourcesTool` in
`tools/normalize_sources.py` (`NormalizeSourcesInput{sources}` → `NormalizeSourcesOutput`).
Pipeline: canonicalise → drop unusable → dedupe → classify → stable authority sort.

**Decisions:** the map is `search/domains.json`, not YAML — PyYAML is not worth adding for a
static file; grouped by class, longest suffix wins, so `nist.gov` beats the broad `gov` entry ·
`search/domains.py` is the *only* loader and classifier, reused as-is by S7's ranking — never
copy the map · `domain_class` reuses the existing `SourceAuthority` literal, no new enum ·
canonicalisation is deliberately conservative: scheme/host lowercased, fragment, default port,
trailing slash, `utm_*` and named tracking params dropped, and nothing else — query order is
preserved and `www.` is *not* stripped from the URL, only during authority lookup · dedup is
exact canonical-string match, so `www`/non-`www` and `http`/`https` variants of one page still
survive as two (one-line fix if S6 shows it wastes fetches) · S6/S7 compose with the pure
function; routing through the registry from inside another tool would be circular · unusable
URLs are counted in `dropped`, never raised.

**Must not use or change:** no network lookups, no live domain reputation service · `domains.json`
contents are cheap to change, the classification *values* are not · the output shape belongs to
`web_search` and `fetch_url` too.

---

### S5 — SQLite and the source cache · **complete**

*Inspect first:* `memory/cache.py`, `memory/db.py`, `config.py` (`db_path`, `cache_ttl_days`)
*Only if needed:* `tests/unit/test_source_cache.py`, `tests/unit/test_db.py`, `tests/conftest.py`

**Provides (base layer):** re-exported from `memory/__init__.py` —
`connect(db_path=None)`, `initialize_schema(conn)`, `transaction(conn)` (a context manager
yielding a cursor), `open_database(db_path=None)` (connect → initialise → close), plus
`SCHEMA_STATEMENTS` and `SCHEMA_VERSION`. `connect` falls back to `DB_PATH` from config, creates
the parent directory, sets `row_factory = sqlite3.Row` and the pragmas `foreign_keys=ON`,
`journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`. The only table so far is
`schema_meta(key, value)`, holding `schema_version`.

**Provides (source cache):** also re-exported from `memory/__init__.py` — `CachedSource`
(a frozen dataclass: `url`, `final_url`, `title`, `text`, `content_type`, `fetched_at`,
`expires_at`), `get_cached_source(connection, url, *, now=None) -> CachedSource | None`
and `store_cached_source(connection, *, url, text, final_url=None, title="",
content_type="", ttl_days=None, now=None) -> CachedSource`, all in `memory/cache.py`. The
`source_cache` table (canonical `url` as primary key) was appended to `SCHEMA_STATEMENTS`;
`SCHEMA_VERSION` stayed 1 — no existing table changed shape. Nothing wires it into a hook
yet, and no tool reads it until S6.

**Cache decisions:** the key is `canonicalize_url(url)` from S4, applied on both get and
store, so the trailing-slash / `utm_*` / uppercase-host / default-port / scheme-less
variants of one page share one entry (`www.` still does not — S4 keeps it deliberately) ·
a missing row, an expired row and an uncanonicalizable URL are all one answer, `None`,
because the caller does the same thing with each · **reads never write**: an expired row is
left in place and overwritten by the next store, so there is no purge sweep and no write on
the read path · expiry is compared in Python, not SQL, so the stored ISO-8601 format stays
inside `cache.py`; timestamps are written timezone-aware in UTC and a naive one parses back
as UTC · `store` is `INSERT OR REPLACE`, so refreshing an expired page is the same call as
caching it the first time · `store` raises `ValueError` on an uncanonicalizable URL — the
caller canonicalises before it fetches, and an invented key would poison the table · `now`
is the only injected seam (default `datetime.now(UTC)`), which is what makes expiry testable
without freezing global time · `ttl_days` falls back to `CACHE_TTL_DAYS` at call time, the
same late-binding as `connect`'s `db_path` · `CachedSource` is a dataclass, not a pydantic
model: it never crosses a tool boundary, and tool-facing shapes stay in `schemas/`.

**Decisions:** **`db.py` owns all DDL**, in `SCHEMA_STATEMENTS`; feature modules own only their
queries — `cache.py` needs `connect`, so `db.py` importing it back would be a cycle and creation
order would follow import order. Adding a table later is appending an `IF NOT EXISTS` statement
to that tuple; the layer itself does not change · `initialize_schema` is idempotent and
non-destructive, cheap enough to run on every open, which is why `open_database` does · writes
that span statements go through `transaction`, not `with connection:` — the latter does not wrap
DDL and leaves the cursor to the caller · no migration runner: `SCHEMA_VERSION` is a marker for
whoever first needs to change an existing table's shape.

**Must not use or change:** no Redis, no ORM, no second database file · `db.py` stays free of
tools, models, HTTP and search backends (stdlib + `config` only) · `cache.py` opens no
connection of its own — the caller passes one from `connect`/`open_database` · it stays free
of HTTP, extraction and ranking: S6 fetches and extracts, then hands the result here · no
generic repository or cache abstraction, and the search cache (S7) gets its own table and
queries rather than a shared one · tests must not write to the real `DB_PATH` — point it at a
temporary path.

---

### S6 — `fetch_url` · **complete**

*Inspect first:* `tools/fetch_url.py`, `documents/html.py`
*Only if needed:* `tests/unit/test_fetch_url.py`, `memory/cache.py` (from S5), `documents/pdf.py`
(from S3), `config.py` (`fetch_timeout_s`, `max_fetch_bytes`)

**Provides:** `FetchUrlTool` in `tools/fetch_url.py` (`FetchUrlInput{url, max_chars=20000,
excerpt_for?}` → `FetchUrlOutput{url, final_url, title, text, char_count, truncated, from_cache,
retrieved_at}`), constructed as `FetchUrlTool(settings=None, *, client=None, connection=None)` —
the same injection seams the search backends use. Also `extract_html(markup) -> (title, text)` in
`documents/html.py`, re-exported from `documents/__init__.py`. Added `MAX_FETCH_BYTES`,
`FETCH_TIMEOUT_S` and `MAX_SOURCE_TEXT_CHARS` (config + `.env.example`). No new dependency and no
new `ErrorCode`. The PDF builder moved from `tests/unit/test_read_document.py` to the `build_pdf`
fixture in `tests/conftest.py`, since both suites now need one. First use of `logging` in `src/`:
a module-level `logging.getLogger(__name__)`, warnings only, no handler configured — a library
names its logger and leaves handlers to the application.

Flow: `canonicalize_url` → `get_cached_source` → (miss) streamed `httpx` GET → content-type
routing → `store_cached_source` → `select_passages`. Cache hit and fresh fetch end in the same
shaping function, so one URL and one question give one answer either way.

**Contracts:** one URL per call, redirects followed, `final_url` reports where it landed while the
cache stays keyed on the **requested** canonical URL (S5's `final_url` column is what that column
is for) · exactly one retry, fired when the failure is marked `retryable` — a timeout, a network
error, a 5xx or a 429; a 4xx is never asked twice · failures: `BAD_ARGUMENTS` (uncanonicalizable
URL), `TIMEOUT`, `FETCH_FAILED`, `NOT_FOUND` (404/410), `BUDGET_EXCEEDED` (over
`MAX_FETCH_BYTES`), `UNSUPPORTED_TYPE`, `EMPTY_FILE` (nothing extracted), and the PDF reader's own
`CORRUPT_PDF`/`ENCRYPTED_PDF`/`NO_TEXT_LAYER` · nothing that failed is cached · a `sqlite3.Error`
on either cache path is **non-fatal but logged** at `WARNING`: the fetched page is still returned,
and a cache that silently never answers is indistinguishable from a slow network.

**Decisions:** **the cache stores the whole extracted source text**, bounded only by
`MAX_SOURCE_TEXT_CHARS` — never raw HTML (every hit would re-extract and the file would bloat),
never an excerpt, and never a reading budget. Neither `SOURCE_EXCERPT_CHARS` nor the caller's
`max_chars` may bound it: both describe *one answer*, and trimming the source to either is what
would make a later, differently-worded `excerpt_for` re-read the previous question's paragraphs
from a page that is never re-fetched to correct it. `max_chars` caps only what a call returns;
`select_passages` runs on the full cached text every time, against
`min(max_chars, SOURCE_EXCERPT_CHARS)` · **PDF bytes reach `read_pdf` through a file in a
`TemporaryDirectory`**,
named after the URL so the reader's messages stay recognisable — `read_document_file` cannot be
used because its guard resolves inside `ALLOWED_ATTACHMENT_DIR` and would answer
`PATH_NOT_ALLOWED` for bytes we downloaded, and widening `read_pdf`'s signature would change a
finished S3 contract for one caller · **`html.py` is stdlib `html.parser`, not `trafilatura`** (a
deliberate, recorded departure from the plan's named dependency, on the user's call): it drops
`script`/`style`/`nav`/`header`/`footer`/`aside`/`form` subtrees and emits blank-line-separated
paragraphs with Markdown `#` headings and four-space-indented `<pre>`, which is exactly the shape
`select_passages` splits and scores — so heading context and the code bonus work on a web page the
same way they do on a local document. **Revisit when** S8's recorded fixtures show the output is
too noisy; the swap is `extract_html`'s body and no caller's business · the download is streamed
and abandoned the moment it passes `MAX_FETCH_BYTES` · `MAX_FETCH_BYTES` and `FETCH_TIMEOUT_S` are
separate from `MAX_DOCUMENT_BYTES` and `SEARCH_TIMEOUT_S`: a page a stranger's server hands us is
not an attachment the user chose, and a slow page is not a slow search backend · `MAX_FETCH_CALLS`
is **not** enforced here — that is a registry pre-hook, and the hook lists stay empty for this
capability.

**Must not use or change:** no headless browser, no JS rendering — a client-side-rendered page
comes back as `EMPTY_FILE`, by design · no crawling or link-following — one URL per call · never
bypass the registry · do not add a second PDF path or a second HTML extractor.

---

### S7 — `web_search`, backends, search cache and quota guard · **complete**

*Inspect first:* `tools/web_search.py`, `search/base.py`, `search/__init__.py` (the factory)
*Only if needed:* `memory/search_cache.py`, `memory/budget.py`, `config.py` (search block),
`search/fixture.py`, `search/serpapi.py`, `search/academic.py`, `schemas/tools.py`,
`.env.example`, `tests/unit/test_web_search.py`, `tests/unit/test_search_backends.py`,
`tests/unit/test_search_cache.py`, `tests/unit/test_search_budget.py`

**Provides (backend contract, done):** re-exported from `search/__init__.py` —
`SearchBackend` (a `runtime_checkable` Protocol: `name: str` plus
`async search(query, *, source_type, max_results) -> list[RawSource]`),
`SearchSourceType = Literal["docs","technical","academic","general"]`, and
`SearchBackendError(backend, message, *, retryable=True)`.

**Provides (search cache, done):** re-exported from `memory/__init__.py` — `CachedSearch`
(frozen dataclass: `key`, `query`, `backend`, `source_type`, `max_results`,
`results: tuple[RawSource, ...]`, `searched_at`, `expires_at`), `normalize_query(query)`,
`search_cache_key(query, *, backend, source_type, max_results)`,
`get_cached_search(connection, query, *, backend, source_type, max_results, now=None)`
and `store_cached_search(connection, *, query, backend, source_type, max_results, results,
ttl_days=None, now=None)`, all in `memory/search_cache.py`. Added `SEARCH_CACHE_TTL_DAYS`
(config + `.env.example`) and the `search_cache` table.

**Provides (quota guard, done):** also from `memory/__init__.py` — `BudgetReservation`
(frozen dataclass: `granted`, `month`, `used`, `limit`), `consumes_quota(backend)`,
`QUOTA_CONSUMING_BACKENDS`, `current_month(now=None)`, `get_search_usage(connection, *,
now=None)` and `reserve_search_call(connection, *, limit=None, now=None)`, in
`memory/budget.py`, plus the `search_budget` table. `SCHEMA_VERSION` stayed 1 — both tables
are new, no existing table changed shape. Nothing wires either into `web_search` yet.

**Cache decisions:** the key is `sha256` over
`normalized_query \x1f backend \x1f source_type \x1f max_results` — the only four inputs
that change what a backend would return, so case and whitespace variants of one query share
an entry while a different backend, source type or limit never falsely does · `max_results`
stays *in* the key: serving a 6-result request from a cached 10-result row is a wider policy
that was deliberately not taken · the payload is JSON `RawSource` (S4's model), so a hit
reconstructs exactly what a live call would have returned and nothing provider-specific
reaches the table · a missing row, an expired row, an empty query and unreadable
`results_json` are all one answer, `None` — a row from an older build degrades to a miss
rather than taking a tool down · an **empty result list is cached like any other**: a query
that found nothing has been answered, and re-asking costs quota for the same silence ·
otherwise it mirrors `cache.py` exactly (reads never write, `INSERT OR REPLACE` so refresh
and first store are one call, expiry compared in Python against an injected `now`, TTL
late-bound from config) · `SEARCH_CACHE_TTL_DAYS` is separate from `CACHE_TTL_DAYS` because
search freshness and page-text freshness are independently tunable, and this is the one that
protects quota.

**Budget decisions:** the check and the increment are **one atomic statement** —
`INSERT ... ON CONFLICT(month) DO UPDATE SET used = used + 1 WHERE used < ? RETURNING used`
(SQLite ≥ 3.35) — so no two callers can both read `limit - 1` and both spend it; a returned
row *is* the grant · a `limit <= 0` is refused *before* that statement, because the `WHERE`
guards only the update branch and a fresh month would otherwise slip one call through the
plain `INSERT` · the month is UTC `YYYY-MM`, so a new month is a new row and a fresh
allowance with nothing to reset · **reserve before the call, and no refund path**: a search
that times out may still have counted at the provider, so over-counting is the safe
direction · `reserve_search_call` returns a dataclass, **not** a `ToolResult` — storage stays
off the tool boundary, and `web_search` maps `granted=False` onto the already-existing
`ErrorCode.MONTHLY_BUDGET_EXCEEDED`, `retryable=False` · `QUOTA_CONSUMING_BACKENDS` is
`{"serpapi"}` only: `fixture`, `academic` and `ddgs` are unmetered, and keeping that list
here rather than in `web_search` is what makes "an offline run cannot burn the live tier"
provable before a backend exists.

**Provides (backends, done):** re-exported from `search/__init__.py` —
`FixtureSearchBackend` (`search/fixture.py`), `SerpApiSearchBackend` (`search/serpapi.py`),
`AcademicSearchBackend` (`search/academic.py`) and
`build_search_backend(name=None, settings=None) -> SearchBackend`, the one place a
`SEARCH_BACKEND` value becomes a class (the shape `build_provider` already has for models).
Added `SEARCH_FIXTURE_DIR` and `SEARCH_TIMEOUT_S` (config + `.env.example`), and the seed
recording `fixtures/search/postgresql-indexing.json`. No new dependency: `httpx` plus stdlib
`json`/`ElementTree` cover all three.

**Backend decisions:** a fixture file is **self-describing** (`query`, `source_type`,
`recorded_from`, `results`) and the backend indexes the directory on first use, so a recording
can be renamed and read by hand and a miss can list what *is* recorded — the filename is not
the key · the fixture key repeats `normalize_query`'s one-line rule locally rather than
importing it: `memory` imports `search`, so the reverse is a cycle · a corrupt, missing or
duplicated fixture is a **loud** `SearchBackendError(retryable=False)`, never an empty list —
unlike a stale cache row, this backend has nothing to fall back to, and silence here is
indistinguishable from "found nothing" · results are stamped `source_backend="fixture"`, not
the `recorded_from` provenance, because that is where *this run's* source came from ·
SerpAPI's `organic_results_state == "Fully empty"` is read as an empty list, not a failure ·
SerpAPI 401/403/429 are `retryable=False` (a rejected key, an exhausted tier) and 5xx/network
are retryable — the flag is what stops the retry step spending the rest of the month · no
retry, backoff or fallback inside any backend; that ladder is the tool's · `academic` is **one**
backend trying OpenAlex → Crossref → arXiv in order, first answer wins, so a normal search is
one HTTP call; a provider that fails is stepped over and only a clean sweep raises · it
accepts and ignores `source_type` (every provider is already scholarly) · OpenAlex abstracts
are rebuilt from `abstract_inverted_index`, or the model would choose sources by title alone ·
`ddgs` is deliberately unimplemented — `build_search_backend` refuses it rather than resolving
to something that merely looks right.

**Provides (the tool, done):** `WebSearchTool` in `tools/web_search.py`
(`WebSearchInput{query 3–200 chars, source_type=general, max_results=6}` →
`WebSearchOutput{results: list[NormalizedSource]}`), constructed as
`WebSearchTool(settings=None, *, backend=None, connection=None)` — the same injection seams
`fetch_url` takes. Also the private `_select_backend(source_type, configured)` and
`_widening(source_type)`. No new config value, no new `ErrorCode`, no new dependency; the
only other edit was widening `pyproject.toml`'s `live` marker description to name a metered
search backend and the network.

Flow: `_select_backend` → `get_cached_search` → (miss) `consumes_quota` /
`reserve_search_call` → `backend.search` → `store_cached_search` → `normalize_sources`.
A hit and a fresh search end in the same shaping function, so they return identical output.

**Tool decisions:** **selection is a pure function and the factory is unchanged** —
`fixture` resolves to `fixture` for *every* source type (an offline run must be provably
offline, and a routing rule with an exception is not provable); otherwise `academic` intent
goes to the free `academic` backend, and everything else uses the configured backend
untouched, so a query is never redirected *onto* a metered backend the user did not choose ·
the backend name is resolved **before** the cache read, because it is part of the key — a
recording must never answer a `serpapi` query · **each live attempt reserves its own quota**
(the ladder is up to three searches at the provider, and there is no refund path) · a budget
ledger that cannot be read **refuses** the live call as `SEARCH_UNAVAILABLE`, not
`MONTHLY_BUDGET_EXCEEDED`, which would claim to know something unreadable — the one place a
`sqlite3.Error` is not degraded to "carry on"; cache read/write failures *are* degraded, and
logged, exactly as in S6 · a backend is built once per name and reused, so the fixture index
is read once · the retry fires only on the first rung and only for `retryable=True`; the
fallback to `general` is one further attempt, and the error reported is the **first** failure
because it describes the search actually asked for · **fallback results are cached under the
source type that answered** (`general`), never the one requested — no row may claim a search
returned what it did not, at the cost of re-running the ladder on a repeat · results are
trimmed to `max_results` **after** normalisation, since dedup can only shrink the list ·
`WebSearchOutput.results` is S4's `NormalizedSource`, not a second result model.

**Still to do (S8, not S7):** the optional `ddgs` backend · **real** recorded responses in
`fixtures/search/` — the seed set is complete but every file is still `handwritten` · a
`@pytest.mark.live` acceptance suite: one real SerpAPI query, recorded into
`fixtures/search/` in the same session. The design already supports it — the tool takes real
`Settings` and a real connection, and the marker keeps it out of the default run and
`.githooks/pre-push`.

**Contract decisions (settled):** a backend returns **`RawSource`** — S4's existing model, not a
new `SearchResult` — because `normalize_sources` takes exactly that, so ranking, dedup and
authority stay out of every provider · a backend **raises `SearchBackendError`** rather than
returning a `ToolResult`, the same split as `LLMError` and its providers: the failure ladder is
the tool's control flow, and an empty list must stay distinguishable from a broken backend (a
query that found nothing succeeded, and is not retried) · `retryable=False` marks what a second
identical call cannot fix — an exhausted quota, a rejected key — and is what keeps the retry step
from spending the rest of the budget · `source_type` and `max_results` are keyword-only and have
**no defaults in the protocol**, so the defaults live once, in `WebSearchInput` · `base.py`
imports only `typing` and `RawSource`: no config, no httpx, no sqlite3, no registry ·
`domains.json` (S4) is the ranking map — S7 never adds a `domains.yaml`.

**Contracts:** the search-cache read happens **before** the quota check, so a cached query costs
neither network nor quota · past `MONTHLY_SEARCH_BUDGET` a live call is refused with
`MONTHLY_BUDGET_EXCEEDED` · failure ladder as built: cache → one retry with 2 s backoff → fall
back to `source_type="general"` → `ToolError(SEARCH_UNAVAILABLE)`, never an exception (the
plan's optional `ddgs` rung is absent because the backend is unimplemented) · results pass
through S4 so official docs re-rank above blogs · switching backend is a `.env` change and
nothing else.

**Must not use or change:** `SEARCH_BACKEND=fixture` stays the committed default in `.env.example`
and in `Settings` · never change it to make a test pass · never loop, retry-storm or sweep queries
against a live backend · the tool's input schema is expensive to change (a future prompt depends
on it); its backend is free to change.

---

### S9 — Final tool registry wiring · **complete**

*Inspect first:* `tools/wiring.py`
*Only if needed:* `tests/unit/test_tool_wiring.py`, `tools/registry.py`

**Provides:** `build_tool_registry(settings=None, *, connection=None) -> ToolRegistry` and
`TOOL_NAMES` in `tools/wiring.py` — the composition root where the finished tools meet the
registry. Registers all four: `fetch_url`, `normalize_sources`, `read_document`, `web_search`.
`settings` and `connection` pass straight through to the two cache-using tools; given no
connection they keep opening their own per call, exactly as before. No new dependency, no
config value, no change to any tool or to the registry.

**Decisions:** **a fresh registry per call, no module-level singleton** — duplicate names raise
at wiring time by design, so a shared registry would turn a second build into a spurious
`ValueError` and leak one caller's tools into another's · **not re-exported from
`tools/__init__.py`**, which imports only `base` and `registry`: re-exporting would pull
`httpx`, `sqlite3`, `pypdf` and the search backends into every import of `RunContext`, and
would close an import cycle · `TOOL_NAMES` is **declared, not derived** — a finished tool that
is never wired, or a name that drifts from the tool's own `name`, is then a failing assertion
rather than a capability the agent silently lost · `normalize_sources` is registered like the
rest so a trace shows what normalisation discarded; which subset is *advertised* to a model is
the tool-spec layer's call (Day 3), not the registry's · the hook lists are left untouched —
installing one here would move a concern into the composition root, and they belong to the
tracing capability.

**Known limitation:** `read_document` takes no `settings`, so the factory's `settings`
argument does not reach it; it resolves `ALLOWED_ATTACHMENT_DIR` through `get_settings()` when
it reads. Widening its constructor would change a finished S3 contract for one caller — do
that only if a real caller needs a per-run attachment directory.

**Must not use or change:** no DI container, no entry-point/plugin discovery, no auto-import
scanning of `tools/` — the menu is explicit and reviewable · no provider, cache, quota or
parsing logic in the factory · the factory does not install hooks · S8's CLI calls
`build_tool_registry`, it does not construct tools.

---

### S8 — Tools CLI and recorded fixtures · **complete**

*Inspect first:* `tools/cli.py`, `fixtures/README.md`
*Only if needed:* `main.py` (the CLI conventions it mirrors), `search/fixture.py`,
`tests/unit/test_tool_wiring.py`, `README.md` (the command table to update)

#### Fixtures and offline replay data · **complete**

**Provides:** the `fixtures/` tree — no source file changed, no dependency, no config value.

```
fixtures/
├── README.md      layout, provenance policy, the format, how to add a recording
├── search/        5 recordings: postgresql-indexing (docs), explain-plans (general),
│                  learned-indexes (academic), asyncio-cancellation (technical),
│                  no-results (general, empty)
├── documents/     indexing-brief.md (headings + fenced code), session-notes.txt (no headings)
└── html/          article.html — a page with nav, header, aside, form, footer and <pre><code>
```

All four `source_type` values are covered, so `SEARCH_BACKEND=fixture` answers each of them out
of the box; `ALLOWED_ATTACHMENT_DIR` already defaults to `fixtures/`, so `read_document` opens
`documents/…` with no configuration.

**Decisions:** **provenance is file-level, never per result** — `RawSource` is `extra="forbid"`,
so `recorded_from` (`handwritten` | `serpapi` | `academic`) lives on the recording and
`source_backend` stays `"fixture"`, which is where *this run's* source came from · everything
committed today is `handwritten`: right shape, not knowledge — **no report may cite one**, and
they are replaced file-by-file at the live-acceptance step · **no committed binaries** — a PDF or
DOCX is neither human-readable nor diff-stable, and both suites already build them in memory
(`make_pdf` in `conftest.py`, `build_docx` in `test_read_document.py`); generate into a temp
directory instead · one file per `(query, source_type)`, no payload repeated in a second format ·
`explain-plans` is recorded under `general` **only**, so asking for it as `docs` exercises
`web_search`'s fallback rung offline at no extra file · `no-results.json` records the one outcome
nothing else can show offline — a query that found nothing, replayed as an empty **success** ·
`explain-plans` is ordered worst-authority-first on purpose, so S4's re-ranking has work to do ·
`html/article.html` is the page S6's *"revisit `extract_html` when S8's fixtures show the output
is too noisy"* note was waiting for.

**Tests (3, all extending existing suites, no new file):** the committed search set is
parameterized over every file in `test_search_backends.py` (the index is built either way, so a
duplicate or unreadable recording fails there whichever file is being replayed), plus one test
that the default `general` type still answers · `test_read_document.py` opens both committed
attachments under the default directory and checks the outline is present for the Markdown and
absent for the text · `test_fetch_url.py` gains the first **direct** `extract_html` test, against
`article.html`. Not re-tested, because `tmp_path` already covers them: missing/malformed/duplicate
failure contracts, key normalisation, `max_results` truncation, backend stamping.

**Must not change:** the self-describing format or the `(query, source_type)` key —
`search/fixture.py` owns both · never hand-edit a recording to make a test pass · a live capture
is written into `fixtures/search/` in the **same session** as the call.

#### Tools CLI · **complete**

**Provides:** `src/evergrove_agent/tools/cli.py` — `python -m evergrove_agent.tools.cli` with
four subcommands, one per registered tool, no model anywhere:

```
search  QUERY  [--type docs|technical|academic|general] [--max-results N] [--backend NAME]
fetch   URL    [--excerpt-for QUESTION] [--max-chars N]
read    PATH   [--mode full|outline|section] [--section HINT]
normalize URL [URL ...]                                        every command also takes --json
```

Public shape: `build_parser()`, `async run(args, *, settings=None) -> int`, `main(argv=None)`.
Private: `_build_call` (the only place the commands exist), `_report`, `_RENDERERS`. No new
dependency, no config value, no `[project.scripts]` entry, and `main.py` untouched.

Flow: parse → `_build_call` → `build_tool_registry(settings)` → `registry.call(name, args,
RunContext())` → format. Given no `connection`, each tool opens its own per call — one command
is one call.

**Decisions:** every flag maps 1:1 onto a field that already exists on a tool's input model, and
**the CLI re-checks none of their bounds** — the args dict goes to the registry, which validates
and answers `BAD_ARGUMENTS`; a copy of the limits in argparse is a second place for them to
drift · an option left unset is **dropped, not passed as `None`**, so every default stays on the
input model (and `--help` reads the defaults off `model_fields`, so the help text cannot drift
either) · `choices` come from `get_args` on the existing literals (`SearchSourceType`,
`SearchBackendName`, `ReadMode`), so a new enum value is not also a CLI edit · **`--backend` is
the one flag that is not a tool argument**: it is `settings.model_copy(update={"search_backend":
…})` passed to the factory — config injection, not routing, and the process-wide settings object
is left unchanged. `ddgs` stays in `choices`; the factory refuses it and the registry converts
that raise into a structured failure · **`normalize` was added as a fourth command** so every
registered tool has a direct CLI path · human-readable output by default (labelled header lines,
then the raw body text under a `---` rule; listings show authority, domain, title, URL, snippet),
`--json` prints `ToolResult.model_dump_json` — one renderer per tool in `_RENDERERS`, so a new
tool is a table entry rather than another branch.

**Exit codes — supersedes this section's earlier note:** `0` the tool succeeded · `1` the tool
returned a `ToolError` (its code and message on stderr, never a traceback — `ToolRegistry.call`
does not raise) · `2` unusable arguments, argparse's own. The earlier line here said a failure
"exits 0"; that was changed on the user's call so a shell can tell a refusal from an answer, and
because `main.py` already means 0/1/2.

**Tests (4, extending `test_tool_wiring.py` — the CLI is nothing but wiring):** flag-to-field
mapping parameterized over all four commands, each dict also `model_validate`d against the tool's
own input model (catches a `dest` that drifted from its field, which breaks nothing at import
time) · `search --backend fixture` end to end against the committed recordings with settings that
say `serpapi` and `respx` holding no routes, so an override that silently did nothing would fail
rather than bill · a missing file prints `NOT_FOUND` and exits 1 with no traceback in either
stream · `--json` output parses back as a real `ToolResult`. `fetch` is covered by the mapping
test only — `test_fetch_url.py` already owns its behaviour and an end-to-end case would need
`respx` for nothing new.

**Must not use or change:** the CLI stays argument parsing + one registry call + formatting —
no tool logic, no backend/cache/quota/normalisation decision, no agent, no model · never
construct a tool directly; always `build_tool_registry` · do not touch `main.py`'s existing flags
or the report path.

---

### S10 — Offline integration and acceptance · **complete**

*Inspect first:* `tests/integration/test_day2_acceptance.py`
*Only if needed:* `search/normalize.py` (`_canonical_host`), `tools/wiring.py`

**Provides:** `tests/integration/` — the first suite that is not about one unit. Seven tests
(15 cases) over the assembled stack: `build_tool_registry`, one SQLite file, the committed
`fixtures/` tree, and **no injected connection**, so every call opens and closes its own the
way the CLI leaves it. No source file changed except the one fix below; no dependency, no
config value, no fixture added.

What each test protects, in the order it would cost most to have wrong:

- **the whole pass** — search → normalize → fetch → read through one registry, with one
  `respx` route standing in for the top result's server. Catches tools that no longer fit
  together (a `web_search` result `fetch_url` cannot be handed), the fixture tree moving out
  from under the committed defaults, and ranking that stops putting official docs first once
  a *real recording* rather than a stub goes through it.
- **both caches, across per-call connections** — miss → work → hit for the source cache and
  the search cache. The unit suites seed one side and read the other on a connection held
  open for the test; only this can catch a write that never reaches the file, or a read keyed
  differently from the write.
- **the metered repeat** — `serpapi` with a mocked server: the first search moves the ledger
  to 1, the repeat is a cache hit and leaves it at 1. `fixture` can never move the counter,
  so this is the only place "a cache hit costs no quota" is actually proven.
- **every committed recording, through the registry, with no routes registered** — all four
  source types plus the empty one, each ending with `get_search_usage == 0`.
- **an unrecorded query** — the only path where a fall-through could happen, since a recorded
  one answers from disk whatever the code does. Must be `SEARCH_UNAVAILABLE`, `retryable=False`.
- **four failure layers through the CLI** — registry validation, a tool guard, the attachment
  containment check, an HTTP status: each is its code on stderr, exit 1, no traceback.
- **argparse's exit 2**, kept distinct from a tool's exit 1.

**Fixed here (one production defect):** `canonicalize_url` accepted a phrase. The scheme-less
branch turned `not a url` into `https://not a url/` — a host no resolver can answer for — so
`fetch_url` spent two attempts on it and reported `FETCH_FAILED  retryable=true` instead of
`BAD_ARGUMENTS`, and `normalize_sources` kept it as a source instead of counting it in
`dropped`. `_canonical_host` now returns `None` for a host containing whitespace; anything
else non-ASCII is still accepted, because an IDN host is legitimate. Covered by one added case
in `test_canonicalize_url`'s existing table, not a new test.

**Observed, not changed:** on a fresh fetch, `FetchUrlOutput.retrieved_at` is taken a beat
*after* the cache row's `fetched_at`, so a hit reports a timestamp ~1 ms earlier than the call
that filled it. Harmless — the contract is only that a hit reports the fetch, never the serve
— so the test asserts `<=` rather than equality. Passing the stored `fetched_at` back through
`_success` would be a change to a finished S6 contract for a cosmetic gain.

**Decisions:** integration tests live in `tests/integration/`, separate from `tests/unit/` —
same offline rules, same default run, but a failure there means the *composition* broke, not a
unit · they use the **committed** `fixtures/` tree and default settings, because a fresh clone
answering with no configuration is part of what is accepted; only `DB_PATH` is redirected to
`tmp_path`, never the real cache or ledger · `read_document` still needs
`documents.reader.get_settings` patched (the S9 limitation) · nothing here re-proves a unit:
each test names the composition failure it catches.

**Must not change:** no test in this directory may reach the network, spend quota, or need a
model — `@respx.mock` with no routes is the default posture, and a route is registered only to
stand in for a specific server · a live acceptance run (one real SerpAPI query, recorded into
`fixtures/search/` in the same session) is still **outstanding** and belongs behind
`@pytest.mark.live`, alongside the other S7 live work.

---

## Decisions already made (do not relitigate)

1. **One path to every tool.** Nothing calls a tool directly; everything goes through
   `ToolRegistry.call`.
2. **Tools never raise.** A failure is a `ToolResult` carrying a `ToolError` with a specific
   `ErrorCode`.
3. **Few tools, enum-routed.** One `web_search` with `source_type`, one `read_document` with
   `mode` — a small local model's tool selection degrades as the menu grows. The routing behind
   the enum is deterministic Python.
4. **Hook points now, hooks later.** The lists stay empty in this capability.
5. **`schemas/` imports nothing** from the package; everything imports it. Keep it that way.
6. **Deterministic first.** No model, embeddings or vector store anywhere in this capability.
7. **Storage is one SQLite file, stdlib `sqlite3`, no ORM.**
8. **`fixture` is the default search backend**, which is what makes the rest of the build free.
9. **Config is one file.** New budgets, TTLs and paths go in `config.py` and `.env.example` —
   never inline.

## Testing and resource rules for this capability

- Offline by default: `FakeProvider`, `respx` for HTTP, the fixture backend, recorded fixtures,
  and `Settings(_env_file=None)` (the `settings` fixture in `tests/conftest.py`).
- Focused runs during implementation: `uv run pytest tests/unit/test_x.py::test_y`. The full
  offline suite (`uv run pytest`, ~1.2 s) at a subtask boundary, or after touching a shared
  contract (`config.py`, `schemas/`, `tools/base.py`, `tools/registry.py`).
- Stop at the cheapest level that proves the behaviour: inspection → focused unit test → mocks and
  fixtures → offline integration → live call. Reaching a live call without the levels below it
  passing is a rule violation.
- Before any live SerpAPI, Gemini, Ollama or HTTP call, state the specific uncertainty it resolves
  and why offline cannot resolve it. **Every successful live search response is recorded into
  `fixtures/search/` in the same session** — an uncaptured live search burns quota twice.
- SerpAPI: 250/month total, `MONTHLY_SEARCH_BUDGET=200`. Never repeat an identical live query;
  check the cache and `fixtures/search/` first.
- A live failure is debugged offline (`respx`), then retried **once**.
- Anything needing a real model, key or network carries `@pytest.mark.live`, which keeps it out of
  the default run and out of the pre-push gate.
- `.githooks/pre-push` runs `ruff check` then `pytest -q`. Never add a check to it that needs a
  model, a key or the network, and never add a marker override.
- Test value over count: each new test names the bug or regression it catches; parameterize
  variations instead of copying them.

## Keeping this file current

After finishing a subtask, update **its section only**: flip the status, and add the files
changed, the public interface it now provides, and any decision that constrains later subtasks.
Do not paste implementation detail that already lives in the code, and do not let a section grow
past a screen.
