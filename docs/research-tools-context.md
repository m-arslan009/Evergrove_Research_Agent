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
| `src/evergrove_agent/documents/` | `excerpt.py`, `base.py`, `reader.py` and the format readers `text.py`/`pdf.py`/`docx.py`; `html.py` lands with `fetch_url` |
| `src/evergrove_agent/search/` | `domains.json`/`domains.py` and `normalize.py` (S4), `base.py` (the backend contract); `fixture.py`, `serpapi.py`, `academic.py` land with S7 |
| `src/evergrove_agent/memory/` | `db.py` (all DDL), `cache.py`, `search_cache.py`, `budget.py` |
| `src/evergrove_agent/schemas/tools.py` | `ToolResult`, `ToolError`, `ErrorCode` — the envelope every tool returns |
| `src/evergrove_agent/config.py` | every budget, path, TTL and backend switch |
| `tests/unit/`, `tests/conftest.py` | offline suites; `settings` fixture is `Settings(_env_file=None)` |
| `fixtures/` | *not created yet* — recorded search JSON, HTML, PDFs, text |
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
S1 ──► S4 normalize_sources ──┬──────┼─► S8 tools CLI + fixtures
config ─► S5 sqlite cache ────┴─► S6 fetch_url ─┤
                              └─► S7 web_search ┘
```

`S3`–`S5` are independent of each other and may be done in any order. `S6` needs `S3`'s PDF
reader, `S4` and `S5`. `S7` needs `S4` and `S5`. `S8` is last.

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
specific tool.

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

### S6 — `fetch_url` · **pending**

*Inspect first:* `tools/base.py`, `schemas/tools.py`, `memory/cache.py` (from S5),
`documents/__init__.py`
*Only if needed:* `documents/pdf.py` (from S3), `tests/unit/test_llm_provider.py` (the `respx`
pattern), `config.py` (`max_fetch_calls`)

**Expected output:** `FetchUrlInput{url, max_chars=20000, excerpt_for?}` →
`FetchUrlOutput{url, final_url, title, text, char_count, truncated, from_cache, retrieved_at}`.

**Contracts:** `httpx` with a timeout and exactly one retry on timeout/5xx · a PDF content-type
routes to the S3 PDF reader automatically · the cache is checked before the network · empty
extraction is a `ToolError` the agent can act on, not an exception · `excerpt_for` runs
`select_passages` · `trafilatura` is the extraction dependency named by the plan.

**Must not use or change:** no headless browser, no JS rendering · no crawling or link-following —
one URL per call · never bypass the registry.

---

### S7 — `web_search`, backends, search cache and quota guard · **partially complete** (backend contract, search cache and quota guard done; backends and the tool pending)

*Inspect first:* `search/base.py`, `tools/base.py`, `schemas/tools.py`, `config.py` (search
block), `memory/search_cache.py`, `memory/budget.py`
*Only if needed:* the normalisation tool from S4, `.env.example`,
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

**Still to do:** the `fixture`, `serpapi`, `academic` (OpenAlex/Crossref/arXiv) and optional
`ddgs` implementations · the `WebSearchInput`/`WebSearchOutput` models and the `web_search`
tool · the name→backend resolution from `SEARCH_BACKEND` · wiring the cache and the guard
into the tool in that order · recorded responses in `fixtures/search/`.

**Expected output:** `WebSearchInput{query 3–200 chars, source_type: SearchSourceType,
max_results=6}` → `WebSearchOutput{results: [{title, url, snippet, source_backend, domain_class}]}`.

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
`MONTHLY_BUDGET_EXCEEDED` · failure ladder: cache → one retry with 2 s backoff → fall back to
`source_type="general"` → optional `ddgs` → `ToolError(SEARCH_UNAVAILABLE)` with an empty list,
never an exception · results pass through S4 so official docs re-rank above blogs · switching
backend is a `.env` change and nothing else.

**Must not use or change:** `SEARCH_BACKEND=fixture` stays the committed default in `.env.example`
and in `Settings` · never change it to make a test pass · never loop, retry-storm or sweep queries
against a live backend · the tool's input schema is expensive to change (a future prompt depends
on it); its backend is free to change.

---

### S8 — Tools CLI and recorded fixtures · **pending**

*Inspect first:* `main.py` (the existing CLI conventions only), the finished tools
*Only if needed:* `README.md` (the command table to update)

**Expected output:** `python -m evergrove_agent.tools.cli` with `search` / `fetch` / `read`
subcommands, exercising all four agent-callable tools with no model involved; a failure prints its
error code and exits 0. Fixtures recorded for HTML, PDF, text and search.

**Contracts:** the CLI is a thin surface over the registry — no tool logic in it.

**Must not use or change:** do not touch `main.py`'s existing flags or the report path.

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
