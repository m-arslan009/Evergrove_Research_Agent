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
| `src/evergrove_agent/documents/` | `excerpt.py` today; `pdf.py`, `text.py`, `html.py` land with the readers |
| `src/evergrove_agent/search/` | *not created yet* — `base.py`, `serpapi.py`, `academic.py`, `fixture.py`, `domains.yaml` |
| `src/evergrove_agent/memory/` | *not created yet* — `db.py`, `cache.py`, `search_cache.py`, `budget.py` |
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

### S3 — Document readers and `read_document` · **pending**

*Inspect first:* `tools/base.py`, `schemas/tools.py`, `documents/__init__.py`
*Only if needed:* `tools/registry.py` (dispatch expectations), `config.py`
(`allowed_attachment_dir`), `tests/unit/test_excerpt.py` (fixture style)

**Expected output:** `documents/text.py` + `documents/pdf.py`, and one `read_document` tool with
`ReadDocumentInput{path, mode: outline|full|section, section_hint?}` →
`ReadDocumentOutput{path, file_type, page_count, outline, text, truncated}`.

**Contracts:** `.txt`/`.md`/`.pdf` only · `path` must resolve inside `ALLOWED_ATTACHMENT_DIR`,
else `PATH_NOT_ALLOWED` · every failure returns its own existing `ErrorCode`
(`CORRUPT_PDF`, `ENCRYPTED_PDF`, `NO_TEXT_LAYER`, `EMPTY_FILE`, `UNSUPPORTED_TYPE`, `NOT_FOUND`) ·
never raise · `full`/`section` end in `select_passages` · no retry — reads are deterministic.

**Must not use or change:** no OCR · no new `ErrorCode` unless a failure genuinely has none · one
tool with a `mode` enum, not three tools · `pypdf` is the PDF dependency named by the plan.

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

### S5 — SQLite and the source cache · **partially complete** (base layer done, cache pending)

*Inspect first:* `memory/db.py`, `config.py` (`db_path`, `cache_ttl_days`)
*Only if needed:* `tests/unit/test_db.py`, `tests/conftest.py`

**Provides (base layer, done):** re-exported from `memory/__init__.py` —
`connect(db_path=None)`, `initialize_schema(conn)`, `transaction(conn)` (a context manager
yielding a cursor), `open_database(db_path=None)` (connect → initialise → close), plus
`SCHEMA_STATEMENTS` and `SCHEMA_VERSION`. `connect` falls back to `DB_PATH` from config, creates
the parent directory, sets `row_factory = sqlite3.Row` and the pragmas `foreign_keys=ON`,
`journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`. The only table so far is
`schema_meta(key, value)`, holding `schema_version`.

**Still to do:** the `source_cache` table and its read/write helpers, with `CACHE_TTL_DAYS`
expiry and a `from_cache=true`-reportable hit. Nothing wires it into a hook yet.

**Decisions:** **`db.py` owns all DDL**, in `SCHEMA_STATEMENTS`; feature modules own only their
queries — `cache.py` needs `connect`, so `db.py` importing it back would be a cycle and creation
order would follow import order. Adding a table later is appending an `IF NOT EXISTS` statement
to that tuple; the layer itself does not change · `initialize_schema` is idempotent and
non-destructive, cheap enough to run on every open, which is why `open_database` does · writes
that span statements go through `transaction`, not `with connection:` — the latter does not wrap
DDL and leaves the cursor to the caller · no migration runner: `SCHEMA_VERSION` is a marker for
whoever first needs to change an existing table's shape.

**Must not use or change:** no Redis, no ORM, no second database file · `db.py` stays free of
tools, models, HTTP and search backends (stdlib + `config` only) · tests must not write to the
real `DB_PATH` — point it at a temporary path.

---

### S6 — `fetch_url` · **pending**

*Inspect first:* `tools/base.py`, `schemas/tools.py`, `memory/db.py` (from S5),
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

### S7 — `web_search`, backends, search cache and quota guard · **pending**

*Inspect first:* `tools/base.py`, `schemas/tools.py`, `config.py` (search block),
`memory/db.py` (from S5)
*Only if needed:* the normalisation tool from S4, `.env.example`

**Expected output:** `WebSearchInput{query 3–200 chars, source_type: docs|technical|academic|general,
max_results=6}` → `WebSearchOutput{results: [{title, url, snippet, source_backend, domain_class}]}`;
a `SearchBackend` protocol with `fixture`, `serpapi`, `academic` (OpenAlex/Crossref/arXiv) and
optional `ddgs` implementations; `search_cache` and `search_budget` tables with the monthly guard;
`search/domains.yaml`; recorded responses in `fixtures/search/`.

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
