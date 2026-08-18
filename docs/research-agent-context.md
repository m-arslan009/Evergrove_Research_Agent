# Evergrove Research Agent — implementation context

The primary context for AI development sessions on this project. Read it before implementing
anything.

It describes **what exists in the repository today**, the contracts that must not be broken,
the decisions that must not be accidentally reversed, and what the next milestone depends on.
It is a technical context document, not a build diary: completed work is summarised, and
implementation detail is kept only where losing it would let a later session make a wrong
architectural decision.

**The repository is the ultimate evidence.** Where this document and the code disagree, the
code is right — inspect it, then correct this file. Where this document and the 7-day plan
(`Evergrove_Research_Agent_Architecture_and_7_Day_Plan.NEW.docx`) disagree, the repository has
usually deviated on purpose; see *Verified deviations from the plan* before "restoring"
anything.

---

## Status

| | |
| --- | --- |
| **Completed** | **Day 1**, **Day 2** |
| **Current milestone** | **Day 5 — Supervisor + Researcher + Appraiser** — **T1–T6 done**: `single_agent.py`'s four stage functions now live in `supervisor.py` / `researcher.py` / `appraiser.py` over a shared `runtime.py`, `service.py` takes a `mode` (`multi` default, `single` retained), each role calls the provider its configuration names, the Appraiser returns a per-source reading of the evidence rather than a sufficiency flag, and the trace now carries the topology as `agent` spans. 577 offline tests pass |
| **Day 4** | **T1–T6 done**: every tool call is traced, logged as one JSON line and budget-checked by the registry, both memories exist, a recalled preparation steers the planner and the report, and `scripts/show_trace.py` renders a run as a tree. **The Day 4 acceptance run is still owed** — no live run has yet exercised either memory or been read back through the renderer |
| **Day 3** | S1–S14 implemented and live-verified. **Implementation complete; sign-off deferred to Day 7** — two acceptance runs are owed and banked there, see *S14 results* |
| **Completed Day 3 subtasks** | **S1 — Agent schemas** · **S2 — Model-facing tool integration** · **S3 — Agent prompts and assembly** · **S4 — In-memory budget counters on `RunContext`** · **S5–S8 — the orchestration loop** · **S9 — `validate_report` and grounding** · **S10 — structured finalisation and the retry ladder** · **S11 — `service.py`, the one entry point** · **S12 — CLI integration** · **S13 — offline integration tests** · **S14 — live end-to-end verification (run on 3 tasks, not the specified 5)** |
| **Completed Day 4 subtasks** | **T1 — Tracing foundation**: the span stack on `RunContext`, the `runs`/`spans` tables, `tracing/store.py`, `tracing/tracer.py` · **T2 — Registry hook chains**: `tools/hooks.py`, one `tool` span per call, `service.py` owns the run's connection and writes the run header · **T3 — Budget enforcement in the pre-hook**: `_TOOL_BUDGET`/`_claim_for_tool` lifted out of `single_agent.py` · **T4 — Persistent and session memory**: `prep_memory`/`run_memory` tables, `memory/prep_memory.py`, `memory/run_memory.py`, `tools/memory_tools.py` (three registered, never-advertised tools), `PreviousPreparation`, and the two best-effort write calls in `run_agent` · **T5 — Memory-aware agent integration**: `RunState.previous`, the single `recall_previous_preparation` call in `run_agent`, `render_previous_preparation` → `plan.md`'s new `{previous_preparation}` placeholder, and `render_continuation_note` → an extra `finalise()` message |
| **Completed Day 5 subtasks** | **T1 — the split, and the mode switch**: `agents/runtime.py` (shared plumbing + the orchestration mechanics both loops use), `agents/supervisor.py` (`decide_next_step`, `finalise`, `run_supervised`, `_delegate_hop`), `agents/researcher.py` (`run_research_step`), `agents/appraiser.py` (`judge_sufficiency`); `single_agent.py` keeps `run_agent` and re-exports the moved names; `config.AgentMode`, `service.prepare_focus_session(mode=…)`, `main.py --mode`; `tests/integration/test_multi_agent.py` · **T2 — typed inter-agent messages**: the five message models were found already defined *and already used at every boundary* (S1 built them, T1 wired them), so T2 added no new model and changed no signature. Its real content is `AppraisalVerdict`'s semantic judgement — `accepted[]`, `rejected[]`, `disagreements[]`, all defaulted — wired into `runtime._appraisal_line` (the `run_memory` row) and `prompt_context.render_research_context` (`finalise`'s prompt), plus `sufficiency.md`'s three new rules and 29 tests pinning the contracts · **T3 — per-role provider selection**: the wiring was found **already complete** — the three `*_PROVIDER` settings, `build_provider`, `AgentProviders.from_settings` and the `providers.<role>` argument at every stage were built by Day 1 and Day 5 T1 — so T3 added no mechanism. Its content is the proof and one guard: `build_provider` now **raises** on an unrecognised name instead of falling through to local, and a wire-level test drives three provider combinations (all-local, hosted Appraiser, local Researcher only) through `prepare_focus_session` with **nothing injected**, asserting from the HTTP endpoints that each role's calls reached the provider its configuration names · **T4 — Appraiser judgement quality**: `AcceptedSource` / `RejectedSource`, the rewritten `sufficiency.md`, the per-source rendering into the finalise prompt and the `run_memory` row, `finalise.md`'s do-not-cite-a-rejected-source rule, and `tests/unit/test_appraiser.py`. The rejection is guidance to the report, never a narrowed grounding set |
| **Next task** | **Day 6 — MCP server and client, hardening.** Day 5 is implementation-complete. Live runs remain Day 7's by standing decision (*Engineering decisions* 12) — three are banked there |

`schemas/agents.py` is the contract every later Day 3 subtask builds against,
`agents/tool_calling.py` is the only bridge between a model and the tool registry,
`agents/prompt_context.py` is the only place a run's state becomes prompt text,
`RunContext.budget` is the only ledger of what a run has spent, and
**there are now two loops that drive the four stages** — `agents/supervisor.py`'s
`run_supervised` (multi, the default) and `agents/single_agent.py`'s `run_agent` (single,
retained as a deliverable), chosen by `service.prepare_focus_session(mode=…)` and identical in
behaviour by design, and
`tools/validate_report.py` is the only place a finished report is checked against the evidence,
and `finalise()` is the only caller of it — up to `MAX_OUTPUT_RETRIES` **total attempts**, each
one validated, the errors quoted back, the last attempt on the alternate provider when one is
configured, then `PreparationFailed`. `service.py` is the only place a surface starts a run from,
and `main.py` is now a surface rather than a second composition root. `agents/` holds exactly
eight modules — `__init__`, `tool_calling`, `prompt_context`, `runtime`, `supervisor`,
`researcher`, `appraiser`, `single_agent`. Do not describe or assume any other capability as
present.

| Day | Area | Status |
| --- | --- | --- |
| 1 | Project, config, schemas, `LLMProvider` + three providers, first structured round trip | **Done** |
| 2 | Deterministic tools: registry, search, fetch, document readers, SQLite caches, fixtures, tools CLI | **Done** |
| 3 | Single research agent — the core loop | **Implementation complete and live-verified; 2 acceptance runs banked for Day 7** |
| 4 | Memory, hooks, tracing | **Implementation complete — T1–T6 built and covered offline; acceptance run banked for Day 7** |
| 5 | Supervisor + Researcher + Appraiser | **T1–T6 done — the split, the `--mode` switch, the typed message contracts, per-role provider selection, the Appraiser's per-source judgement and the evidence-driven multi-hop decision, and the cross-agent `agent` spans. Implementation complete** |
| 6 | MCP server and client, hardening | Not started |
| 7 | Tests, five evaluations, requirement audit, final demo | Not started |

---

## Project purpose

A standalone Python service that prepares a focus session *before* the Evergrove timer starts.
Input: a task title, a session length, optionally a description and a local attachment. Output:
one validated, source-grounded `FocusPreparationReport` — a narrowed objective, session-sized
topics, real sources that were actually read, what to skip, one practice exercise, one success
criterion, plus `assumptions` and `unknowns`.

It is a **separate project in its own repository**. It does not touch the Evergrove frontend,
backend, database or `CONTRACT.md`. Integration is Phase 3 and out of scope.

Three decisions genuinely need a model — how big a slice fits one session, whether the sources
found support it, and what to search for next when they do not. **Everything else is
deterministic Python and is written as such.**

## Architecture

One process, a modular monolith layered internally. "Multi-agent" describes how the reasoning
is organised, not how many services are deployed.

```
                    CLI  ·  MCP server            ← surfaces   (CLI: BUILT · MCP: Day 6)
                          │
                     service.py                   ← BUILT (S11). Composition only:
                          │                          registry + providers + RunContext,
                          │                          and `mode` picks one of two loops
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
   supervisor.run_supervised          single_agent.run_agent
   (multi · default)                  (single · retained demo)
        │                                   │
   Supervisor ──► Researcher              one agent runs
        └──────► Appraiser                all four stages
                          │              ← BUILT (Day 5 T1). Same four stage functions,
                          │                 same tools/budgets/memory/validation; only
                          │                 the topology differs
              agents/tool_calling.py               ← BUILT (S2). Advertise → dispatch;
                          │                          the only way a model reaches a tool
                   tool registry                  ← BUILT. The only path to a tool.
                          │                          Hooks installed: span + budget (T2/T3)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   search backends   documents/         SQLite          ← all BUILT
   (serpapi ·        (pdf · docx ·      (source cache ·
    academic ·        html · text ·      search cache ·
    fixture)          excerpt)           search budget ·
                                         runs · spans ·
                                         prep_memory ·
                                         run_memory)
        │                 │                 │
        └──────── schemas/ (Pydantic) ──────┘      ← imports nothing from the package;
                          │                           everything imports it
                       config.py
```

| Decision | Choice |
| --- | --- |
| Language / tooling | Python 3.12, `uv`, `ruff` (no type checker) |
| Orchestration | Plain Python state machine — no LangGraph, no LangChain |
| Model runtime | Ollama local (`qwen3:4b`), Google AI Studio (Gemini) as opt-in second provider |
| Web search | SerpAPI free tier + keyless OpenAlex / Crossref / arXiv, `fixture` by default |
| Storage | One SQLite file, stdlib `sqlite3`, no ORM |
| Structured output | Pydantic → JSON Schema → constrained decoding → re-validated |
| Cost | $0 mandatory. Local path needs no key; both free tiers need no card |

## Repository map

| Path | Contents |
| --- | --- |
| `src/evergrove_agent/schemas/` | `task.py`, `report.py`, `tools.py`, `agents.py` — Pydantic only, imports nothing from the package |
| `src/evergrove_agent/config.py` | Every tunable value: models, budgets, TTLs, timeouts, paths |
| `src/evergrove_agent/llm/` | `base.py` (contract), `ollama_provider.py`, `hosted_provider.py`, `fake_provider.py`, `prompts/` (`__init__.py` loader + `plan.md`, `research_step.md`, `sufficiency.md`, `finalise.md`) |
| `src/evergrove_agent/agents/` | `tool_calling.py` — the model ↔ tool bridge (S2); `prompt_context.py` — the placeholder renderers (S3); `runtime.py` — what more than one role needs, plus the orchestration mechanics both loops use (Day 5 T1); `supervisor.py` — `decide_next_step`, `finalise`, `run_supervised`; `researcher.py` — `run_research_step`; `appraiser.py` — `judge_sufficiency`; `single_agent.py` — `run_agent`, the one-agent loop, retained |
| `src/evergrove_agent/tools/` | `base.py` (contract · `RunContext` · `RunBudget`), `registry.py` (the only call path), `hooks.py` (span + budget hooks, T2/T3), `wiring.py` (composition root), `cli.py`, and the eight tools — the four Day 2 ones, `validate_report.py` (S9), and the three in `memory_tools.py` (T4) |
| `src/evergrove_agent/search/` | `base.py`, `normalize.py`, `domains.py` + `domains.json`, `fixture.py`, `serpapi.py`, `academic.py` |
| `src/evergrove_agent/documents/` | `base.py`, `reader.py`, `excerpt.py`, `text.py`, `pdf.py`, `docx.py`, `html.py` |
| `src/evergrove_agent/memory/` | `db.py` (all DDL), `cache.py`, `search_cache.py`, `budget.py`, `prep_memory.py` (cross-run preparation memory, T4), `run_memory.py` (the session-memory mirror, T4) |
| `src/evergrove_agent/tracing/` | `store.py` (the `runs`/`spans` rows), `tracer.py` (the API a hook calls, plus `agent_span` — the context manager an agent boundary uses, Day 5 T6), `render.py` (the read side, Day 4 T6 — pure, writes nothing). Day 4 T1, wired by T2 — `service.py` owns the connection, `tools/hooks.py` writes one span per tool call, and the two loops write one per agent boundary |
| `scripts/` | Operator entry points, not library code. `show_trace.py <run_id>` prints one run's trace as a tree (T6). Not a package: the logic lives in `tracing/render.py` so it can be imported and tested |
| `src/evergrove_agent/service.py` | `prepare_focus_session` — the one entry point; composition only (S11) |
| `src/evergrove_agent/main.py` | CLI entry point — research mode and `--no-research`, flat flags, the progress line (S12) |
| `tests/unit/`, `tests/integration/`, `tests/conftest.py` | Offline suites; `settings` fixture is `Settings(_env_file=None)` |
| `fixtures/` | `search/` recordings, `documents/` attachments, `html/` markup, `README.md` (provenance policy) |
| `.githooks/pre-push` | `ruff check` then `pytest -q`; enable per clone with `git config core.hooksPath .githooks` |
| `.env.example` | The documented setting set; committed defaults must stay offline |
| `docs/research-agent-context.md` | This file |
| `prompts.md` | The required AI interaction log |

**Not present, do not assume:** `evals/`, `.mcp.json`.

---

## Day 1 — completed (summary)

**Schemas** (`schemas/`, re-exported from `schemas/__init__.py`):

- `TaskContext{task_title, session_minutes=25 (5–180), task_description?, attachment_path?}` —
  deliberately identical to the future MCP tool signature.
- `FocusPreparationReport` — the deliverable. Provenance (`run_id`, `generated_at`,
  `model_used`), `original_task`, `session_duration_minutes`, `interpreted_goal`,
  `session_objective`, `topics_to_cover` (2–8), `topics_to_skip` (≤10), `resources` (≤5 of
  `Resource{title, url: HttpUrl, why_this_source, authority}`), `practice?`,
  `success_criteria`, `assumptions` (≤6), `unknowns` (≤6), `hops_used` (0–3),
  `sources_examined`.
- `SourceAuthority = official | standards | primary | secondary | unknown`. `unknown` exists
  for a URL discovered but never read.
- `ToolResult` / `ToolError` / `ErrorCode` — the tool envelope (see *Core contracts*).
- Every model is `extra="forbid"`.

**Configuration** (`config.py`) — one file, `Settings(BaseSettings)` reading `.env`,
`get_settings()` `lru_cache`d process-wide. Secrets are `SecretStr`. Paths resolve against
`PROJECT_ROOT`. Helpers: `provider_for(role)`, `roles_using_hosted`, `force_fully_local()`
(refuses rather than silently rewriting, so the $0-local claim stays checkable).

**LLM layer** (`llm/`) — see *LLM architecture* below.

**Prompts** (`llm/prompts/`) — prompts are version-controlled `.md` files, one per agent turn,
loaded by `load_prompt(name)` / `render_prompt(name, **values)` (`{placeholder}` substitution,
`lru_cache`d, raises `PromptNotFound` listing what exists). S3 added the other three stage
prompts beside `finalise.md`.

**CLI** (`main.py`) — one working path: `--no-research`, a single structured round trip
producing a validated report from model knowledge alone. `max_topics_for(minutes)` =
`min(8, max(3, minutes // 5))`. `_apply_bookkeeping()` overwrites everything the model does not
get to decide (`run_id`, `generated_at`, `model_used`, `original_task`,
`session_duration_minutes`, `resources=[]`, `sources_examined=0`, `hops_used=0`, and the
no-research assumption/unknown) — which is also what stops a model smuggling an invented URL
into a no-research report. Exit codes: 0 success, 1 run failure, 2 bad usage. **S12 replaced the
Day 1 refusals**: research mode and `--attachment` now run; see *The CLI* below.

## Day 2 — completed (summary)

Every deterministic tool the agent will call, working and tested **before any agent exists** —
so when the Day 3 loop misbehaves, the tools are already ruled out.

**Registry and wiring** — `ToolRegistry` in `tools/registry.py`; `build_tool_registry(settings=None,
*, connection=None)` and `TOOL_NAMES` in `tools/wiring.py`. Four tools are registered:
`fetch_url`, `normalize_sources`, `read_document`, `web_search`.

**Tools:**

| Tool | Input → Output |
| --- | --- |
| `web_search` | `WebSearchInput{query 3–200, source_type=general, max_results=6}` → `WebSearchOutput{results: list[NormalizedSource]}` |
| `fetch_url` | `FetchUrlInput{url, max_chars=20000, excerpt_for?}` → `FetchUrlOutput{url, final_url, title, text, char_count, truncated, from_cache, retrieved_at}` |
| `read_document` | `ReadDocumentInput{path, mode=full\|outline\|section, section_hint?}` → `ReadDocumentOutput{path, file_type, page_count, outline, text, truncated}` |
| `normalize_sources` | `NormalizeSourcesInput{sources}` → `NormalizeSourcesOutput{sources, dropped, duplicates_removed}` |

Each tool carries a `description` string written for a model to read; `web_search`, `fetch_url`
and `read_document` describe when to use them, and `normalize_sources`' description says
"Pipeline step; not offered to the model."

**Tools CLI** (`tools/cli.py`, `python -m evergrove_agent.tools.cli`) — `search`, `fetch`,
`read`, `normalize`, each `--json`-capable. Thin by design: argument parsing → one
`build_tool_registry()` call → formatting. Flags map 1:1 onto existing input-model fields and
the CLI re-checks none of their bounds (the registry answers `BAD_ARGUMENTS`); an unset option
is dropped rather than passed as `None`, so defaults live once on the input models. `--backend`
is the one flag that is not a tool argument — it is `settings.model_copy(update=…)`. Exit codes:
0 success, 1 the tool returned a `ToolError` (code and message on stderr, never a traceback),
2 argparse.

**Fixtures** (`fixtures/`) — 6 search recordings covering all four `source_type` values plus an
empty result (5 `handwritten`, plus one real `serpapi` capture added by S14), 2 document
attachments, 1 HTML page. Self-describing format
(`query`, `source_type`, `recorded_from`, `results`); the backend indexes the directory, so the
filename is not the key. `ALLOWED_ATTACHMENT_DIR` defaults to `fixtures/`, so a fresh clone
answers with no configuration. No committed binaries — PDFs and DOCX files are built in memory
by the test helpers.

---

## Core contracts future work must preserve

Changing any of these breaks work that is already finished, or work that is planned on top of
it. Treat each as expensive.

**`schemas/` imports nothing from `evergrove_agent`.** Everything imports it. This is what makes
a circular import impossible. Never add a package import to `schemas/`.

**The tool envelope** (`schemas/tools.py`):

```python
class ToolResult(BaseModel, Generic[T]):
    ok: bool; data: T | None; error: ToolError | None; duration_ms: int; from_cache: bool
class ToolError(BaseModel):
    code: ErrorCode; message: str; retryable: bool
```
A validator enforces exactly one outcome. **Tools never raise** — a failure is a value the agent
can reason about and the trace can record. Changing this shape breaks the registry, every tool,
the Day 4 hooks and both Day 5 workers.

`ErrorCode` members: `BAD_ARGUMENTS`, `BUDGET_EXCEEDED`, `MONTHLY_BUDGET_EXCEEDED`, `TIMEOUT`,
`FETCH_FAILED`, `NOT_FOUND`, `SEARCH_UNAVAILABLE`, `CORRUPT_PDF`, `CORRUPT_DOCX`,
`ENCRYPTED_PDF`, `NO_TEXT_LAYER`, `EMPTY_FILE`, `UNSUPPORTED_TYPE`, `PATH_NOT_ALLOWED`,
`UNKNOWN`. Add one only when a failure genuinely has none.

**The runtime tool contract** (`tools/base.py`):

```python
class Tool(Protocol):
    name: str; description: str; input_model: type[BaseModel]; output_model: type[BaseModel]
    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult[Any]: ...
```
`description` and `input_model` are **the half the model sees** — they are what Day 3 Subtask 2
turns into a `ToolSpec`. `run` is the half only the registry calls.

**`RunContext`** — `run_id`, `budget: RunBudget` (S4) and `span_stack: list[str]` (Day 4 T1). The
registry, every hook and every agent read it, so its shape is expensive to change. T1's field was
**appended with a default**, so no construction site or positional call moved.

**The run's spend ledger** (`tools/base.py`, Day 3 S4) — `RunBudget`, held by `RunContext`, built by
`RunBudget.from_settings(settings=None, *, clock=time.monotonic)`.

```python
BudgetKind = Literal["search", "fetch", "model_call"]
claim(kind) -> bool            # spend one, or refuse; the single enforcement point
remaining(kind) -> int         # clamped at 0
expired / remaining_seconds / elapsed_seconds
hops_remaining(hops_used) / sources_remaining(sources_kept)   # count passed in, never stored
exhausted_limits -> tuple[str, ...]                           # identifiers, not sentences
```

Invariants later work must preserve:

- **`RunState` is what a run has *seen*; `RunBudget` is what it has *spent*.** Neither holds a copy
  of the other's fields. That is why the counters are not on `RunState` (a Pydantic message Day 4
  mirrors to `run_memory`, where a live clock does not belong) and why the sources are not here.
- **`claim` is the only place a limit is enforced**, and it refuses when the kind is exhausted *or*
  the deadline has passed. Claim **before** the call, never after — the same direction
  `memory.budget.reserve_search_call` takes, because over-counting is the safe error.
- **A refusal is `False`, never an exception and never a `ToolResult`.** The ledger knows nothing
  about the tool envelope, `ErrorCode`, or prompt wording. Day 4's pre-hook maps a tool name onto a
  `BudgetKind` and turns the same `False` into `ToolResult(BUDGET_EXCEEDED)` — a lift, not a rewrite.
- **A refused claim increments nothing**, so `remaining` and `exhausted_limits` keep telling the
  finalise step the truth however many times it asks.
- **Limits are snapshotted at construction**, never re-read from `Settings` mid-run.
- **`MAX_HOPS` and `MAX_SOURCES_KEPT` are limits here, not ledgers.** Their counts belong to
  `RunState`, so `hops_remaining` / `sources_remaining` are pure functions over a count the caller
  passes in. Both clamp at 0, because the answer feeds `ResearchAssignment`'s `ge=0` allowances.
- **`exhausted_limits` covers only what this object counts** (the three kinds, plus `"time"`), and
  returns identifiers so that turning them into prompt text stays `prompt_context.py`'s job.
- **The clock is injected** (`clock=time.monotonic`) and monotonic, so a wall-clock adjustment
  cannot hand a run extra budget and a test needs no real wait.
- **`tools/base.py` now imports `config`**, which pulls only `pydantic-settings` — the rule that
  importing `RunContext` must not drag in `httpx`, `sqlite3`, `pypdf` or the search backends still
  holds.

**The tracing contract** (`tools/base.py` + `tracing/`, Day 4 T1) — one `run_id` per run, one
`span_id` per operation, `parent_span_id` from a stack so the tree builds itself (plan §13).
**Wired by T2:** `service.py` opens the run's connection and writes its header, and the registry's
hooks write one `tool` span per call. `single_agent.py` remains untouched by tracing — the loop
does not know a trace exists.

Three layers, and the split is the contract:

```python
# tools/base.py — identity and nesting. Pure; no I/O.
RunContext.span_stack: list[str]              # outermost first
RunContext.current_span_id -> str | None
RunContext.begin_span() -> tuple[str, str | None]   # (span_id, parent_span_id); mints and pushes
RunContext.end_span(span_id) -> None                # pops; tolerant of an out-of-order close

# tracing/store.py — the rows. Raises on a database error.
start_run / finish_run / get_run · start_span / finish_span / get_spans
RunRecord · SpanRecord · SpanKind = agent | tool | llm
RunStatus = running | ok | failed | budget_exhausted

# tracing/tracer.py — what a hook calls.
Tracer(connection, *, settings=None, clock=_utc_now)
    .start_run(ctx, task) · .finish_run(ctx, *, status, hops_used)
    .open_span(ctx, name, kind, *, input_summary=None) -> str
    .close_span(ctx, span_id, *, ok, error_code=None, from_cache=False,
                output_summary=None, duration_ms=None)
```

Invariants later work must preserve:

- **`RunContext` holds identifiers, never I/O.** No connection, no tracer, no `sqlite3` import in
  `tools/base.py` — the same rule that keeps `httpx`, `pypdf` and the search backends off the
  import of `RunContext`. Minting an id is pure; writing a row is `tracing/`'s job, and a hook
  closes over both.
- **Start and finish are a *pair of methods*, not a context manager.** A pre-hook and a post-hook
  are two independent callables with no shared frame: the pre-hook calls `open_span`, the post-hook
  calls `close_span`. A convenience manager for the agent and model spans that *are* opened and
  closed in one frame is additive and belongs with the work that adds them.
- **`parent_span_id` is read before the push**, so a top-level operation is unparented and a sibling
  opened after a close points at the *outer* span, not at its finished sibling. Never derive a
  parent at a call site.
- **Two writes per operation, not one.** A row is inserted when something starts, with `ended_at`
  NULL, and updated when it finishes. Runs take 9–15 minutes on this hardware and S14 killed several:
  `ended_at IS NULL` reads "this never finished", which is a diagnosis rather than a gap. `ok is
  None` is likewise distinct from `ok is False`.
- **`store.py` raises; `Tracer` swallows.** Only `sqlite3.Error`, logged at WARNING — the stance
  `fetch_url` already takes over its cache, and the plan's Day 4 requirement that a storage failure
  never fails a run. The guard is in `Tracer._guard` alone, so a test can still see the real error.
  A `TypeError` or a bad `SpanKind` is a caller bug and still surfaces.
- **The stack is kept consistent whatever the database does.** `open_span` mints and pushes *before*
  it writes, and `close_span` pops *before* it writes. A failed write costs a row, never the nesting
  of the rest of the run.
- **`duration_ms` is accepted when the caller already measured it**, derived from the row's
  timestamps otherwise, and clamped at 0. `ToolRegistry.call` already times every tool and stamps
  `ToolResult.duration_ms`; a post-hook hands that number through rather than creating a second,
  disagreeing measurement of one call.
- **Summaries are bounded at write time** by `TRACE_SUMMARY_CHARS` (200), with an ellipsis marking a
  cut. A trace records enough to recognise a call, not a second copy of the evidence — the fetched
  text already lives in `source_cache`. An unmarked truncation would read as a tool that returned
  exactly that much.
- **The counters are passed in, not recounted.** `Tracer.finish_run` reads the three live ones off
  `ctx.budget` (the one place that happens) and takes `hops_used` as a parameter, because `RunState`
  owns that count — the same division `RunBudget.hops_remaining` already rests on.
- **Wall-clock UTC here, monotonic in `RunBudget`.** A budget must not be enlarged by a clock
  adjustment; a trace has to be a real instant someone can line up against a log line. The clock is
  injected, so a duration is tested without waiting for it.
- **`Tracer` takes a caller-supplied connection** and opens none of its own, as `memory/cache.py`
  does. **`service.py` owns it** (T2's call): one handle per run, opened with
  `db.connect` + `initialize_schema`, handed to both the `Tracer` and the tools it wires, closed
  in the same `finally` that writes `finish_run`. A connection that cannot be opened is logged
  and the run proceeds untraced, the same stance `Tracer` takes over each individual write.

**The cross-agent trace** (`tracing/tracer.py` + `agents/supervisor.py` + `agents/single_agent.py`,
Day 5 T6) — the topology, as rows someone can read back.

```python
# tracing/tracer.py — the manager Day 4 deferred to "the work that adds them".
AgentSpan(span_id: str | None)        # .summarise(text) sets the span's output_summary
@contextmanager
agent_span(tracer, ctx, name, *, input_summary=None) -> Iterator[AgentSpan]
```

| Mode | Spans, outermost first |
| --- | --- |
| `multi` | `supervisor.run` → `supervisor.decide` · `researcher.loop` · `appraiser.judge` · `supervisor.finalise` |
| `single` | `agent.run` |

Invariants later work must preserve:

- **Parenting is `RunContext.span_stack` and nothing else.** `Tracer.open_span` reads the parent
  before it pushes, so a tool call made while a role's span is open nests under that role with
  **no parent id passed down and no change to `tools/hooks.py`**. Never thread a parent through a
  stage function, and never derive one at a call site — that is the rule Day 4 wrote and T6 is
  the proof it was worth writing.
- **The spans open at the call sites in the loops, never inside the stage functions.**
  `decide_next_step`, `run_research_step` and `judge_sufficiency` are shared by both loops; a
  span opened inside one of them would appear in `single` too, and a single-agent trace that
  claims a Supervisor delegated to a Researcher describes a topology that did not happen.
- **`single` gets one span on purpose.** One agent performs all four stages, so there is no
  delegation to record; the mode difference is then readable straight off the tree, which is the
  distinction T6 exists to create.
- **The three memory tool calls sit under `supervisor.run` / `agent.run`, not under a worker.**
  Recalling and filing away is the coordinator's bookkeeping. A `record_run_memory` span under
  `researcher.loop` would mean the mirror moved inside a delegation.
- **`appraiser.judge` has no children, and that is an assertion, not an observation.** The module
  imports neither the registry nor `dispatch`, so there is no path from that stage to a tool; the
  empty child list is where the structural rule becomes checkable on a recorded run.
- **A raising stage still closes its span** — `ok=False`, `error_code=type(exc).__name__` — and
  the exception propagates untouched. `BaseException` is caught and re-raised, because a
  cancelled run is exactly the run whose trace is wanted. A span left open re-parents everything
  after it for the rest of a fifteen-minute run.
- **`tracer=None` pushes nothing.** An untraced run nests exactly as it did before T6, which is
  what keeps `service.py`'s "the run proceeds untraced" literally true.
- **The `Tracer` travels as an argument**, from `service.py` into `run_supervised` / `run_agent`.
  Not on `RunContext`, which holds identifiers and never I/O; not off the registry, which
  dispatches tools and does not own the run.
- **A span summary is a line, never a second copy of the evidence.** `_verdict_summary` records
  the three fields the loop acts on; the full semantic judgement already reaches `run_memory`
  through `runtime._appraisal_line`, and `TRACE_SUMMARY_CHARS` bounds this at 200 either way.

**The memory contract** (`memory/prep_memory.py` + `memory/run_memory.py` + `tools/memory_tools.py`,
Day 4 T4) — two memories that share one SQLite file and nothing else.

```python
# memory/prep_memory.py — cross-run. Raises on sqlite3.Error.
normalize_task_key(task_title) -> str
save_preparation(connection, *, report, now=None) -> PreviousPreparation
recall_previous_preparation(connection, *, task_title, max_age_days=None, now=None)
                                                     -> PreviousPreparation | None

# memory/run_memory.py — within-run, the durable mirror of RunState. Raises.
RunMemoryKind = goal | finding | appraisal | decision | seen_url | seen_query
RunMemoryEntry(kind, content) · RunMemoryRecord
entries_from(*, goal, decision, findings, appraisal, queries, urls) -> list[RunMemoryEntry]
record_entries(connection, *, run_id, hop, entries, now=None) -> int
get_run_memory(connection, run_id, *, kind=None) · seen_queries(...) · seen_urls(...)

# tools/memory_tools.py — the guard. Never raises.
recall_previous_preparation : RecallInput{task_title, max_age_days?}
                              → RecallOutput{found, previous}
save_preparation            : SavePreparationInput{report}
                              → SavePreparationOutput{saved, run_id, task_key}
record_run_memory           : RecordRunMemoryInput{hop, entries[]}
                              → RecordRunMemoryOutput{written}
```

Invariants later work must preserve:

- **`RunState` is the session memory the loop runs on; `run_memory` is its mirror.** Nothing in
  the loop reads a decision back out of SQLite, and nothing may start: `RunState` already owns
  `used_queries` / `evidence_urls` / `fetched_urls` and the loop already carries it hop to hop.
  The table exists because `RunState` dies with the process and a 9-15 minute run deserves a
  record. `seen_queries` / `seen_urls` are for an audit, an evaluation or a test — never for a
  decision.
- **The two memories never answer each other's question.** `prep_memory` is keyed on a normalised
  task and survives runs; `run_memory` belongs to one `run_id` and describes hops. A run reading
  another run's `run_memory` would hold evidence it never gathered, which is why the tool takes
  `run_id` from `ctx` and never from its arguments.
- **Storage raises, the tool swallows** — the same split as `tracing/store.py` and `Tracer`, and it
  is what makes the guard testable. A storage failure becomes
  `ToolResult(ok=False, ErrorCode.UNKNOWN, retryable=False)` plus a WARNING log. **Nothing raises,
  so memory can never end a run.**
- **`found=False` means "nothing recent matches", not "something broke".** A first run is
  `ok=True` with an empty payload; only a dead database is a failing `ToolResult`. Collapsing the
  two — which is the plan's literal wording — would make every first run carry a failed tool call
  and hide a real outage inside a normal answer.
- **A preparation is saved only from `run_agent`, from the value `finalise` returned.** That is
  structurally the only report that passed `validate_report`; every other outcome is a
  `PreparationFailed`. There is no `validated=` flag and no second grounding check, and moving
  that call anywhere a failure also passes through (a `finally`, `service.py`'s `except`) would
  silently break the rule. The failed run still keeps its `run_memory` rows — the run happened.
- **Both write calls are best-effort and their results are deliberately not inspected.** There is
  nothing the loop could usefully do about a mirror that did not write, and a run that produced a
  valid report has already succeeded.
- **Rows are appended, never replaced, and nothing is deleted on age-out.** A task prepared three
  times keeps three rows and recall takes the newest within `MEMORY_RECALL_MAX_AGE_DAYS`; an aged
  preparation stays for audit and simply stops being recalled, the stance `cache.py` takes to an
  expired page.
- **`normalize_task_key` is the whole cross-run feature**, so its stopword list is deliberately
  small, explicit and local — an imported corpus that updated would stop matching every
  preparation already on disk. Tokens are lowercased, punctuation-stripped (keeping `c++`, `c#`,
  `node.js` intact), deduped and **sorted**, so word order cannot split one subject in two. An
  all-stopword title falls back to its full token set rather than keying on `""`, which would
  match every other such title.
- **The age comparison happens in Python against an injected `now`**, not in SQL — the stored
  format is `prep_memory.py`'s business and a naive legacy row must not raise inside a
  comparison. SQL does the indexed equality on `task_key`. A row whose JSON cannot be read
  degrades to a **miss**, so one bad row cannot hide a good one.
- **Registered, never advertised.** `advertised_tool_names` lists none of the three, and a test
  pins that for every role and both attachment states. A model that could call `save_preparation`
  could save a report that never passed validation.
- **None of them costs budget.** `TOOL_BUDGET` has no entry for a local SQLite write, and a name
  absent from that map is free.

**The read side** (`agents/single_agent.py` + `agents/prompt_context.py` + `llm/prompts/plan.md`,
Day 4 T5) — what a *recalled* preparation is allowed to do to the next run.

```python
# agents/single_agent.py
async def _recall_previous_preparation(registry, ctx, task, settings) -> PreviousPreparation | None
RunState.previous                                    # schemas/agents.py, additive and defaulted

# agents/prompt_context.py — "" when nothing was recalled
render_previous_preparation(previous) -> str         # plan.md's {previous_preparation}
render_continuation_note(previous) -> str            # finalise()'s extra Message
```

Invariants later work must preserve:

- **One recall per run, in `run_agent`, before the first decision.** This is the answer to the
  question the deviation table left open. Not `service.py` (composition only, and `run_agent` must
  behave the same when a test calls it directly) and not per stage — the planner and the report
  must not be able to disagree about what the last session did.
- **`None` is the answer to every failure.** A miss (`found=False`), a storage failure, a payload
  of an unexpected type and an aged-out row all degrade to `None`, and `None` means the run is the
  run this project shipped before T5. `registry.call` never raises and the tool never raises, so
  there is no path from a broken database to a failed run.
- **`found` is read, never inferred** from `previous is not None`. The tool's contract makes that
  field the distinction between "nothing recent matches" and a failure; reading around it
  re-couples the two the guard exists to separate.
- **`max_age_days` is passed explicitly** from the run's `Settings`. Leaving it `None` is
  documented as "use `MEMORY_RECALL_MAX_AGE_DAYS`", but `prep_memory` resolves that default
  through the process-wide `get_settings()`, which ignores a `settings` override the loop was
  handed.
- **`source_urls` never reaches a prompt.** Both blocks render the previous goal, objective,
  `topics_covered` and `topics_deferred` and nothing else. Yesterday's URLs in front of the stage
  that writes `resources` invite a citation S9's grounding check must then reject — spending
  finalise attempts undoing something we volunteered. `PreviousPreparation` already says these are
  "context, never evidence"; this is where that has teeth.
- **Guidance, never enforcement.** Nothing filters `topics_to_cover` in code and
  `_apply_bookkeeping` does not touch `interpreted_goal` — the same stance it takes to
  `resources`. A deferred topic that no longer fits the task or the session is a worse next step
  than one the model picks itself, so both blocks state a preference with an explicit escape. A
  continuation the model declined is a decision to read in the trace, not a bug to paper over.
- **The continuation reaches `finalise` as an extra `Message`**, appended to the *opening* turns
  after the stop-reason note, so every rung of the retry ladder argues with a model that was told
  about the earlier session. `finalise.md`'s five placeholders stay frozen — the same mechanism and
  the same reason as `render_stop_reason`.
- **`plan.md` gained a fifth placeholder** and that is safe: `decide_next_step` is its only
  caller, and `PROMPT_PLACEHOLDERS` in `tests/unit/test_prompt_context.py` is the guard that a
  file and its caller cannot drift. **`finalise.md` is the frozen one, not this.**
- **Cross-run and within-run memory stay separate, and T5 did not touch the second.**
  `RunState.previous` is what an *earlier* run prepared; `RunState`'s findings, `used_queries` and
  `evidence_urls` remain the only session memory the loop runs on, carried hop to hop exactly as
  S8 left them. Nothing reads a decision back out of SQLite.

**`ToolRegistry`** — `register`, `get`, `names`, `add_pre_hook`, `add_post_hook`, and
`async call(name, args, ctx) -> ToolResult`. `args` may be the tool's input model, a raw mapping
(**which is the form a model's tool call arrives in**) or `None`. Order: resolve → validate args
→ pre-hooks → `Tool.run` → post-hooks. **`call` never raises**; it times the call and stamps
`duration_ms` centrally; an unknown name returns `UNKNOWN` with the menu, invalid arguments
return `BAD_ARGUMENTS`, and a tool that raises anyway is caught **and still handed to the
post-hooks** (T2), so the one call that breaks the contract is not the one call whose span never
closes. A pre-hook returning a `ToolResult` short-circuits the tool — that is how the budget
refusal works. Duplicate registration raises at wiring time; everything at call time is a
`ToolResult`.

**`tools/hooks.py`** (T2/T3) — `TOOL_BUDGET`, `enforce_run_budget`, `TracingHooks` and
`install_registry_hooks`, which `wiring.py` calls. **Install order is the behaviour:** the
tracing pre-hook first (so a refused call is still a span), `enforce_run_budget` second (so
nothing runs unpaid), the tracing post-hook over whichever result came back. The span id is
correlated between the two halves by the `ToolInvocation` object, not by re-reading
`ctx.current_span_id`. Budget enforcement is unconditional; tracing needs a `Tracer` and is
otherwise absent.

**The LLM contract** (`llm/base.py`): `Message{role, content}`, `ToolSpec{name, description,
parameters: JSON Schema}`, `ToolCall{name, arguments: dict}`, `LLMResponse{text, model, provider,
tool_calls, duration_ms, finish_reason}` with `parse_as(schema)`, `LLMError(provider, message)`,
and

```python
async def generate(self, messages, *, schema=None, tools=None, temperature=0.0) -> LLMResponse
```
Changing this signature forces updates to all three agents. **`ToolSpec` and `ToolCall` already
exist — Day 3 must reuse them, not define new model-facing types.**

**The search contract** (`search/base.py`): `SearchBackend` protocol (`name`, `async search(query,
*, source_type, max_results) -> list[RawSource]`), `SearchSourceType = docs | technical |
academic | general`, `SearchBackendError(backend, message, *, retryable=True)`. A backend returns
`RawSource` — S4's existing model, never a new result type — and **raises** rather than returning
a `ToolResult`, so an empty list stays distinguishable from a broken backend.

**The agent contracts** (`schemas/agents.py`, Day 3 S1) — the typed messages the four
reasoning stages exchange. Two families, and the split is the contract:

| Family | Models | Constraint |
| --- | --- | --- |
| **Model output** — handed to `generate(schema=…)` | `SupervisorDecision`, `AppraisalVerdict` | Small and flat, because a 4B model's adherence degrades as the target grows; must survive `to_gemini_schema` for the hosted retry; `extra="forbid"` so drift becomes a retry |
| **Code assembled** — a model never sees the schema | `GatheredSource`, `ToolFailure`, `ResearchAssignment`, `ResearchFindings`, `AppraisalRequest`, `RunState`, `PreviousPreparation` (T4) | As rich as the loop needs |

Invariants later work must preserve:

- **`GatheredSource` is not `NormalizedSource`.** It cannot be: `search/normalize.py` imports
  `schemas`, so the reverse is a cycle. It also should not be — `NormalizedSource` is a search
  hit, `GatheredSource` is the agent's evidence, carrying the text actually read. S6 builds one
  from a `NormalizedSource` plus, when the page was opened, a `FetchUrlOutput`.
- **`retrieved_at is None` means discovered but never opened.** `ResearchFindings.sources` holds
  everything a hop *discovered*, not only what it read. That is what makes the grounding rule
  expressible: a cited URL must be in `RunState.evidence_urls`, and one outside
  `RunState.fetched_urls` may only claim `authority="unknown"`.
- **`RunState`'s four derived properties (`all_sources`, `evidence_urls`, `fetched_urls`,
  `used_queries`) are the single definition of what a run has seen.** S6 dedupes against them,
  S9 grounds against them, S3 renders them. Never recompute one locally.
- **No validator forces a `requested_followup` when `sufficient` is false.** "Not enough, and
  nothing specific would help" is a real verdict; the loop answers it by finalising with
  populated `unknowns`. Adding that validator would create a retry loop and invite an invented
  question.
- **`ResearchAssignment.max_searches` / `max_fetches` are an allowance, not a ledger.** The live
  counters are `RunContext`'s (S4), in one place, so Day 4 can lift enforcement into hooks.
- **`AppraisalVerdict` carries the semantic judgement, and it informs without deciding (T2).**
  `accepted[]`, `rejected[]` and `disagreements[]` were added additively, every one defaulting
  to `[]`, so a Day 3-shaped reply is still a valid verdict — which matters because this is a
  constrained-decoding target and a 4B model routinely fills only the fields it understands. A
  lost default would fail `model_validate_json`, spend `_decode`'s one re-ask, and return
  `None`, which the loop reads as "the appraiser could not answer" and **stops the run on**.
  `_stop_after_hop` reads `sufficient`, `requested_followup` and **`len(accepted)`** (T5
  widened it by one field; `rejected` and `disagreements` still only inform), so a verdict that
  names the wrong sources cannot redirect a run — it can only make the report and the trace
  more honest, and make a thin "yes" read as the `thin_evidence` stop it is. Both consumers render only populated lists: `_appraisal_line` omits an
  empty segment, `render_research_context` omits the whole heading, because an empty heading in
  front of the next model invites the invented contradiction the honest empty case exists to
  avoid. They are `list[str]`, deliberately not URLs — prompt material and a trace line, never a
  citation menu, since a cited URL must still be in `RunState.evidence_urls` (S9).
- **`PreviousPreparation` is a summary, not a stored report** (T4). Only what a later session needs
  to continue: the previous goal and objective, `topics_covered`, `topics_deferred`, the source
  URLs, the run id and when it was saved. It lives here rather than in `memory/` because
  `memory/prep_memory.py` produces it, the recall tool returns it, and T5 puts it in front of both
  the planner and the report — `schemas/` is the one layer all three can import. Its `source_urls`
  are **context, never evidence**: a citation must still be grounded in what *this* run gathered
  (S9), which is why T5 renders every other field of this model into a prompt and never that one.

**`SearchSourceType` now lives in `schemas/tools.py`**, re-exported from `search/base.py` so every
existing importer is unchanged. It moved because the Supervisor's `source_preference` is handed to
`web_search` as `source_type` with no translation, and `schemas/` is the only layer both the search
package and `schemas/agents.py` can import. **Never re-declare it** — a second copy drifts into a
runtime bug, and a test pins the identity.

**The model-facing tool contract** (`agents/tool_calling.py`, Day 3 S2) — the only bridge
between a model and the registry. Five pieces, no new model-facing types: `ToolSpec` and
`ToolCall` are Day 1's and are reused unchanged.

```python
advertised_tool_names(role, *, has_attachment=False) -> tuple[str, ...]
to_tool_spec(tool) -> ToolSpec                      # parameters = input_model.model_json_schema()
advertise(registry, role, *, has_attachment=False) -> list[ToolSpec]
async dispatch(call, *, registry, ctx, allowed) -> ToolResult
async dispatch_all(calls, *, registry, ctx, allowed) -> list[ToolCallOutcome]
```

Invariants later work must preserve:

- **The menu is keyed on `config.AgentRole`**, not a separate stage enum — the same three
  values `build_provider(role)` routes on, so the stage that picks a provider and the stage
  that picks a menu cannot drift. `supervisor` and `appraiser` get **no tools**: planning,
  finalising and judging are constrained-decoding calls made with `schema=`, and a menu
  handed to one of them is `format` and `tools` in a single Ollama payload. Only
  `researcher` acts: `web_search` + `fetch_url`, plus `read_document` **only when the task
  carries an attachment**. `normalize_sources` is never advertised to anyone.
- **`advertised_tool_names` is both the advertisement and the allow-list.** `dispatch`
  refuses any name outside `allowed` *before* the registry is reached, as
  `ToolResult(UNKNOWN, retryable=False)` naming what was available — the same code and
  sentence shape the registry uses for an unknown name. This is what stops "registered"
  from meaning "reachable"; every other registered tool is one guessed name away otherwise.
- **The bridge never builds a tool's input model.** Model arguments go to `registry.call`
  as the raw mapping they arrived as, so `_parse_args` stays the single argument validator
  and `BAD_ARGUMENTS` keeps one definition.
- **`dispatch_all` is sequential, in call order** — never `asyncio.gather`. S4's counters
  and the monthly search quota are claimed one call at a time, and a run whose tools fire
  in a varying order cannot be read back off a trace.
- **`ToolCallOutcome(call, result)`** is a frozen dataclass beside the dispatcher, not a
  Pydantic model in `schemas/`: it is not a message between reasoning stages, and `schemas/`
  may not import `llm.base.ToolCall`. The precedent is `ToolInvocation`.
- **No provider is named anywhere in the module.** `OllamaProvider` passes
  `ToolSpec.parameters` through and `HostedProvider` applies `to_gemini_schema` to it; both
  already parse their own replies into `ToolCall`. Never add a provider branch here.
- **`dispatch` takes a `ToolCall`, not an `LLMResponse`** — which is what makes the Day 3
  contingency free: a structured `{"action", "arguments"}` decision under a constrained
  schema constructs a `ToolCall` and reuses the same dispatcher, with no second tool path.

**The prompt contract** (`llm/prompts/*.md` + `agents/prompt_context.py`, Day 3 S3) — four
stage prompts and the renderers that fill them. **Wording is safe to change; the placeholder
set and the rendering split are not.**

| Prompt | Stage | Model output | Placeholders |
| --- | --- | --- | --- |
| `plan.md` | `decide_next_step()` | `SupervisorDecision` | `task_title`, `task_description`, `session_minutes`, `progress`, `previous_preparation` (T5) |
| `research_step.md` | `run_research_step()` | tool calls | `research_question`, `session_minutes`, `source_preference`, `available_tools`, `allowance`, `already_covered`, `attachment` |
| `sufficiency.md` | `judge_sufficiency()` | `AppraisalVerdict` | `research_question`, `session_minutes`, `sources` |
| `finalise.md` | `finalise()` | `FocusPreparationReport` | `task_title`, `task_description`, `session_minutes`, `max_topics`, `research_context` |

Invariants later work must preserve:

- **`finalise.md`'s five placeholders are frozen.** `main.py`'s `--no-research` path passes
  exactly those; a sixth is a `KeyError` on the one shipped path. S10's retry therefore quotes
  validation errors back as an **extra `Message`**, never as a new placeholder. S3's revision was
  additive only: the not-opened → `authority="unknown"` rule, `unknowns` extended to carry the
  research's own gaps, and `original_task`/`session_duration_minutes` folded into the bookkeeping
  bullet.
- **One file = one user message**, rendered by `render_prompt` and sent as
  `Message(role="user", …)` — as `main.py` already does. No system-role preamble, no provider
  named, no output syntax described beyond "answer with JSON matching the schema you were given":
  constrained decoding supplies the schema.
- **`render_prompt` is `str.format`.** A literal `{` in a prompt file is a runtime crash. The
  prompts deliberately carry **no JSON examples** — an example is a second, drifting copy of the
  schema.
- **Prompts never restate a deterministic rule.** Field bounds are Pydantic's and the grounding
  set-membership check is S9's. The prompts state intent once and own nothing a validator owns.
- **`agents/prompt_context.py` is the only place a run's state becomes prompt text**, one
  function per placeholder. It lives in `agents/` because it imports `schemas/` and `config`, and
  `llm/` imports neither — keeping the loader independent of both. Nothing in it calls a model, a
  tool, the network or the database.
- **The evidence split is a context-window decision, not a style one.** `NUM_CTX` is 4096:
  `render_sources` (→ `sufficiency.md`) carries **full excerpts** at `SOURCE_EXCERPT_CHARS`
  because the appraiser is the one stage that must read the evidence; `render_research_context`
  (→ `finalise.md`) carries a **compact block** — title, URL, authority, read/not-read and the
  search snippet, plus each hop's `ResearchFindings.notes`, the verdict's `missing_information`
  and every `ToolFailure`. No new tunable was introduced. Raising `NUM_CTX` to 8192 is the
  alternative if S14 shows the compact block is too thin.
- **Read and unread sources are spelled out in words** (`status: read` /
  `status: found but not opened`), in one shared block shape used by both prompts. The authority
  rule and S9's grounding check both depend on that distinction surviving into the text.
- **`render_available_tools` takes the `ToolSpec`s `advertise()` produced**, not a role, and
  renders **names only**. The descriptions and argument schemas already travel with the specs; a
  prose copy would be the second tool-selection mechanism S2's allow-list exists to prevent.
- **A failure is rendered with its code, its message and whether a retry could help.** That is
  what makes `research_step.md`'s recovery instruction actionable — a model told only "that did
  not work" invents a replacement.

**The loop** (`agents/single_agent.py`, Day 3 S5–S8) — four separately-prompted stage
functions and the loop that drives them. Public surface:

```python
AgentProviders(supervisor, researcher, appraiser, fallback=None)  # .from_settings(settings)
PreparationFailed(RuntimeError)                        # exception, not a schema
    .run_id · .attempts · .issues                      # message-first, facts also attached
StopReason = Literal["sufficient", "planner_finalised", "hop_cap", "budget_spent",
                     "no_followup", "planner_unavailable", "appraiser_unavailable"]
async decide_next_step(state, *, provider, ctx, settings=None)  -> SupervisorDecision | None
async run_research_step(assignment, *, provider, registry, ctx, settings=None,
                        attachment_path=None)                   -> ResearchFindings
async judge_sufficiency(request, *, provider, ctx, settings=None) -> AppraisalVerdict | None
async finalise(state, *, provider, ctx, stop_reason="sufficient", settings=None,
               fallback_provider=None)                          -> FocusPreparationReport
async run_agent(task, *, registry, providers=None, ctx=None, settings=None)
                                                                -> FocusPreparationReport
```

Invariants later work must preserve:

- **`None` from a stage means "this stage could not answer"** — a refused claim or output that
  drifted twice. It is ordinary control flow, and every caller answers it the same way: stop
  and finalise with what was gathered. Only `run_research_step` never returns `None`; a hop
  that gathered nothing is still a fact about the run.
- **Two reserves, one ledger.** `_claim_reasoning_call(budget, reserve=...)` is the only place
  a floor is applied: `_FINALISE_RESERVE = 1` for planning and judging, `_RESEARCH_RESERVE = 2`
  inside a hop so a research turn cannot leave its own hop unjudged. It falls through to
  `claim`, which remains the sole enforcement point. On the shipped defaults this fits two
  fully-judged hops plus the report into `MAX_MODEL_CALLS=10` with nothing to spare.
  **`finalise` claims with no reserve** — a refusal there means the deadline passed, and the
  run raises `PreparationFailed` rather than returning a partial report.
- **Tool-budget enforcement left the loop in T3.** `TOOL_BUDGET` + `enforce_run_budget` now live
  in `tools/hooks.py` and run inside `ToolRegistry.call`: `web_search` → `search`, `fetch_url` →
  `fetch`; `read_document` is local disk and free; a name the model invented is absent from the
  map, so a guessed name cannot drain a budget. The loop just calls `dispatch`.
  **The known over-count is gone:** the pre-hook runs *after* argument validation, so a
  malformed call is `BAD_ARGUMENTS` without being charged. A tool the step was not offered is
  also no longer charged, since `dispatch` refuses it before the registry is reached.
- **`dispatch_all` is not used by the loop** — the counters are claimed one call at a time, so
  `dispatch` is called per tool call, still strictly in the model's own order.
- **A later hop's question is never written by our code.** Still true, but the path changed in
  T5: `requested_followup` → `state.verdict` → `_outstanding_followup` → `_followup_decision`
  → `ResearchAssignment.research_question`, with **no planner turn in between**. The loop only
  carries it. A null follow-up ends the run (`"no_followup"`); never invent one.
- **`RunState.findings` is reassigned, never appended to.** `validate_assignment=True` does not
  see an in-place `append`, and the `max_length=3` bound is what the hop cap rests on.
- **When no hop remains the planner is skipped entirely**, before `decide_next_step` — `plan.md`
  has no "you may not research" wording, and skipping saves a model call for the report.
- **`_absorb` owns `NormalizedSource` (+ `FetchUrlOutput`) → `GatheredSource`**, keyed on
  `canonicalize_url` so a search hit and a later fetch of the same page are one entry. It
  clamps `title`/`snippet` to `GatheredSource`'s ceilings, which neither source model bounds.
  `read_document` is deliberately absent: an attachment has no URL and nothing to ground
  against, so it never becomes a citable source — its text still reaches the model through
  `render_tool_outcome`.
- **`sources_examined` counts sources *read*** (`len(RunState.fetched_urls)`), settling the
  question S1 left open. **`resources` is left exactly as the model wrote it** — grounding is
  S9's single definition and must not be duplicated in bookkeeping.
- **`MAX_SOURCES_KEPT` bounds *read* sources, not leads.** It reaches a run only by sizing
  `ResearchAssignment.max_fetches` through `sources_remaining` — an allowance, as S4 specifies,
  not a second enforcement point. Leads cost nothing to keep and are what the grounding set is
  built from.
- **The loop writes memory best-effort and reads it exactly once (T4/T5).** Two write calls:
  `_mirror_session_memory` after each hop is judged (whether or not it was — a hop that lost its
  appraiser is exactly the history someone wants), and `_remember_preparation` on the report
  `finalise` returned. Neither result is inspected, neither can raise, and neither changes a
  decision. One *read* call, `_recall_previous_preparation`, runs before the loop and seeds
  `RunState.previous`; its result **is** inspected, and every failure shape degrades to `None`.
  `RunState` is still the only session memory the loop runs on.
- **`_MAX_RESEARCH_TURNS = 3` and `_MAX_DECODE_ATTEMPTS = 2`** — one is not enough for a hop
  (the model must see search results before it can open anything), and the re-ask is what
  `extra="forbid"` exists to trigger. Both are bounds, not targets.

**The stop-reason contract** (`prompt_context.render_stop_reason`) — the block S4 said was
owed. It turns `exhausted_limits`' identifiers into one sentence, and returns **`""` when the
run finished and spent no limit**, which is the signal that neither the extra message nor the
`unknowns` entry is needed. `sufficient` and `planner_finalised` carry no cause on purpose: a
healthy run must not read like a degraded one. It travels to `finalise.md` as an **extra
`Message`** — the five placeholders stay frozen — *and* is appended to `unknowns`, because a
model may ignore the message.

**`max_topics_for` moved from `main.py` into `prompt_context.py`.** `{max_topics}` is a
`finalise.md` placeholder and that module owns one function per placeholder; both the
`--no-research` path and `finalise()` render that prompt, and a second copy of a sizing rule
means two different sessions from the same number of minutes. `main.py` imports it from
`agents.prompt_context` **directly, not via `agents/__init__.py`**. The original reason — keeping
`httpx` and `sqlite3` off the `--no-research` path — **no longer holds since S12**: `main.py`
imports `service.py` at module level, so the loop and the wired registry load either way. The
direct import is kept because it is precise, not because it saves anything; do not cite the old
rationale for a new decision.

**The grounding contract** (`tools/validate_report.py`, S9) — the third validation of a report,
after constrained decoding and `model_validate_json`, and the only one that can see what the run
actually gathered. Pure: two sets and a report in, a `ReportValidation` out; no model, no network,
no clock.

- `validate_report(report, *, evidence_urls, fetched_urls, max_topics, research_performed)`
  returns **`ReportValidation{ok, issues: list[ReportIssue]}`**, never a bare bool. Each
  `ReportIssue` carries a stable `code`, a `field` path in the report's own vocabulary
  (`resources[2].url`), and a one-line `message` written to be shown to a model unedited.
  `as_lines()` is the single rendering of those issues — what S10 stores in
  `RunState.validation_errors` *and* quotes into the retry message, so a failed run's report and
  the model's instructions are one list.
- **Every issue is returned, not the first.** A retry told about one problem at a time spends an
  attempt learning what was already known.
- **Both sides of every URL comparison go through `canonicalize_url`** — the same function
  `GatheredSource.url` was built with. A citation differing only by a fragment, a tracking
  parameter or a trailing slash is the same page; treating it as ungrounded would make the S10
  ladder burn every attempt correcting nothing.
- **`max_topics` is passed in, never computed here.** `max_topics_for` stays the one definition
  of the sizing rule, and `tools/` never imports `agents/`.
- Registered in `wiring.py` as `validate_report` and, like `normalize_sources`, **never
  advertised** — whether a report passes is not the model's call. Internal callers compose with
  the pure function. The wrapper's `ToolResult.ok` answers "did the validator run", so a rejected
  report is `ok=True` carrying `ReportValidation.ok=False`.
- The `cited is None` branch is a guard, not reachable behaviour: `Resource.url` is an `HttpUrl`,
  so a non-http(s) citation is rejected by the schema first.

**The retry ladder** (`finalise()` in `agents/single_agent.py`, S10) — the only caller of
`validate_report`, and the reason a run cannot return a report its own evidence contradicts.

- **`MAX_OUTPUT_RETRIES` means total attempts, not retries.** The shipped 3 is one initial call
  plus two corrections. The setting name is the plan's and is kept; the mismatch is recorded in
  `finalise`'s docstring, which is the one place it can be read next to the loop that consumes it.
  Do not "fix" the name without re-approving what the number means.
- **One ladder for two failure kinds.** A reply that fails `model_validate_json` and a report that
  fails `validate_report` are the same event to the caller — *this report is not acceptable* — so
  both spend an attempt and both are quoted back. Only the second kind produces `ReportIssue`s;
  a shape failure quotes `_errors(exc)` and leaves `PreparationFailed.issues` empty, because there
  was never a report to have a verdict about.
- **Validation runs *after* `_apply_bookkeeping`, never before.** `original_task`,
  `session_duration_minutes` and the appended `unknowns` note are set there and read by
  `goal_not_narrowed`, `too_many_topics` and `unknowns_required`. Validating the raw reply would
  reject reports for fields `finalise.md` explicitly tells the model not to fill in.
- **The correction is an extra `Message`, and every attempt is rebuilt from the *opening* turns**
  (`_with_correction`) rather than appended to the previous one. Attempt 3 argues with attempt 2's
  answer alone; the evidence and the stop reason sit in the opening turns and travel unchanged,
  which is what makes every attempt argue about the same run inside a 4096-token window.
- **The retry corrects the report, never the research.** No search, no fetch, no new evidence
  between attempts — the grounding set is frozen the moment finalisation starts. A ladder that
  re-entered the loop would spend SerpAPI quota rewriting a paragraph.
- **Every attempt claims `model_call`; a refusal ends the ladder.** No retry path goes around
  `claim`. `_FINALISE_RESERVE` stays **1**: it guarantees the *first* attempt, and retries spend
  whatever slack the loop left. Raising it to 3 would guarantee the ladder by taking three calls
  away from research on a 10-call budget, which is the opposite of the agreed trade.
  `PreparationFailed.attempts` is how a caller tells "the model cannot write a valid report" from
  "the run ran out of room to ask again".
- **Only the last attempt switches provider, and only when an alternate is genuinely configured.**
  `_alternate_provider` requires `GOOGLE_API_KEY` for a hosted alternate — `HostedProvider`
  constructs without one and raises only when called, so an assumed alternate would spend the
  final attempt discovering there was none. `AgentProviders.fallback` is defaulted and resolved in
  `from_settings`, so composition stays in one place and a test can inject a second provider.
  With `MAX_OUTPUT_RETRIES=1` there is no switch: the first attempt always belongs to the primary.
- **The failure that ends the ladder is the one reported.** A drift clears both `issues` and
  `RunState.validation_errors` before retrying, and the terminal message names which kind of
  failure ended the run. Without that, a rejection followed by two drifts reports the *first*
  attempt's citation issues as the final verdict, and whoever reads it goes looking for a
  citation problem in replies that never parsed.
- **`RunState.validation_errors` is written by the ladder and read by nobody yet — kept
  deliberately.** `PreparationFailed.issues` serves the failure path, but a run that was
  *corrected* and then succeeded currently leaves no trace that it needed correcting.
  **S11 owes surfacing it**, and Day 4 tracing is the other candidate. It is a live field, not
  dead weight: keep its lifecycle correct (set on a rule failure, cleared on a drift) so
  whatever surfaces it is not showing a stale verdict.
- **The alternate's `LLMError` folds into `PreparationFailed`; the primary's still propagates.**
  An unreachable primary is a broken run, as everywhere else in the project. A fallback that was
  reached for *because* the run was already failing must not replace the real reason with a
  connection error.

**The role split** (`agents/runtime.py` + `supervisor.py` + `researcher.py` + `appraiser.py`,
Day 5 T1) — the same four stage functions, in four files instead of one, plus a second loop.

```python
# agents/runtime.py — what more than one role needs. Imports no sibling in agents/.
StopReason · PreparationFailed · AgentProviders · _alternate_provider
_claim_reasoning_call · _FINALISE_RESERVE · _RESEARCH_RESERVE · _MAX_DECODE_ATTEMPTS
_decode · _RETRY_INSTRUCTION · _errors
_assign · _recall_previous_preparation · _mirror_session_memory · _appraisal_line
_remember_preparation

# agents/supervisor.py — decides, coordinates, reports.
async decide_next_step(state, *, provider, ctx, settings=None) -> SupervisorDecision | None
async finalise(state, *, provider, ctx, stop_reason, settings=None, fallback_provider=None)
async run_supervised(task, *, registry, providers=None, ctx=None, settings=None)
async _delegate_hop(decision, state, *, registry, providers, ctx, settings)
_stop_before_planning(state, budget) · _stop_after_hop(verdict)

# agents/researcher.py — gathers. The only role with a tool menu.
async run_research_step(assignment, *, provider, registry, ctx, settings=None,
                        attachment_path=None) -> ResearchFindings

# agents/appraiser.py — judges. Imports neither the registry nor the dispatcher.
async judge_sufficiency(request, *, provider, ctx, settings=None) -> AppraisalVerdict | None
```

Invariants later work must preserve:

- **The import graph is a DAG, and siblings are reached by module path.** `runtime` → the
  three roles → `single_agent` → `__init__`. **No module inside `agents/` may import
  `evergrove_agent.agents`** — that makes the package re-enter itself. A test in
  `tests/integration/test_multi_agent.py` parses the sources and pins this.
- **The workers never import each other, and never import the supervisor.** That is what
  "Researcher and Appraiser do not communicate directly" means operationally, and
  `supervisor._delegate_hop` is structurally the entire channel between them: it is the one
  place a `ResearchFindings` becomes an `AppraisalRequest`. Pinned by the same test.
- **`appraiser.py` imports neither `tool_calling` nor `tools.registry`**, so "the Appraiser
  performs no research" is a property of what it can reach, not of what one scripted run
  happened to do. `advertise` already gives it an empty menu; this is the second lock.
- **The Researcher never finalises and never decides the run is over.** Neither function
  exists in its module. It answers one `ResearchAssignment` with one `ResearchFindings`.
- **Both loops behave identically, and that is the contract, not a coincidence.**
  `run_supervised` and `run_agent` produce the same stop reasons, spend the same budget, make
  the same three memory calls and return the same report. A change to one that alters
  behaviour must be made to both, or it is a divergence rather than a refactor. What is
  written twice is only the ~25-line control skeleton; every per-hop mechanic is a single
  `runtime.py` function.
- **`single_agent.py` re-exports the moved names through `__all__`.** `main.py` and three test
  modules import `PreparationFailed`, `finalise`, `AgentProviders` and the stage functions from
  there. The `__all__` entry is also what tells ruff those imports are intentional.
- **The four prompts were not renamed and no schema changed.** `plan.md`,
  `research_step.md`, `sufficiency.md` and `finalise.md` already were one prompt per stage;
  the plan's `supervisor_decide.md` / `researcher.md` / `appraiser.md` naming would be churn
  against `finalise.md`'s frozen placeholder set. `AppraisalVerdict`'s
  `accepted[]` / `rejected[]` / `disagreements[]` fields were added by Day 5 T2 and given
  their per-source shape by T4.

- **The stop/continue decision is the Appraiser's, and it has exactly one definition (T5).**
  `supervisor._stop_after_hop` names a `StopReason` or returns `None` to continue;
  `supervisor._outstanding_followup` returns the question to spend, and is non-`None`
  **exactly** when the first returned `None`. The two are two readings of one verdict and a
  gap between them either strands the run or lets a stale verdict reach the planner, so the
  equivalence is pinned by `tests/unit/test_supervisor.py`. `single_agent.py` **imports** both
  from `supervisor` rather than restating them: it plays all three roles, but it must not
  answer a verdict differently from the way the Supervisor answers it.
  - **A hop the Appraiser asked for does not go past the planner at all.** `_followup_decision`
    builds the `SupervisorDecision` in code from `requested_followup`, so there is no model
    call in which a `FINALISE` could be produced over the top of an insufficient verdict.
    That is what "depends entirely on the Appraiser's verdict" (plan §8.3) means structurally
    rather than as a prompt instruction. The question is still never *written* by our code —
    it is the Appraiser's own text, verbatim; only `source_preference` (the schema default)
    and `reasoning` (a trace line) are supplied.
  - **The planner still owns the first question**, because there is no verdict to defer to
    before any evidence exists. `plan.md`'s "prefer the suggested follow-up" clause and
    `render_progress`'s verdict block are therefore now unreachable in a live run. Both are
    left in place deliberately: they are still correct, still covered by
    `test_prompt_context.py`, and removing them is a decision of its own rather than a side
    effect of this one.
  - **Every bound still applies to a forced hop.** `_stop_before_planning` runs *first*, so
    `MAX_HOPS` and the search/fetch ceilings cap a hop the Appraiser demanded exactly as they
    cap one the planner chose — which matters more now, since a verdict that always asks for
    more has no planner in between to decline it. `_can_afford_a_hop` is the replacement for
    the one guard skipping the planner removed: `decide_next_step` used to answer `None` when
    the ledger refused it, and without a substitute a forced hop would spend real searches and
    fetches and then have nothing left to judge them with.
  - **`sufficient` alone no longer stops a run.** The plan's condition is `sufficient` **and
    at least two accepted sources** (`_MIN_ACCEPTED = 2`); a thinner "yes" is the new
    `thin_evidence` stop — still a stop, because the verdict named no follow-up to spend a hop
    on, but a *cut-short* one whose reason reaches `unknowns`. This **supersedes T2's "the
    semantic lists inform, they never decide"** for `accepted` only; `rejected` and
    `disagreements` still only inform. `AcceptedSource`'s bare-string coercion carries more
    weight because of it — a model that names its sources without describing them still
    clears the bar.

- **The Appraiser judges; it never validates and never decides (T4).** The verdict is
  semantic — what a source supports, what it leaves open, its authority, why one was
  rejected — and nothing deterministic moved into it: Pydantic still checks shape,
  `validate_report` (S9) still checks the finished report against the URLs the run really
  gathered, and `_stop_after_hop` still reads only `sufficient` and `requested_followup`. A
  rejection reaches the report as an instruction in the finalise prompt, **not** as a
  narrowed grounding set: letting a model's reading delete a genuinely fetched URL from
  `evidence_urls` would spend the retry ladder undoing a true citation. If that guidance ever
  needs teeth, it is a new `ValidationCode` and a decision of its own, not an edit here.

- **The two per-source lists are the only nesting in a model-output schema**, one level deep
  — the depth `FocusPreparationReport.resources` has driven through both providers since Day
  1. Every field is defaulted and a bare string coerces to `{"source": …}`, because the
  failure being bought off is expensive: a reply that does not validate spends `_decode`'s
  one re-ask and then returns `None`, which `_stop_after_hop` reads as "the appraiser could
  not answer" and ends the run on.
- **`agent` spans exist as of Day 5 T6**, and this is where the loop first learns a trace
  exists. See *The cross-agent trace* below for the five span names, the parenting rule and
  what may not be added to them.

**The mode switch** (`config.AgentMode` + `service.py` + `main.py`, Day 5 T1):

- **`AgentMode = Literal["single", "multi"]` is a type alias with no `Settings` field.** The
  mode is a per-run choice a surface makes, not a deployment tunable; an `.env` value would be
  a second place the default lives and would let a stale environment decide which topology a
  demo runs.
- **`multi` is the default**, in `prepare_focus_session`'s signature and in `--mode`'s
  argparse default, and the two must agree.
- **Picking the loop is composition, so it lives in `service.py`.** Everything else — the
  connection, the `Tracer`, `build_tool_registry`, the providers, the budget, the `finally`
  that closes the run out — is assembled once *above* the choice and handed to whichever loop
  runs. That is what makes "only the reasoning topology differs" structural.
- **`--no-research` is unaffected.** It is a single round trip with no loop, so it has no
  topology and ignores `--mode`.
- **Both modes stay demonstrable.** `single` is a Week 4 deliverable in its own right and is
  the mode nothing else exercises by default, which is why
  `test_multi_agent.py::test_both_modes_produce_a_valid_report_from_the_same_inputs` is
  parameterized over both rather than testing only the new path.

**The entry point** (`service.py`, S11) — the one function a surface starts a run from. The CLI
calls it today and the Day 6 MCP server calls the same function; two surfaces each assembling
their own registry, providers and budget would be two places for composition to drift.

```python
async def prepare_focus_session(task, *, settings=None, registry=None, providers=None,
                                ctx=None) -> FocusPreparationReport
```

- **Composition only.** It resolves the four collaborators and calls `run_agent`. No prompt, no
  budget arithmetic, no retry, no error translation — all of those are the loop's, and
  `run_agent`'s optional parameters exist *for this module*. A change here that alters what a run
  does rather than what it is built from belongs in `agents/single_agent.py`.
- **`settings` is resolved once and threaded into all three defaults.** Letting each default reach
  for `get_settings()` would ignore a caller's override in two places out of three — and
  `--fully-local` and `--provider` are exactly such overrides.
- **`PreparationFailed` and `LLMError` pass straight through.** A surface has to show a user why a
  run failed, and a third exception type wrapping them would strand `PreparationFailed.issues`.
- **`RunState.validation_errors` is *not* surfaced here** — the S11 question is settled: it goes to
  **Day 4 tracing**. `run_agent` returns a report rather than the state, and widening that return
  type to carry a diagnostic would change the contract Day 6's MCP tool is specified against. The
  failure path is already served by `PreparationFailed.issues`; what is still missing is a trace of
  a run that was *corrected and then succeeded*, which is a tracing concern rather than a result
  one. Keep the field's lifecycle correct (set on a rule failure, cleared on a drift).

**The CLI** (`main.py`, S12) — a surface, not a second composition root.

- **Flat flags, no subcommand.** The open plan-vs-repository question is settled in the
  repository's favour: `evergrove-agent --task "…"`. A second word buys nothing while there is one
  thing to do; `tools/cli.py` keeps its subcommands because it genuinely has four tools to choose
  between.
- **Two paths.** `--no-research` is the single round trip against model knowledge alone — still the
  only mode where the task text never reaches a search provider. Everything else goes to
  `prepare_focus_session`.
- **A failed research run is never downgraded to a no-research one.** The two modes make different
  promises about what a report rests on; substituting one for the other after the fact would return
  a sourceless plan under flags that asked for sources. `PreparationFailed` → exit 1.
- **`--attachment` is wired**, and pre-flighted through `documents.resolve_attachment` before the
  run starts. Combining it with `--no-research` is refused: reading it is a tool call and that path
  makes none.
- **The progress line reads the ledger, not a callback.** `main.py` builds the `RunContext`, hands
  that same object to `service.py`, and a sibling `asyncio` task renders `RunBudget`'s counters on
  **stderr** while the run holds it. This is why `run_agent`'s signature needed no `on_event`
  parameter — and why the caller's `ctx` reaching the run unchanged is a tested invariant rather
  than an incidental one. Terminal-only (`isatty`) and `--quiet`-suppressible, so redirected output
  is byte-identical to a silent run; **stdout carries the report and nothing else**.
- **`documents.resolve_attachment(path, *, settings=None)`** was promoted out of
  `reader._resolve` for that pre-flight. The containment rule, the relative-path rule and the error
  codes keep one definition — a pre-flight that disagreed with the tool would be worse than none.

**Still open after S12:** `--no-research` does not go through S9's `validate_report`. It satisfies
`resources_without_research` and `unknowns_required` by construction, but `too_many_topics`,
`topic_overlap` and `goal_not_narrowed` go unchecked on that path. Extending the ladder to it is a
change to a shipped mode's behaviour and was deliberately kept out of the wiring work — decide it
on its own.

**`FocusPreparationReport`** is the most expensive schema in the project. Changing it forces
updates to the Day 3 finalise prompt, the Day 4 memory summary, the Day 5 Supervisor output, the
Day 6 MCP return type and every Day 7 evaluation.

---

## LLM architecture

`llm/base.py` holds the contract; nothing outside `llm/` talks to a model directly. Three
implementations:

- **`OllamaProvider`** (`name="ollama"`) — the local, mandatory, $0 path. POSTs `/api/chat`.
  Structured output is `format=<JSON Schema>`, i.e. constrained decoding: the model physically
  cannot emit JSON violating the schema. Sends `keep_alive` on every call so the model does not
  unload between calls. Timeout is `TOTAL_RUN_TIMEOUT_S`.
- **`HostedProvider`** (`name="hosted"`) — Google AI Studio (Gemini), opt-in. Also carries
  `to_gemini_schema()`, which translates a Pydantic JSON Schema into Gemini's OpenAPI-3.0 subset:
  inlines `$ref`/`$defs`, collapses `T | None` into `nullable`, drops keywords Gemini rejects.
  Nothing is lost that matters because Pydantic re-validates the reply. **Reuse this function —
  do not write a second translator.**
- **`FakeProvider`** (`name="fake"`) — replays a scripted list and records every call
  (`RecordedCall{messages, schema_name, tool_names, temperature}`), raising `ExhaustedScript` if
  asked for one more than scripted. This is why the whole suite runs offline. **Day 3's loop
  tests are built on it.**

`build_provider(role, settings=None, *, override=None)` is the only construction path;
`role ∈ {supervisor, researcher, appraiser}` resolves through `Settings.provider_for`. Adding a
provider is a branch here plus a class — never a change at a call site.

**Per-role selection is the whole mechanism (Day 5 T3), and it is configuration only.**
`SUPERVISOR_PROVIDER`, `RESEARCHER_PROVIDER` and `APPRAISER_PROVIDER` each resolve
independently through this one factory into `AgentProviders`, which `service.py` builds and
hands to whichever loop `mode` selects; a stage receives `providers.<role>` as an argument.
**No agent module reads a `*_PROVIDER` setting or constructs a client**, so pointing the
Appraiser at a different model — the independent semantic judgement the split exists for — is
an `.env` edit and nothing else. `single` mode uses the same three fields for the same three
stages: it inherits per-role selection from the identical composition path rather than
carrying a setting of its own, which is what keeps the two modes comparable. There is exactly
one override seam, the CLI's `--provider`, which writes all three settings *before*
composition, so precedence never becomes a question at resolution time.

**An unrecognised provider name raises here rather than defaulting to local.** `ProviderName`
catches a typo when `Settings` is built, but `model_copy(update=…)` and `setattr` — how
`tools/cli.py` and `main.py` apply overrides — bypass that, and a silent fall-through would
give an operator a local run they believed was hosted.

**`LLMError` is an exception, `ToolError` is a value.** Deliberate: an unreachable model is a
broken run, not something the agent can reason around. Preserve the asymmetry.

## Tool and registry architecture

- **One path to every tool.** Nothing calls a tool directly; everything goes through
  `ToolRegistry.call`. Day 3 must dispatch model tool calls through it too.
- **Few tools, enum-routed.** One `web_search` with a `source_type`, one `read_document` with a
  `mode` — a small local model's tool selection degrades as the menu grows. The routing behind
  each enum is deterministic Python.
- **Hooks are installed in exactly one place** — `install_registry_hooks`, called by
  `wiring.py` (T2/T3). What they do lives in `tools/hooks.py`, never in a tool and never in
  `registry.py`; the order they are added in is part of the behaviour and is decided there.
- **`build_tool_registry` builds a fresh registry per call** — no module-level singleton, because
  duplicate names raise by design. It is deliberately **not** re-exported from `tools/__init__.py`
  (which imports only `base` and `registry`), so importing `RunContext` does not drag in `httpx`,
  `sqlite3`, `pypdf` and the search backends.
- **`TOOL_NAMES` is declared, not derived**, so a finished-but-unwired tool is a failing assertion
  rather than a silently lost capability.
- **Registered ≠ advertised.** `normalize_sources` is a pipeline step registered so a trace shows
  what normalisation discarded. **Which subset is advertised to a model is Day 3 Subtask 2's
  decision**, and `wiring.py` explicitly defers it.
- No DI container, no plugin discovery, no auto-import scanning: the menu is explicit and
  reviewable.

## Search architecture

`_select_backend(source_type, configured)` is a **pure function** in `tools/web_search.py`; the
factory `build_search_backend(name=None, settings=None)` in `search/__init__.py` is the one place
a `SEARCH_BACKEND` value becomes a class.

- **`fixture` resolves to `fixture` for every source type** — an offline run must be provably
  offline, and a routing rule with an exception is not provable.
- Otherwise `academic` intent goes to the free `academic` backend; everything else uses the
  configured backend untouched. **A query is never redirected onto a metered backend the user did
  not choose.**
- The backend name is resolved **before** the cache read, because it is part of the cache key — a
  recording must never answer a `serpapi` query.
- **Failure ladder:** cache → one retry with 2 s backoff (only for `retryable=True`, only on the
  first rung) → fall back to `source_type="general"` (one further attempt) → `ToolError(
  SEARCH_UNAVAILABLE)`. Never an exception. The error reported is the **first** failure, because
  it describes the search actually asked for.
- Fallback results are cached **under the source type that answered**, never the one requested.
- Backends: `FixtureSearchBackend` (self-describing recordings; a corrupt/missing/duplicated
  fixture is a loud `SearchBackendError`, never an empty list), `SerpApiSearchBackend`
  (401/403/429 → `retryable=False`; 5xx/network → retryable; `"Fully empty"` is an empty list,
  not a failure), `AcademicSearchBackend` (**one** backend trying OpenAlex → Crossref → arXiv in
  order, first answer wins, so a normal search is one HTTP call; OpenAlex abstracts are rebuilt
  from `abstract_inverted_index`).
- No retry, backoff or fallback **inside** a backend; that ladder belongs to the tool.
- **Ranking** is `search/normalize.py` + `search/domains.py`/`domains.json`: canonicalise → drop
  unusable → dedupe → classify → stable authority sort, so official docs re-rank above blogs.
  `domains.py` is the **only** loader and classifier (longest domain suffix wins) — never copy the
  map. Canonicalisation is deliberately conservative: lowercase scheme/host, drop fragment, default
  port, trailing slash, `utm_*` and named tracking params, nothing else; query order is preserved
  and `www.` is **not** stripped from the URL, only during authority lookup.

## Fetching and document processing

`fetch_url` flow: `canonicalize_url` → `get_cached_source` → (miss) streamed `httpx` GET →
content-type routing → `store_cached_source` → `select_passages`. **A cache hit and a fresh fetch
end in the same shaping function**, so one URL and one question give one answer either way.

- One URL per call; redirects followed; `final_url` reports where it landed while the cache stays
  keyed on the **requested** canonical URL. No crawling, no link-following.
- Exactly one retry, only when the failure is `retryable` (timeout, network, 5xx, 429). A 4xx is
  never asked twice. Nothing that failed is cached.
- A `sqlite3.Error` on either cache path is **non-fatal but logged** at WARNING — the page is still
  returned.
- **No headless browser, no JS rendering**: a client-side-rendered page returns `EMPTY_FILE`, by
  design.
- `MAX_FETCH_CALLS` is **not** enforced here — that is the registry pre-hook
  (`enforce_run_budget`, T3), which claims `fetch` before this tool is ever reached.

`documents/`:

- `read_document_file(path, *, settings=None)` — path guard → size guard → suffix routing over
  `.txt`/`.md`/`.pdf`/`.docx`. `path` resolves inside `ALLOWED_ATTACHMENT_DIR`; **containment is
  checked before existence**, so a probe outside cannot learn what is there. Readers raise
  `DocumentReadError`; the tool converts it once.
- **The outline is never guessed** — Markdown ATX/setext headings, Word heading styles, PDF
  bookmarks. A `.txt`, a style-less `.docx` and a bookmark-less PDF all get an empty outline, and
  `section` then falls back to keyword selection over the whole text rather than slicing at an
  invented boundary.
- `select_passages(text, question, *, max_chars=None)` (`documents/excerpt.py`) — the deterministic
  passage selector: keyword overlap only, no model, embeddings, network or database, so the same
  page and question always give the same excerpt. **Both readers and `fetch_url` end in this
  function**; do not add a second selection path.
- `extract_html(markup) -> (title, text)` (`documents/html.py`) — stdlib `html.parser`. Drops
  `script`/`style`/`nav`/`header`/`footer`/`aside`/`form` subtrees and emits blank-line-separated
  paragraphs with Markdown `#` headings and four-space-indented `<pre>`, which is exactly the shape
  `select_passages` splits and scores.
- PDF bytes from the network reach `read_pdf` through a file in a `TemporaryDirectory`;
  `read_document_file` cannot be used because its guard would answer `PATH_NOT_ALLOWED` for
  downloaded bytes. **Do not add a second PDF path or a second HTML extractor.**

## SQLite, caches and the quota guard

One file, stdlib `sqlite3`, no ORM, no Redis, no second database. `memory/db.py` owns **all DDL**
in `SCHEMA_STATEMENTS` (`schema_meta`, `source_cache`, `search_cache`, `search_budget`, Day 4 T1's
`runs`, `spans` + `idx_spans_run`, and T4's `prep_memory` + `idx_prep_task_key` and `run_memory` +
`idx_run_memory_run`); `SCHEMA_VERSION` is **still 1** — it marks a change to an
existing table's *shape*, and T1 and T4 only appended new tables. Verified against the real
populated file twice: after T1 and again after T4, the new tables and indexes appear and every
existing row count is unchanged (`runs` 3, `schema_meta` 1, `search_budget` 1 row, `search_cache`
16, `source_cache` 9, `spans` 0).
`connect()` sets `row_factory = sqlite3.Row` and the pragmas `foreign_keys=ON`,
`journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`. `initialize_schema` is idempotent;
`open_database()` runs it on every open. Multi-statement writes go through `transaction(conn)`.
Feature modules own only their queries — `db.py` stays free of tools, models, HTTP and backends.

- **Source cache** (`memory/cache.py`) — keyed on `canonicalize_url(url)` on both get and store.
  **Reads never write**: an expired row is left in place and overwritten by the next store, so
  there is no purge sweep. `INSERT OR REPLACE`, so refreshing and first-caching are one call.
  Expiry is compared in Python against an injected `now`, the only test seam. A missing row, an
  expired row and an uncanonicalizable URL are all one answer: `None`.
- **The cache stores the whole extracted source text**, bounded only by `MAX_SOURCE_TEXT_CHARS` —
  never raw HTML, never an excerpt, never a reading budget. Neither `SOURCE_EXCERPT_CHARS` nor the
  caller's `max_chars` may bound it: both describe *one answer*, and trimming to either would make
  a later, differently-worded question re-read the previous question's paragraphs from a page that
  is never re-fetched to correct it.
- **Search cache** (`memory/search_cache.py`) — key is `sha256` over
  `normalized_query \x1f backend \x1f source_type \x1f max_results`, the only four inputs that
  change what a backend returns; `max_results` stays *in* the key. Payload is JSON `RawSource`. An
  **empty result list is cached like any other**, and a row from an older build degrades to a miss
  rather than taking a tool down.
- **Quota guard** (`memory/budget.py`) — check and increment are **one atomic SQLite statement**
  (`INSERT … ON CONFLICT(month) DO UPDATE SET used = used + 1 WHERE used < ? RETURNING used`), so
  two callers cannot both spend the last call; a returned row *is* the grant. Month is UTC
  `YYYY-MM`. **Reserve before the call; there is no refund path** — a search that times out may
  still have counted at the provider, so over-counting is the safe direction.
  `QUOTA_CONSUMING_BACKENDS = {"serpapi"}` only.
- **Preparation memory** (`memory/prep_memory.py`, Day 4 T4) — `prep_memory`, plan §12.2's columns
  plus `interpreted_goal`. Rows are **appended**, not replaced, and never deleted: recall takes the
  newest within the window and an aged row stays for audit. See *The memory contract* above.
- **Session memory** (`memory/run_memory.py`, Day 4 T4) — `run_memory`, plan §12.2's columns. One
  batch per hop in one transaction, `run_id` from `RunContext`, no foreign key to `runs` for the
  same reason `spans` has none. It is a mirror of `RunState`, never a second copy of it.
- **Traces** (`tracing/store.py`, Day 4 T1) — `runs` and `spans`, exactly plan §12.2's columns.
  `spans.run_id` carries **no foreign key** to `runs.run_id`, matching the plan: `foreign_keys=ON` is
  set, so a key would make a span unwritable in the one case a trace is most wanted — the one where
  the run header failed to write. `ended_at`, `ok`, `from_cache` and `duration_ms` are nullable
  because a row is written when an operation *starts*. `get_spans` orders by `started_at` then
  `rowid`: two operations can start inside one millisecond and insertion order is the only honest
  tiebreak.
- **The search-cache read happens before the quota check**, so a cached query costs neither network
  nor quota. A budget ledger that cannot be read **refuses** the live call as `SEARCH_UNAVAILABLE`
  (never `MONTHLY_BUDGET_EXCEEDED`, which would claim to know something unreadable) — the one place
  a `sqlite3.Error` is not degraded to "carry on".

## Configuration

One file (`config.py`) plus `.env.example`. **New budgets, TTLs and paths go there — never
inline.** An empty `.env` is a valid, fully local, $0 configuration.

**Consumed today:** `OLLAMA_HOST`, `LOCAL_MODEL`, `LOCAL_KEEP_ALIVE`, `NUM_CTX`, `HOSTED_MODEL`,
`HOSTED_API_BASE`, `GOOGLE_API_KEY`, `*_PROVIDER`, `TEMPERATURE`, `SEARCH_BACKEND`,
`SERPAPI_API_KEY`, `MONTHLY_SEARCH_BUDGET`, `SEARCH_TIMEOUT_S`, `SOURCE_EXCERPT_CHARS`,
`MAX_SOURCE_TEXT_CHARS`, `MAX_DOCUMENT_BYTES`, `MAX_FETCH_BYTES`, `FETCH_TIMEOUT_S`,
`TOTAL_RUN_TIMEOUT_S` (as the Ollama client timeout), `CACHE_TTL_DAYS`, `SEARCH_CACHE_TTL_DAYS`,
`DB_PATH`, `ALLOWED_ATTACHMENT_DIR`, `SEARCH_FIXTURE_DIR`, `TRACE_SUMMARY_CHARS`.

**`TRACE_SUMMARY_CHARS=200`** is the only setting Day 4 added (T1) — the ceiling on a span's
`input_summary` / `output_summary`. Everything else Day 4 needs was already sized.

**`MEMORY_RECALL_MAX_AGE_DAYS=30` is consumed as of T4**, by
`prep_memory.recall_previous_preparation` when a caller passes no `max_age_days`. No setting was
added for memory: the stored-line ceiling is a structural bound in `run_memory` (2000 chars, the
same as `ResearchFindings.notes`, which is the longest thing that becomes a row), not a tunable.

**Declared but not yet consumed — these are Day 3's and Day 4's budgets, already sized:**
`MAX_HOPS=3` (**raised from the plan's 2 on request, Day 3**; 3 is also the ceiling
`FocusPreparationReport.hops_used` allows, so any further raise means changing the most expensive
schema in the project), `MAX_SEARCH_CALLS=3`, `MAX_FETCH_CALLS=4`, `MAX_SOURCES_KEPT=3`,
`MAX_MODEL_CALLS=10`, `MAX_OUTPUT_RETRIES=3`, **Day 3 must consume these, not invent new ones.**

`SEARCH_BACKEND=fixture` stays the committed default in `Settings` and `.env.example`. Never
change it to make a test pass.

## Testing and offline strategy

**458 offline test cases, plus 1 `live`-marked test** (a real-Ollama round trip in
`test_llm_provider.py`; this count drifts — it has been recorded as ~356, 400, 420 and 432 before,
so re-measure rather than trusting it). The newest are T4's 26: 15 in `tests/unit/test_memory.py`
(the round trip, the task-key equivalence class and its negative, both sides of the age window, the
config default, newest-wins, a corrupt row degrading to a miss, hop-1 rows surviving hop 2, the
seen-query/URL sets, and one run never seeing another's memory), 8 in
`tests/unit/test_memory_tools.py` (the empty-but-successful recall, the wired save→recall round
trip, `run_id` coming from `ctx`, a dead database answering with a `ToolResult` from all three
tools, none of them advertised to any role, and the storage layer still raising so the guard is not
dead code), and 3 appended to `tests/unit/test_single_agent.py` (a validated run leaves both
memories, a failed run leaves no preparation but keeps its hop history, and a wholly broken memory
layer still returns the report). One existing test moved with them:
`test_a_retry_rewrites_the_report_without_researching_again` filtered its call-list assertion to
`web_search`/`fetch_url`, because the guard is about spending quota during finalisation and a local
SQLite write spends none. Before them, the 10 in `tests/unit/test_registry_hooks.py`
(T2/T3) — the span open before the tool and closed after, tool-span parenting, the recorded row
against the result, two calls paid and the third refused for each budgeted tool, an uncharged
free tool, the refusal itself traced, a cache hit on the span, and both failure endings. Before
them, the 8 in `tests/unit/test_tracing.py` (Day 4 T1)
— parent derivation across all four nesting shapes, id uniqueness and stack unwinding, an
out-of-order close, both round trips, the caller-supplied-vs-derived duration, a closed connection
proving a trace failure never reaches the run, and the summary bound. Before them,
the 8 in `tests/integration/test_single_loop.py` (S13) — the whole run driven through
`prepare_focus_session` against the **committed** `fixtures/search/` tree and the real SQLite file,
proving what no unit suite structurally can: that the composition holds, that the shipped offline
default never moves the live-search counter, that a second hop is genuinely derived from what the
first one read, and that the run finishes well inside the 2-second acceptance budget (0.87 s for the
file). Before them, the 11 in `tests/unit/test_single_agent.py` (S5–S8) —
which assert every exit from the loop, the finalise reserve, and that a guessed tool name, a
refused budget and an unavailable search all stay recoverable — after the 13 in
`tests/unit/test_run_budget.py` (S4), after the
9 in `tests/unit/test_prompt_context.py` (S3), the 18 in `tests/unit/test_tool_calling.py` (S2) and
the 15 in `tests/unit/test_agent_schemas.py` (S1). S4's suite drives an injected `FakeClock`, so the
900-second timeout is proven without waiting for it. S3's suite asserts no prompt *wording* — wording is the
part of a prompt that stays safe to change; what it pins is the placeholder set, the
read/unread distinction, the excerpt bound and that a degraded run stays visibly degraded. Unit suites in `tests/unit/`, composition suites in `tests/integration/`
— a failure there means the *composition* broke, not a unit.

- Offline by default: `FakeProvider`, `respx` for HTTP, the fixture search backend, recorded
  fixtures, and `Settings(_env_file=None)` (the `settings` fixture). Tests point `DB_PATH` at a
  temporary path — never the real cache or ledger.
- **Prove behaviour at the cheapest level that can prove it:** code inspection → focused unit test
  → mocks and fixtures → offline integration → live call. Reaching a live call without the levels
  below it passing is a rule violation.
- Anything needing a real model, key or network carries `@pytest.mark.live`, which keeps it out of
  the default run (`addopts = "-m 'not live' --strict-markers"`) and out of the pre-push gate.
- **`.githooks/pre-push` runs `ruff check .` then `pytest -q`.** Never add a check needing a model,
  a key or the network, and never add a marker override — the exclusion lives once, in
  `pyproject.toml`.
- **Test value over count.** Each new test names the bug or regression it catches; parameterize
  variations instead of copying them. No test for trivia, framework behaviour, or coverage.
- SerpAPI: 250/month total, `MONTHLY_SEARCH_BUDGET=200`. Never repeat an identical live query;
  check the cache and `fixtures/search/` first. **Every successful live search response is recorded
  into `fixtures/search/` in the same session** — an uncaptured live search burns quota twice.
- Fixture provenance is **file-level, never per result**: `recorded_from` lives on the recording
  and `source_backend` stays `"fixture"`. Never hand-edit a recording to make a test pass.

## Engineering decisions (do not relitigate)

1. **One path to every tool** — `ToolRegistry.call`, always.
2. **Tools never raise**; a failure is a `ToolResult` with a specific `ErrorCode`. Providers *do*
   raise (`LLMError`, `SearchBackendError`) — the failure ladder is the caller's control flow.
3. **Few tools, enum-routed**, because small-model tool selection degrades as the menu grows.
4. **One place installs the hooks** — `tools/hooks.py` decides what they do, `wiring.py` says
   that an assembled registry has them.
5. **`schemas/` imports nothing** from the package; everything imports it.
6. **Deterministic first** — no model, embeddings or vector store anywhere in the tool layer.
7. **Storage is one SQLite file**, stdlib `sqlite3`, no ORM, no migration runner.
8. **`fixture` is the default search backend**, which is what makes development free.
9. **Config is one file.**
10. **Injection seams over globals** — tools take `(settings=None, *, client/backend=None,
    connection=None)`; given none they resolve their own. `now` is injected for anything
    time-dependent.
11. **Composition roots stay logic-free** — `wiring.py` and `cli.py` build and dispatch, they never
    decide behaviour.
12. **Implementation first; every live run is Day 7's.** Standing instruction from the user
    (2026-08-17). Acceptance runs, evaluations and any other model-, network- or quota-spending
    verification are **consolidated into Day 7** rather than run at each day's boundary — including
    the ones Days 3 and 4 nominally owe. A day is therefore "implementation complete" when its
    subtasks are built and covered offline, and a milestone is not blocked from starting because an
    earlier one has unspent live runs. **This does not weaken the offline gate:** the cheapest test
    that can prove a behaviour still runs at the time the behaviour is written
    ([`offline-first-development.md`](../.claude/rules/offline-first-development.md) §2), and
    `.githooks/pre-push` is unchanged. What is deferred is only what costs a model, a key, the
    network or quota.

## Verified deviations from the 7-day plan

The repository is authoritative. **Do not "restore" the plan's version of any of these.**

| Plan | Repository | Why |
| --- | --- | --- |
| `trafilatura` for HTML extraction | stdlib `html.parser` in `documents/html.py` | Recorded departure on the user's call. Revisit only if fixtures show the output is too noisy; the swap is `extract_html`'s body and no caller's business |
| `domains.yaml` allow-list | `search/domains.json` | PyYAML is not worth adding for a static file |
| `read_document` handles `.txt`/`.md`/`.pdf` | **`.docx` too**, via stdlib `zipfile` + `ElementTree` reading `word/document.xml` | Added to scope on request. No `python-docx`, no `lxml` |
| `fetch_url` empty extraction → `NO_CONTENT` | `EMPTY_FILE` | Reuses an existing code rather than adding a near-duplicate |
| `ddgs` as the keyless fallback rung | **Deliberately not implemented**; `build_search_backend` refuses it and `web_search`'s ladder has no such rung. It remains in the `SearchBackendName` literal and in CLI `--choices` | A backend that merely looks right is worse than one that refuses |
| Four *agent-callable* tools: `web_search`, `fetch_url`, `read_document`, `recall_previous_preparation` | Eight *registered* tools; the model is offered only `web_search`, `fetch_url` and `read_document` | `normalize_sources`, `validate_report` and T4's three memory tools are pipeline tools, registered for traceability, and are **not** on the model's menu |
| The Supervisor *chooses* to call `recall_previous_preparation` (plan §12.2) | Recall is a deterministic call our own code makes, **once, in `run_agent`, before the first decision** (T5); the tool is registered and never advertised | The supervisor has **no tool menu** in the shipped design (S2): planning is a `generate(schema=…)` call, and handing it `format` and `tools` in one Ollama payload is exactly what S2 avoided. The recalled preparation reaches the planner as prompt text instead, which gets the same influence for no reliability cost |
| `save_preparation` is one of the pipeline tools, saved "after validation passes" | Same, and the caller is **`run_agent`, on the value `finalise` returned** | That is structurally the only validated report in the system, so the rule needs no flag and no second grounding check |
| `prep_memory` stores `session_objective` | Stores `interpreted_goal` **as well** | They answer different questions — the narrowed slice versus what that session was to achieve — and a continuation needs the goal. One short text column |
| `recall_previous_preparation` "returns found=false on any failure" | `found=false` for no match; a **failing `ToolResult`** (`UNKNOWN`) for a storage failure | The guarantee ("a memory outage must never fail a run") is kept — nothing raises. But reporting success after a failed read would make this the only tool in the project that hides a storage failure, and would make every first run indistinguishable from an outage in the trace |
| `task_key` is "lowercase, stopwords removed" | Also **deduped and sorted** | Word order carries no subject information: without sorting, `"Indexing in PostgreSQL"` would not match `"PostgreSQL indexing"`. The readable title is kept in `original_task` |
| Day 4 creates `memory/run_memory.py` as *the* session memory | `RunState` remains the session memory the loop runs on; `run_memory` is its durable per-hop mirror | The loop already carried `RunState` across hops from S8, and the task's own constraint was not to build a second competing state model. Nothing reads a decision back out of SQLite |
| Day 3 demo CLI `evergrove-agent prepare --task …` (a subcommand) | `main.py` uses flat flags with no subcommand; the tools CLI is a separate module | **Settled in S12: flat flags stay.** A subcommand buys nothing while there is one thing to do; `tools/cli.py` keeps its subcommands because it genuinely has four tools to choose between |
| `validate_report` listed under Day 2 in `README.md` | Not implemented; plan §23 feature 5 places it in the **Day 3** loop | The plan wins over the README here |
| `MAX_HOPS = 2` | **`MAX_HOPS = 3`** in `config.py` and `.env.example` | Raised on the user's explicit instruction during Day 3 S1. It is the ceiling: `FocusPreparationReport.hops_used` is `le=3`. Costs one more possible hop's worth of searches and fetches per run |
| `SearchSourceType` defined in `search/base.py` | Defined in **`schemas/tools.py`**, re-exported unchanged from `search/base.py` | The Supervisor's `source_preference` is the same enum, and `schemas/` may import nothing from the package — so the definition had to move to the layer both sides can see. Every existing importer is unchanged |
| `schemas/agents.py` reuses `NormalizedSource` for the agent's sources | A distinct `GatheredSource` | Reuse is impossible (`search/normalize.py` imports `schemas`) and wrong: a search hit is not the same thing as a source that was opened and read |
| Day 4 creates `tracing/context.py` | **`RunContext` stays in `tools/base.py`**, extended in place with the span stack; `tracing/` holds `store.py`, `tracer.py` and `render.py` | Moving it would touch the registry, every tool, every agent, `service.py` and `main.py` for zero behaviour change. The DDL still goes in `memory/db.py`, whose own docstring specifies exactly that for tracing |
| The post-hook's JSON trace line goes **to stdout** (§13) | Emitted through the **`logging`** module on the `evergrove_agent.trace` logger, and `main.py --trace-log` attaches a handler on **stderr** (T6) | `main.py` documents stdout as the report and nothing else — "a piped or redirected report is byte-identical to a silent run" — so a JSON line printed there would corrupt the one output this project promises to keep clean. §13's own chosen mechanism is "stdlib JSON logging", where the destination is a handler's business; only the destination moved. **Silent by default**: no handler, no lines |
| The JSON trace line is part of the span write, so it needs a `Tracer` | `TracingHooks` is installed **unconditionally**; `tracer` decides whether span *rows* are written, not whether the call is observed (T6) | The line needs no database, and a run whose SQLite file could not be opened is precisely the run whose record is most worth keeping. With no tracer the line carries `span_id: null` |

## Reuse and dependency guidance

**Before creating anything, check this table.** Duplicating one of these is the most likely way a
future session damages the project.

| Need | Already exists — reuse it |
| --- | --- |
| Talk to a model | `llm.build_provider(role)` → `LLMProvider.generate` |
| Advertise a tool to a model | `agents.advertise(registry, role, has_attachment=…)` — never build a `ToolSpec` at a call site |
| Run the tool a model asked for | `agents.dispatch` / `agents.dispatch_all` — never `registry.call` straight from a `ToolCall` |
| Run a tool (our own code, not a model's request) | `tools.wiring.build_tool_registry()` → `registry.call(name, args, ctx)` |
| Pydantic → model-facing JSON Schema | `Model.model_json_schema()`; for Gemini, `hosted_provider.to_gemini_schema` |
| Search the web | the `web_search` tool (never a backend directly, never a new backend for a new source type) |
| Read a page or PDF by URL | the `fetch_url` tool |
| Read a local attachment | the `read_document` tool |
| Rank/dedupe/classify URLs | `search.normalize_sources` / `canonicalize_url` / `classify_domain` |
| Trim text to what a model should see | `documents.select_passages` |
| Cache a page or a search | `memory.cache` / `memory.search_cache` |
| Remember a validated preparation | the `save_preparation` tool (never `prep_memory.save_preparation` from a run — the tool is the guard) |
| Find what a previous run prepared | the `recall_previous_preparation` tool — never a second lookup path, and never a `SELECT` of your own. In a run it is already called once by `run_agent`; read `RunState.previous` rather than looking it up again |
| Tell a stage what an earlier session prepared | `agents.render_previous_preparation` (planner) / `agents.render_continuation_note` (report) — never phrase a continuation at a call site, and never render `source_urls` |
| Normalise a task title for matching | `memory.normalize_task_key` — the one definition; a second one silently stops matching every row on disk |
| Record what this run has done | the `record_run_memory` tool + `memory.entries_from` — the one place a string becomes a `RunMemoryKind` |
| Read a run's own history back | `memory.get_run_memory` / `seen_queries` / `seen_urls` — for an audit or a test, never for a decision |
| Spend or check live-search quota | `memory.budget.reserve_search_call` |
| A prompt | `llm.prompts.render_prompt(name, **values)` + a new `.md` file |
| A prompt's placeholder text | `agents/prompt_context.py` — one renderer per placeholder; never build a block at a call site |
| Show a model the sources a run gathered | `agents.render_sources` (full, for judging) / `agents.render_research_context` (compact, for finalising) |
| Show a model what a tool answered | `agents.render_tool_outcome` — code, message and retryability included |
| Start a run from a surface (CLI, MCP) | `service.prepare_focus_session(task)` — never assemble a registry, providers and a budget at a surface |
| Run the whole agent | `agents.run_supervised(task, registry=…)` (multi) or `agents.run_agent(task, registry=…)` (single) — never call the four stages in sequence yourself, and never write a third loop |
| Start a run in a specific topology | `service.prepare_focus_session(task, mode="single" \| "multi")` — never import a loop at a surface |
| Hand one worker's output to the other | `supervisor._delegate_hop` — the only channel between the Researcher and the Appraiser; never let a worker import its sibling |
| Check an attachment path before using it | `documents.resolve_attachment` — never a second existence or containment check |
| One reasoning stage | `agents.decide_next_step` / `run_research_step` / `judge_sufficiency` / `finalise` |
| A model for a role, in a test or a run | `agents.AgentProviders` — `.from_settings()` in production, the three-argument constructor in tests |
| Spend a model call in a non-final stage | `single_agent._claim_reasoning_call(budget, reserve=…)` — never `budget.claim("model_call")` directly outside `finalise` |
| Charge a tool call to the ledger | `single_agent._claim_for_tool` — the one place a tool name becomes a `BudgetKind` |
| Say why a run stopped short | `agents.render_stop_reason` — never phrase a limit at a call site |
| The session-sizing rule | `agents.prompt_context.max_topics_for` |
| A tunable value | `config.Settings` + `.env.example` |
| A test without a model | `FakeProvider`; with HTTP, `respx`; with search, `SEARCH_BACKEND=fixture` |
| A message between reasoning stages | `schemas/agents.py` — never a dict, never a new parallel model |
| A source the agent gathered | `schemas.GatheredSource` (not `NormalizedSource`, which is a search hit) |
| "What has this run seen?" | `RunState.evidence_urls` / `fetched_urls` / `used_queries` / `all_sources` |
| "What has this run spent?" / may it spend one more? | `RunContext.budget` — `claim(kind)` / `remaining(kind)` / `exhausted_limits`; never a counter of your own |
| A span id, and what it nests under | `RunContext.begin_span()` / `end_span()` — never mint an id or derive a parent at a call site |
| Write a run or a span to the trace | `tracing.Tracer` — `start_run` / `finish_run` / `open_span` / `close_span`; never call `tracing.store` from a hook, and never guard a trace write yourself |
| Trace an operation a single frame opens and closes | `tracing.agent_span(tracer, ctx, name)` — it closes on the failure path too, and is a no-op without a tracer. Never pair `open_span`/`close_span` by hand outside a hook |
| Read a run's trace back | `tracing.get_run` / `tracing.get_spans` |
| Show a run's trace to a person | `scripts/show_trace.py <run_id>`, or `tracing.render_trace(run, spans)` for the lines — never a second tree builder, and never a `SELECT` of your own |
| Log what a tool call did | nothing to add — `TracingHooks.after` already emits the JSON line for every call. Never log a tool call from inside a tool |
| The source-type enum | `schemas.SearchSourceType` — one definition, re-exported by `search/base.py` |

**What later days depend on:**

- **Day 4** (memory, hooks, tracing) — **T1–T6 done**: the span stack and `tracing/`, the registry's
  hook chains, budget enforcement in the pre-hook, both memories with their three tools, the
  recalled preparation reaching the planner and the report, and the trace renderer plus the JSON
  log line. **Still to do:** the acceptance run, which is live-model work.
- **Day 5** — **T1 done.** Day 3's four functions now live in `agents/supervisor.py`,
  `agents/researcher.py` and `agents/appraiser.py` over a shared `agents/runtime.py`, and it was
  a file move rather than a rewrite exactly as planned, because each stage already took and
  returned a Pydantic model. Workers reuse the Day 2 tools unchanged through the registry; no
  tool is re-implemented inside a worker. `single_agent.py` is retained and `--mode` selects.
  **Still to do:** cross-agent `agent` spans (T6).
- **Day 6** (MCP) wraps `service.py`. The MCP tool's return type is `FocusPreparationReport` and its
  input mirrors `TaskContext`.
- **Day 7** runs five evaluations over all of the above.

## Known limitations and not yet implemented

**Missing (Day 3 still builds these):** nothing — S14 ran. **Two more acceptance runs are owed and
banked for Day 7**; see below.

**Memory is now written *and* read (T4/T5).** A run recalls before it plans, saves after it
validates, and mirrors each hop. What is **not** proven is what a real model does with the
continuation: whether `qwen3:4b` actually narrows to a deferred topic and says so in
`interpreted_goal` is a live question, and the offline suite can only show that the instruction and
the previous topics reach both stages. **A run that ignores the continuation still produces a valid
report** — by design, since nothing enforces it — so a disappointing Day 4 acceptance run is a
prompt-wording problem, not a wiring one, and `plan.md` / `render_continuation_note` are where to
look.

**No live run has exercised either memory.** Both tables were verified against the real populated
database file and every path is covered offline, but no 9-15 minute Ollama run has written a
`prep_memory` row yet, and no second run has recalled one. The Day 4 acceptance criterion ("run 2
covers different topics from run 1 and says so in `interpreted_goal`") and plan Evaluations 5 and 6
are where that gets proven — and it needs **two consecutive runs on the same task title**, since
the first only writes.

## S14 results — the first live runs (2026-08-16/17)

**Hardware reality, which shapes everything here:** 8 CPU cores, **no GPU**. `qwen3:4b` measures
**~4.5 tok/s generation, ~15–50 tok/s prefill**. A full run is **9–15 minutes**. That is this
machine's measurement, and it is the number to plan Day 4–7 against — not the plan's estimates.

**Success rate: 3 valid reports from 3 runs on the final code.** The criterion is *≥4 of 5*, so
**it is not yet satisfied — the sample is smaller than the criterion specifies**, by instruction
rather than by failure. Two more runs settle it. Do not record Day 3 as accepted until they pass.

| Criterion | Result |
| --- | --- |
| Valid report on ≥4 of 5 attempts | **3/3 valid — sample incomplete** |
| A second hop with a query derived from hop 1's content | **Met** |
| Every cited URL actually fetched in that run | **Met — 3 of 3 citations, audited against `source_cache`** |
| Offline `FakeProvider` suite under 2 s | **Met — 1.63 s** |
| No budget can be exceeded | **Met** |

**The multi-hop evidence, because it is the one nobody could assert from a unit test.** Run 3
(`Learn what a PostgreSQL B-tree index is`, 923 s, `hops_used=2`) read
`postgresql.org/docs/current/btree.html` in hop 1 — a page containing "page split" **9 times** —
and hop 2 then searched `postgresql b-tree index page splits and merges documentation`. The
follow-up came from what the run *read*, not from a reworded task title.

**Observed model behaviour worth keeping:** hops are triggered by *a source raising a question the
model cannot answer*, not by topic breadth. The broad task (`Learn PostgreSQL indexing`) stopped
at one hop; the narrow one took two. Sizing a task's expected hops by how big it sounds is wrong.

### The five defects S14 found — all fixed, none found by the offline suite

1. **The thinking tax.** `qwen3` reasons into `message.thinking`, which `format=` does not
   constrain and `OllamaProvider` discards. One trivial prompt: **173.9 s with thinking, 5.1 s
   without**, and prefill fell 547 → 16 tokens because the scaffolding leaves the template with
   it. Fixed by `"think": False` in the payload.
2. **A timeout reported as unreachable.** `httpx.TimeoutException` fell into the generic
   `HTTPError` branch and stringifies to `""`, so a 900 s timeout printed *"could not reach Ollama
   at http://localhost:11434: "* while the server was demonstrably healthy. Now a separate branch
   naming `TOTAL_RUN_TIMEOUT_S`.
3. **Free-form tool calling cost 8× a constrained call** — 361 s vs 46 s. The calls were
   *correct*; nothing bounded the ~4 000 characters of prose in front of them. **Contingency
   option (2) spent** — see *The research decision* below.
4. **Tool argument schemas stopped reaching the model** — a regression introduced by (3), since
   `generate(tools=specs)` was what put them on the wire. Every `fetch_url` came back
   `BAD_ARGUMENTS` and a run cited a page it never opened. Fixed in `render_available_tools`.
5. **The model re-issued a byte-identical `web_search` until its search budget was gone**, never
   reaching `fetch_url`. The prompt is rendered *once*, so `{allowance}` and `{already_covered}`
   described the hop's opening state forever; the model decided turn 3 with turn 1's facts. The
   cache answered the repeats, so it cost no SerpAPI quota and stayed invisible in the ledger while
   still losing the hop its evidence. Fixed by `render_turn_state`.

**Every one of these was found by a live run. The 401-case offline suite was green throughout.**
That is the standing lesson: this suite proves the loop's *decisions*, not its *cost* or what a
real model does with an unconstrained turn.

### The research decision (`ResearchAction`) — contingency option (2), spent

A research turn is now one constrained `ResearchAction{tool, arguments, reasoning}` instead of
free-form tool calling. `dispatch` still takes a `ToolCall` and arguments still travel as a raw
mapping, so `registry.call` remains the only argument validator and there is **no second tool
path** — which is exactly why the plan called this contingency free. Invariants:

- **`tool` is a plain `str`, never a `Literal` of the tool names.** `advertised_tool_names` stays
  both the advertisement and the allow-list; a second copy of the menu would drift the moment an
  attachment changes what is offered. An invented name is still a `ToolResult(UNKNOWN)` from
  `dispatch`, before the registry.
- **`arguments` is `dict[str, str | int | float | bool]`** — every argument the advertised tools
  take is a scalar, so nothing is lost and constrained decoding gets a real value grammar.
- **A drifted reply ends the hop** rather than re-asking: `_decode`'s retry belongs to stages whose
  answer steers control flow, and a hop that stops early still returns its findings.
- **One tool call per turn.** A claim has to sit between one call and the next.

### `render_available_tools` now renders arguments, not just names

Its old contract said names only, because "the descriptions and argument schemas already travel
with the specs". **That premise died with `generate(tools=specs)`** — see defect (4). It now emits
each tool's description, its arguments with types, required first, and enum values spelled out
(`source_type: one of docs, technical, academic, general`). This is **not** a second
tool-selection mechanism: `advertise()` is still the only source and `dispatch`'s allow-list still
decides what may run.

### `render_turn_state` — state that moves as the hop moves

Appended to each turn's observation, carrying searches/page-reads left and the queries already run,
plus a nudge to open a result when fetches remain. It travels as part of the observation message —
the same mechanism `render_stop_reason` uses — so **the frozen placeholder sets are untouched**.
Counts are passed in as ints, keeping `prompt_context.py` free of `tools/` and the renderer pure.

### Degradations seen, all handled correctly

- A **malformed URL** from the model (`https://www.post:postgresql.org/...`) was rejected and
  reported in `unknowns`. The tool contract working as designed, not a defect.
- A **planner drift** ended one run's loop early; it finalised honestly with what it had.
- `authority="unknown"` is a **domain-classification** label, not a fetch-status label. A cited
  page can be `unknown` *and* fully read — percona.com is simply not in `domains.json`. Do not
  read it as "never opened"; the fetch audit is `source_cache`.

### Quota

**13 live SerpAPI calls of 200** across all of S14, including the failed and killed runs.
**12 recordings captured into `fixtures/search/`**, one per distinct live query. Reservation is
before the call with no refund path, so a killed or retried run spends quota without leaving a
cached row — that, not a leak, is why `search_budget` runs slightly ahead of the row count.

**Still owed by later subtasks:**

- **`--no-research` still bypasses `validate_report`.** S12 left it deliberately (see *The CLI*
  above): `too_many_topics`, `topic_overlap` and `goal_not_narrowed` go unchecked on that path.
  It is now one of two modes rather than the only one a user can run, which lowers the urgency but
  does not settle it.
- **S13 replaced none of `tests/unit/test_single_agent.py`** — as planned. That suite proves the
  loop's own decisions with `FakeProvider`; S13's integration suite proves the composition end to
  end, under 2 s, through `service.py`. It also does not replace `tests/unit/test_service.py`, which
  covers only the three ways a thin composition layer can be wrong, or the CLI routing tests in
  `tests/unit/test_main.py`, which substitute the flow rather than driving it. **The script builders
  (`plan`, `verdict`, `tool_turn`) are deliberately duplicated in the S13 file rather than promoted
  to `conftest.py`:** sharing them would mean editing a passing suite, and the integration file's
  report builder differs anyway — those runs cite pages they actually fetched, while the unit runs
  cite nothing.

**Settled during S11–S12:**

- **`RunState.validation_errors` goes to Day 4 tracing**, not to `service.py` — the reasoning is
  under *The entry point* above.
- **`service.py` calls `run_agent` and nothing else.** Building the registry, the providers and the
  `RunContext` is composition, so it lives there rather than inside the loop; all three were
  already optional injected parameters for exactly that reason.
- **The progress line needed no change to `run_agent`.** The CLI owns the `RunContext` and reads
  its ledger, so the loop kept its signature — which matters because Day 5 splits that loop and
  Day 6 calls the same entry point.

**Settled during S5–S8 (previously owed by S3/S4/S1):**

- The attachment path travels **alongside** the assignment, as `run_research_step`'s
  `attachment_path` parameter, and sets both the advertised menu and `{attachment}` from one
  fact. Day 5 may move it onto `ResearchAssignment` when the Researcher becomes a separate
  agent; nothing forces that today.
- Budget headroom for finalise, `exhausted_limits` reaching `unknowns`, skipping the planner at
  the hop cap, per-turn observations from `render_tool_outcome`, `GatheredSource` assembly and
  `sources_examined` counting reads — all now implemented and described under **The loop** above.
- **The contingency option (2)** — a structured `{"action", "arguments"}` decision instead of
  free-form tool calling — is **still unspent**. The loop uses free-form tool calling, which is
  the design S14 is meant to test. If it fails there, `dispatch` already takes a `ToolCall`, so
  the swap remains a change inside `run_research_step` and no new tool path.
- **S10 appended validation errors as an extra `Message`**, never as a new `finalise.md`
  placeholder (see the prompt contract above) — `finalise()` already sent the stop reason that
  way, so the ladder extended a mechanism rather than introducing one. **Done**; the ladder's
  own contract is under *The retry ladder* above.

**Discovered during S1, still standing:**

- `SupervisorDecision.source_preference` and `WebSearchInput.source_type` are literally the
  same `Literal`, so no translation is ever needed. **S2 does not do this mapping** — the
  bridge never touches a model's arguments. It travels on `ResearchAssignment` and reaches
  the tool through the researcher's prompt (S3) and the research step (S6), which own it.

**Missing (later days):** the MCP server, client and `.mcp.json` (Day 6) · `evals/`, the
requirement audit, the `--offline` demo (Day 7). Everything Day 4 and Day 5 specified is now
built.

**`tool` and `agent` spans exist; `llm` spans do not.** Day 4 T2 wired the run header and one
span per tool call; Day 5 T6 added the agent boundaries, and the prediction held exactly — the
parenting came free from `RunContext`'s stack and neither `tools/hooks.py` nor `render.py`
changed. There is still no span around an individual model call, so a stage's own `generate`
turns are invisible: `supervisor.decide` shows that the planner ran and what it decided, not
that it re-asked once after a schema drift. `SpanKind` already allows `llm` and `agent_span`
generalises to it in a line, but nothing opens one and no work depends on it.

**No live run has been rendered.** `scripts/show_trace.py` and the JSON log line are proven
against synthesised rows and against the real registry with the fixture search backend — every
path offline. What has never happened is reading back a genuine 9-15 minute Ollama run, which is
part of the owed Day 4 acceptance run. The renderer reads what `store.get_spans` returns and
nothing else, so the risk is display detail rather than correctness.

**`RunState.validation_errors` is still unsurfaced.** S11 handed it to Day 4 tracing and neither
T1 nor T2 took it: a corrected-then-successful run needs a *finalisation* span to carry it, and
T2 added only tool spans. The field's lifecycle stays live (set on a rule failure, cleared on a
drift).

**Outstanding Day 1/Day 2 validation — partially cleared during S14 (2026-08-16):**

- **Ollama is installed and `qwen3:4b` is pulled.** *Corrected during S14 — this document
  previously claimed Ollama was not installed on the development machine, and a session planning
  around that would have been planning around a fact that had stopped being true.* Verified:
  `http://localhost:11434` answers, `qwen3:4b` (2.5 GB) is present, and it loads and generates.
  **The development machine is CPU-only** — 8 cores, no NVIDIA GPU — so `ollama ps` reports
  `100% CPU`, and every model call in this project is CPU-bound. `num_ctx=4096` and the 60-minute
  `keep_alive` both reach the server as configured.
- **The model bake-off is still not run.** `qwen3:1.7b` is **not** pulled, and the
  `qwen3:4b` vs `qwen3:1.7b` comparison (valid-JSON rate / tokens-per-second / time-to-first-token
  / wall clock) remains outstanding **by decision, not by omission**: S14 measures `qwen3:4b` alone,
  from the acceptance runs themselves, at no extra cost. `qwen3:4b` is therefore this machine's
  measured model but still not its *chosen* one — the plan's recommendation has not been tested
  against the smaller alternative. Pull `qwen3:1.7b` and run the comparison if 4b ever misses the
  acceptance bar.
- **The first live SerpAPI call was made during S14** and the response was recorded in the same
  session as `fixtures/search/postgresql-partial-index-when-to-use.json` (`recorded_from:
  "serpapi"`). The backend's field mapping, authority classification and quota guard are confirmed
  against the real API, not only against a `respx` mock built from an assumed response shape.
  `search_budget` was empty beforehand, which is what proves it was genuinely the first.
  **The other five recordings remain `handwritten` and no report may cite one.**
- **`HOSTED_MODEL=gemini-3.6-flash` exists for this key** — confirmed during S14 by one free
  ListModels GET, which returned it among 53 models. **This is not proof the ladder works.**
  `config.py`'s own docstring documents the trap: ListModels still advertises `gemini-2.5-flash`
  while `generateContent` answers "no longer available to new users". No hosted *generation* has
  been verified, so **the retry ladder's third rung remains unproven** — and S10's stake still
  stands, that a stale id fails at exactly the moment a run was already in trouble. Verifying it
  costs one free-tier generation; it was deliberately not spent.
- **S14 must measure whether the ladder has room to run.** `_FINALISE_RESERVE` guarantees the
  first attempt only, so a greedy three-hop run reaches finalise with roughly one model call left
  and gets no retry at all. **S13 measured the two-hop case offline and it is tighter than that
  estimate:** a run of two fully-researched hops (plan + 3 turns + appraisal, then plan + 2 turns +
  appraisal) spends exactly all 10 calls including the report, leaving the ladder **zero** attempts
  in reserve — pinned by `test_an_insufficient_verdict_buys_exactly_one_more_hop_drawn_from_hop_one`.
  A run that both researches twice and needs a correction is currently unaffordable. S14's criterion is a valid report on ≥4 of 5 attempts; if `qwen3:4b`
  cites ungrounded URLs with any regularity, the ladder will be unavailable on precisely the runs
  that need it. `MAX_MODEL_CALLS` is the dial to turn, not the reserve — raising the reserve takes
  calls away from research to buy retries nobody may need.

**Untested behaviour S14 exposed — three live-critical things nothing pins:**

Each of these can be deleted or broken with the **entire offline suite still green**, and each
costs a live run when it breaks. Defect (4) above shipped precisely through the second one.

- **`"think": False` in the Ollama payload.** Remove it and every run gets ~34× slower. No test
  asserts the payload carries it.
- **`render_available_tools` rendering arguments.** No test asserts it emits anything at all — it
  had none before S14 either, which is why the names-only regression reached a live run.
- **`render_turn_state`.** New in S14, zero coverage.

One assertion each, in the suites that already exist (`test_llm_provider.py`,
`test_prompt_context.py`). **Not added during S14** — the subtask authorised fixes and re-running
affected tests, not new coverage. This is the highest-value coverage work outstanding.

**Never exercised, live or offline:** the **retry ladder** (no live run has yet produced an invalid
report, so `MAX_OUTPUT_RETRIES` has never fired against a real model) and the **hosted fallback**
(`gemini-3.6-flash` is confirmed to exist for this key by ListModels, but no `generateContent` call
has ever been made). S10's stake stands: the ladder's final rung is unproven.

**Known defects and rough edges:**

- `read_document` takes no `settings`, so `build_tool_registry(settings=…)` does not reach it; it
  resolves `ALLOWED_ATTACHMENT_DIR` through `get_settings()` when it reads. Tests must patch
  `documents.reader.get_settings`. Widen its constructor only if a real caller needs a per-run
  attachment directory.
- URL dedup is exact canonical-string match, so `www`/non-`www` and `http`/`https` variants of one
  page survive as two entries and can cost two fetches. One-line fix if it proves wasteful.
- On a fresh fetch, `FetchUrlOutput.retrieved_at` is taken a beat after the cache row's
  `fetched_at`, so a cache hit reports a timestamp ~1 ms earlier than the call that filled it.
  Harmless; the contract is only that a hit reports the fetch, never the serve.
- **`README.md`'s factual claims were corrected after S10** — status, the file tree, the test
  count, search, `validate_report`, the retry ladder, attachments, and the `ddgs` backend (a
  reserved name that raises, not a working fallback). Its *structure* is unchanged, so the
  narrative sections still read as a Phase 2 plan rather than a description of what runs today.
  Keep the factual claims in step with this document when a subtask lands.
- `fixtures/documents/Sample.txt` is a lorem-ipsum scratch file, deliberately not committed and
  not part of the documented fixture set. It is now named in `.gitignore` so it stops showing up
  in every `git status` — named individually, never as a pattern, because `fixtures/` is a
  documented set with a provenance policy and a wildcard there would hide a real recording.

---

## Current milestone — Day 5: Supervisor + Researcher + Appraiser

**Goal (plan §25):** convert the proven single-agent loop into Supervisor + Researcher +
Appraiser. Splitting a loop that works is a refactor; building three agents from scratch is a
rewrite — which is why this day comes after Day 3 rather than instead of it.

| # | Subtask | Primary targets | Status |
| --- | --- | --- | --- |
| **T1** | **Split, do not rewrite — and keep the single agent runnable.** The four Day 3 stage functions moved into three role modules over a shared `runtime.py`; `service.py` gained `mode`, `main.py` gained `--mode` (default `multi`). No schema changed, no prompt was renamed, no tool was re-implemented, no `agent` span was added. Every one of the 470 pre-existing tests passes untouched | `agents/{runtime,supervisor,researcher,appraiser,single_agent,__init__}.py`, `config.py`, `service.py`, `main.py`, `tests/integration/test_multi_agent.py`, `tests/unit/test_service.py`, `tests/unit/test_main.py` | **Done** |
| **T2** | **Typed inter-agent messages** — `AppraisalVerdict` gains `accepted[]`, `rejected[]`, `disagreements[]`; additive, never a rewrite | `schemas/agents.py` | Not started |
| **T3** | **Per-role provider selection** — `SUPERVISOR_PROVIDER` / `RESEARCHER_PROVIDER` / `APPRAISER_PROVIDER` already exist and already route; needs verifying rather than building | `config.py`, `llm/__init__.py` | Not started |
| **T4** | **Appraiser judgement quality** — the verdict became a reading of the evidence rather than a flag. `AppraisalVerdict.accepted`/`rejected` now hold one object per source: `AcceptedSource(source, supports, does_not_support, authority)` and `RejectedSource(source, reason)`, both defaulted and both coercing a bare string, so a Day 3/T2-shaped reply still validates. `sufficiency.md` rewritten to judge per source, reject only with a stated reason, and leave `disagreements` empty when none exist. Consumed by `render_research_context` (the finalise prompt now names an accepted source's authority, what it supports and what it leaves open, and heads the rejected list with "do not cite any of these in resources") and by `_appraisal_line` (the `run_memory` row, and therefore the trace). `finalise.md` gained the matching citation rule | `schemas/agents.py`, `schemas/__init__.py`, `llm/prompts/{sufficiency,finalise}.md`, `agents/{prompt_context,runtime,appraiser}.py`, `tests/unit/test_appraiser.py` | **Done** |
| **T5** | **Evidence-driven multi-hop** — the stop/continue decision became the Appraiser's alone. `_stop_after_hop` now applies the plan's full stop condition (`sufficient` **and** `len(accepted) >= _MIN_ACCEPTED`, else the new `thin_evidence` reason), and when it says to continue the planner is **skipped entirely**: `_outstanding_followup` reads the verdict and `_followup_decision` turns `requested_followup` into the next `ResearchAssignment`, so no model call exists in which the Supervisor could answer FINALISE over the judgement. `_can_afford_a_hop` replaces the budget guard that the skipped planner used to provide. Both loops share the four helpers (`single_agent.py` imports them) so they cannot answer one verdict two ways | `agents/{supervisor,single_agent,runtime,prompt_context}.py`, `schemas/agents.py`, `tests/unit/test_supervisor.py`, `tests/integration/test_multi_agent.py`, `tests/integration/test_single_loop.py`, `tests/unit/test_single_agent.py` | **Done** |
| **T6** | **Cross-agent tracing** — `tracing.agent_span`, the context manager Day 4 deferred, plus five `agent` spans opened at the call sites in `run_supervised` (`supervisor.run` → `supervisor.decide` · `researcher.loop` · `appraiser.judge` · `supervisor.finalise`) and one, `agent.run`, in `run_agent`. The `Tracer` reaches a loop as an argument from `service.py`; parenting is `RunContext.span_stack` and nothing else, so `tools/hooks.py`, `render.py` and `store.py` are untouched. The renderer's Day 4 prediction held exactly: proven at depth against synthesised rows, it displayed a genuine run's tree with no change | `tracing/{tracer,__init__}.py`, `agents/{supervisor,single_agent}.py`, `service.py`, `tests/unit/test_tracing.py`, `tests/integration/test_multi_agent.py` | **Done** |

**What T1 deliberately did not do**, so a later session does not read the absence as an
oversight: it renamed nothing (`decide_next_step` / `run_research_step` / `judge_sufficiency`
keep their exported names, the four prompts keep their filenames), changed no schema, added no
`agent` span, and left `single_agent.run_agent`'s body byte-identical. Each of those is a
separate Day 5 feature or an explicit non-goal; see *The role split* under *Core contracts*.

---

## Previous milestone — Day 4: memory, hooks and tracing

**Goal (plan §24):** the system remembers across runs, and every tool call is timestamped, traced and
budget-checked.

| # | Subtask | Primary targets | Status |
| --- | --- | --- | --- |
| **T1** | **Tracing foundation** — the span stack on `RunContext`; the `runs`/`spans` tables; `tracing/store.py` (rows, raises) and `tracing/tracer.py` (the hook-facing API, swallows `sqlite3.Error`). `TRACE_SUMMARY_CHARS` added. **Wired to nothing** — the agent flow is byte-identical | `tools/base.py`, `memory/db.py`, `tracing/`, `config.py`, `.env.example`, `tests/unit/test_tracing.py` | **Done** |
| **T2** | **Registry hook chains** — `tools/hooks.py`: span open/close around every tool call, `from_cache` on the span, `ToolResult.duration_ms` passed through. `service.py` owns the run's connection, writes the run header and closes it `ok`/`failed`/`budget_exhausted`. A raising tool now also reaches the post-hooks, so its span closes | `tools/hooks.py`, `tools/registry.py`, `tools/wiring.py`, `service.py` | **Done** |
| **T3** | **Budget enforcement into the pre-hook** — `_TOOL_BUDGET` + `_claim_for_tool` lifted out of `single_agent.py` into `enforce_run_budget`; a refused `claim` becomes `ToolResult(BUDGET_EXCEEDED)` and the tool never runs. Removed the over-count on malformed arguments, and on a tool the step was not offered | `tools/hooks.py`, `agents/single_agent.py` | **Done** |
| **T4** | **Persistent memory** — `prep_memory`, `run_memory`, `recall_previous_preparation`, `save_preparation`, task-key normalisation, the 30-day recall window | `memory/`, `tools/`, `wiring.py` | **Done** |
| **T5** | **Memory-aware agent integration** — one recall in `run_agent` seeds `RunState.previous`; prior goal, `topics_covered` and `topics_deferred` reach **both** the planner (a new `plan.md` placeholder) and the report (an extra `finalise()` message, since `finalise.md` is frozen). Guidance, not enforcement; `source_urls` never rendered; nothing recalled means the pre-T5 prompts, unchanged | `agents/single_agent.py`, `agents/prompt_context.py`, `llm/prompts/plan.md`, `schemas/agents.py` | **Done** |
| **T6** | **Trace renderer + JSON log lines** — `tracing/render.py` (pure: rows in, lines out) and `scripts/show_trace.py <run_id>`, which prints §13's tree. One JSON line per tool call from `TracingHooks.after`, from the same values as the span row; the hook is now installed unconditionally and `main.py --trace-log` attaches the handler | `tracing/render.py`, `scripts/show_trace.py`, `tools/hooks.py`, `main.py` | **Done** |

**Acceptance criteria (plan §24):** zero tool calls outside the registry · the trace tree renders with
correct nesting and timings · run 2 covers different topics from run 1 and says so in
`interpreted_goal` · budget exhaustion produces an honest report with populated `unknowns`, not a
crash.

**Day 3's two remaining S14 acceptance runs are banked for Day 7**, with everything else that costs
a model (*Engineering decisions* 12). They are independent of Day 4 work, which does not touch the
loop's decisions, and they do not block Day 5.

---

## Previous milestone — Day 3: single research agent (the core loop)

**Goal:** one agent that plans, searches, reads, decides whether it has enough, performs a second
hop when needed, and produces a validated `FocusPreparationReport`. No Supervisor, no workers yet.

**Why it is the highest-risk day:** if a small local model cannot drive this loop, that must be
discovered now, with four days left to adapt, not on Day 5.

**Features (plan §23):** the loop (plan → act → observe → decide → stop) · task understanding and
narrowing with `session_minutes` as a hard scoping input · tool-calling integration · the
sufficiency check and further hops, capped at `MAX_HOPS=3` · structured finalisation +
`validate_report` + retry, including source-URL grounding · in-memory budget enforcement in
`RunContext`.

**Structure it for Day 5 now.** Write the loop as four separate, separately-prompted functions,
each taking and returning a Pydantic model from `schemas/agents.py`, even though one agent runs
them all today:

| Day 3 function | Becomes on Day 5 |
| --- | --- |
| `decide_next_step()` | `supervisor.decide()` |
| `run_research_step()` | `researcher.run()` |
| `judge_sufficiency()` | `appraiser.judge()` |
| `finalise()` | `supervisor.finalise()` |

**Acceptance criteria (end of day):** an end-to-end live run produces a valid report on ≥4 of 5
attempts · at least one run visibly performs a second hop with a query derived from hop 1's content
· every cited URL was actually fetched in that run · the offline `FakeProvider` test runs in under
2 s · no budget can be exceeded.

**Contingency — decide during Day 3, not after.** If the model cannot reliably drive tool calls,
act the same day, in this order: (1) reduce the tool menu offered per turn (`web_search` and
`fetch_url` only during the research step); (2) replace free-form tool calling with a structured
decision step — the model returns `{"action": …, "arguments": {…}}` under a constrained schema and
our code dispatches, which is far more reliable on small models; (3) drop to `MAX_HOPS=1` and make
the second hop deterministic, triggered by non-empty `missing_information` rather than by free
choice. All three preserve every Phase 2 requirement. **Record whichever is chosen in
`prompts.md` and here.**

## Day 3 subtask breakdown — S1–S12 done

Each subtask is planned, approved, implemented and tested independently.

| # | Subtask | Primary targets | Depends on | Status |
| --- | --- | --- | --- | --- |
| **S1** | **Agent schemas** — the typed contracts for the four reasoning boundaries. Reuses `TaskContext`, `SourceAuthority`, `ToolError`; `SearchSourceType` moved into `schemas/tools.py`. `GatheredSource` replaces the planned reuse of `NormalizedSource` (import rule) | `schemas/agents.py`, `schemas/tools.py`, `schemas/__init__.py`, `search/base.py` | — | **Done** |
| **S2** | **Model-facing tool integration** — registered `Tool` → existing `llm.base.ToolSpec`; a per-`AgentRole` menu (`normalize_sources` never advertised, `read_document` only with an attachment); dispatch through `ToolRegistry`, which stays the only argument validator; an un-advertised name refused as a `ToolResult` before the registry. Provider-neutral | `agents/tool_calling.py`, `agents/__init__.py`, `tests/unit/test_tool_calling.py` | S1 | **Done** |
| **S3** | **Agent prompts and assembly** — the four stage prompts plus `prompt_context.py`, one renderer per placeholder, bounding what a model sees at `SOURCE_EXCERPT_CHARS`. Wording is safe to change later; the placeholder set and the evidence split are not | `llm/prompts/{plan,research_step,sufficiency}.md`, `finalise.md`, `agents/prompt_context.py`, `tests/unit/test_prompt_context.py` | S1 | **Done** |
| **S4** | **In-memory budget counters on `RunContext`** — `RunBudget`: `claim(kind)` is the single enforcement point for `MAX_SEARCH_CALLS`, `MAX_FETCH_CALLS`, `MAX_MODEL_CALLS` and `TOTAL_RUN_TIMEOUT_S`; `MAX_SOURCES_KEPT` and `MAX_HOPS` are limits whose counts stay on `RunState`. A refusal is `False`, so Day 4 lifts it into a pre-hook unchanged. `RunState` was not touched — it already owns everything a run has *seen* | `tools/base.py`, `tools/__init__.py`, `tests/unit/test_run_budget.py` | — | **Done** |
| **S5** | **Task understanding and narrowing** — `decide_next_step()`: a broad task becomes one session-sized research question, with `session_minutes` as a hard scoping input | `agents/single_agent.py`, `tests/unit/test_single_agent.py` | S1–S4 | **Done** |
| **S6** | **Research step** — `run_research_step()`: the search → fetch → collect turn; tool-call parsing; "unknown tool" and "malformed arguments" handled as ordinary recoverable states; in-run `seen_urls` / `seen_queries` | `agents/single_agent.py` | S5 | **Done** |
| **S7** | **Sufficiency judgement** — `judge_sufficiency()`: do these sources support a useful session, or is a prerequisite missing? Becomes the Appraiser on Day 5 | `agents/single_agent.py` | S6 | **Done** |
| **S8** | **Core loop and the genuine second hop** — plan → act → observe → decide → stop; hop 2's query derived from hop 1's content; `MAX_HOPS=3` never exceeded even if the model keeps asking to research. **The acceptance criterion is still one *visible* second hop** — a third is now permitted, not required | `agents/single_agent.py` | S5–S7 | **Done** |
| **S9** | **`validate_report` and grounding** — Pydantic, then the business rules; **every cited URL must appear in the set this run discovered or fetched**. Registered as a pipeline tool | `tools/validate_report.py`, `wiring.py` | S1 | **Done** |
| **S10** | **Structured finalisation and the retry ladder** — `finalise()`; attempt 1 primary model, attempt 2 primary with the validation errors quoted back, attempt 3 the second provider *when one is genuinely configured*, then fail loudly. A drifted schema takes a rung of the same ladder. A run that cannot produce a valid report **never returns a partial one** | `agents/single_agent.py`, `tests/unit/test_single_agent.py` | S9 | **Done** |
| **S11** | **`service.py`** — the one entry point the CLI and the Day 6 MCP server both call. Thin: resolves settings once, defaults registry / providers / `RunContext` from it, calls `run_agent`. `PreparationFailed` and `LLMError` pass through unwrapped. `RunState.validation_errors` handed to Day 4 tracing | `service.py`, `tests/unit/test_service.py` | S8, S10 | **Done** |
| **S12** | **CLI integration** — research mode replaces the Day 1 refusal; `--attachment` wired through `TaskContext` and pre-flighted with `documents.resolve_attachment`; a live progress line on stderr reading the run's own `RunBudget`; `--quiet`. **Flat flags kept** — the subcommand question is settled. A failed run is never downgraded to `--no-research` | `main.py`, `documents/reader.py`, `tests/unit/test_main.py` | S11 | **Done** |
| **S13** | **Offline integration tests** — full loop on `FakeProvider` + fixture search, valid report, **under 2 s** · a scripted "insufficient" verdict triggers exactly one second hop · the hop cap holds · a `SEARCH_UNAVAILABLE` degrades the report rather than crashing · three invalid outputs raise `PreparationFailed` · grounding rejects a report citing an unfetched URL. Driven through `prepare_focus_session` against the **committed** fixture tree; 8 cases, 0.87 s | `tests/integration/test_single_loop.py` | S12 | **Done** |
| **S14** | **Live end-to-end verification** — Ollama + SerpAPI. Found and fixed 5 defects the offline suite could not see, spent contingency option (2), and proved the second hop and the grounding rule live. **3/3 valid reports — the ≥4-of-5 sample is incomplete.** 12 live searches recorded into `fixtures/search/` | `llm/ollama_provider.py`, `schemas/agents.py`, `agents/single_agent.py`, `agents/prompt_context.py`, `fixtures/search/`, `prompts.md` | S13 | **Done — sign-off pending 2 runs** |

```
S1 ─┬─► S2 ─┐
    ├─► S3 ─┼─► S5 ─► S6 ─► S7 ─► S8 ─► S11 ─► S12 ─► S13 ─► S14
S4 ─┘       │                      ▲
            └─► S9 ─► S10 ─────────┘
```

---

## Maintaining this document

After each completed subtask, update **only** the sections it affects:

- flip that subtask's status and record what it now provides;
- add any new contract, invariant or decision that constrains later work;
- note new or changed files, dependencies and tests;
- update the status block when the milestone or next task changes.

Do **not** turn it back into an implementation diary. Do not paste detail that the code already
states clearly. Do not remove an older contract, invariant, deviation or dependency that later
work still relies on. If something here turns out to be wrong, verify against the code and correct
it — the repository is the evidence.
