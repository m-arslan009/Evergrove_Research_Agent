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
| **Current milestone** | **Day 3 — Single research agent (the core loop)** |
| **Completed Day 3 subtasks** | **S1 — Agent schemas** · **S2 — Model-facing tool integration** · **S3 — Agent prompts and assembly** · **S4 — In-memory budget counters on `RunContext`** · **S5–S8 — the orchestration loop** · **S9 — `validate_report` and grounding** |
| **Next task** | **Day 3 Subtask 10 — structured finalisation and the retry ladder** |

`schemas/agents.py` is the contract every later Day 3 subtask builds against,
`agents/tool_calling.py` is the only bridge between a model and the tool registry,
`agents/prompt_context.py` is the only place a run's state becomes prompt text,
`RunContext.budget` is the only ledger of what a run has spent, and
`agents/single_agent.py` is the loop that drives all four, and
`tools/validate_report.py` is the only place a finished report is checked against the evidence.
Nothing else in `agents/` exists: no `service.py`. `finalise()` exists but makes **one attempt**
and does not yet call `validate_report` — the retry ladder is still S10's. Do not describe or
assume any other Day 3 capability as present.

| Day | Area | Status |
| --- | --- | --- |
| 1 | Project, config, schemas, `LLMProvider` + three providers, first structured round trip | **Done** |
| 2 | Deterministic tools: registry, search, fetch, document readers, SQLite caches, fixtures, tools CLI | **Done** |
| 3 | Single research agent — the core loop | **Current — S1–S9 done, S10–S14 remain** |
| 4 | Memory, hooks, tracing | Not started |
| 5 | Supervisor + Researcher + Appraiser | Not started |
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
                    CLI  ·  MCP server            ← surfaces   (CLI: partial · MCP: Day 6)
                          │
                     service.py                   ← NOT BUILT (Day 3 S11)
                          │
       Supervisor ──► Researcher ──► Appraiser     ← BUILT (S5-S8) as four functions in
                          │                          agents/single_agent.py; split into
                          │                          three agents on Day 5
              agents/tool_calling.py               ← BUILT (S2). Advertise → dispatch;
                          │                          the only way a model reaches a tool
                   tool registry                  ← BUILT. The only path to a tool.
                          │                          Hook lists exist but are empty (Day 4)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   search backends   documents/         SQLite          ← all BUILT
   (serpapi ·        (pdf · docx ·      (source cache ·
    academic ·        html · text ·      search cache ·
    fixture)          excerpt)           search budget)
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
| `src/evergrove_agent/agents/` | `tool_calling.py` — the model ↔ tool bridge (S2); `prompt_context.py` — the placeholder renderers (S3); `single_agent.py` — the four stage functions and the loop (S5–S8) |
| `src/evergrove_agent/tools/` | `base.py` (contract · `RunContext` · `RunBudget`), `registry.py` (the only call path), `wiring.py` (composition root), `cli.py`, and the four tools |
| `src/evergrove_agent/search/` | `base.py`, `normalize.py`, `domains.py` + `domains.json`, `fixture.py`, `serpapi.py`, `academic.py` |
| `src/evergrove_agent/documents/` | `base.py`, `reader.py`, `excerpt.py`, `text.py`, `pdf.py`, `docx.py`, `html.py` |
| `src/evergrove_agent/memory/` | `db.py` (all DDL), `cache.py`, `search_cache.py`, `budget.py` |
| `src/evergrove_agent/main.py` | CLI entry point — `--no-research` is the only working mode |
| `tests/unit/`, `tests/integration/`, `tests/conftest.py` | Offline suites; `settings` fixture is `Settings(_env_file=None)` |
| `fixtures/` | `search/` recordings, `documents/` attachments, `html/` markup, `README.md` (provenance policy) |
| `.githooks/pre-push` | `ruff check` then `pytest -q`; enable per clone with `git config core.hooksPath .githooks` |
| `.env.example` | The documented setting set; committed defaults must stay offline |
| `docs/research-agent-context.md` | This file |
| `prompts.md` | The required AI interaction log |

**Not present, do not assume:** `service.py`, `tools/validate_report.py`, `evals/`, `scripts/`,
`.mcp.json`.

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
into a no-research report. Research mode and `--attachment` **exit 2 with an explanatory
message rather than faking a result**. Exit codes: 0 success, 1 run failure, 2 bad usage.

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

**Fixtures** (`fixtures/`) — 5 search recordings covering all four `source_type` values plus an
empty result, 2 document attachments, 1 HTML page. Self-describing format
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

**`RunContext`** — `run_id` and `budget: RunBudget` (S4). Day 4 adds the span stack. The registry,
every hook and every agent read it, so its shape is expensive to change.

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

**`ToolRegistry`** — `register`, `get`, `names`, `add_pre_hook`, `add_post_hook`, and
`async call(name, args, ctx) -> ToolResult`. `args` may be the tool's input model, a raw mapping
(**which is the form a model's tool call arrives in**) or `None`. Order: resolve → validate args
→ pre-hooks → `Tool.run` → post-hooks. **`call` never raises**; it times the call and stamps
`duration_ms` centrally; an unknown name returns `UNKNOWN` with the menu, invalid arguments
return `BAD_ARGUMENTS`, and a tool that raises anyway is caught. A pre-hook returning a
`ToolResult` short-circuits the tool — that is how Day 4's cache hits and budget refusals will
work. Duplicate registration raises at wiring time; everything at call time is a `ToolResult`.

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
| **Code assembled** — a model never sees the schema | `GatheredSource`, `ToolFailure`, `ResearchAssignment`, `ResearchFindings`, `AppraisalRequest`, `RunState` | As rich as the loop needs |

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
- **`AppraisalVerdict` is lean by design.** Day 5 adds `accepted[]`, `rejected[]` and
  `disagreements[]` as optional fields — additive, not a rewrite.

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
| `plan.md` | `decide_next_step()` | `SupervisorDecision` | `task_title`, `task_description`, `session_minutes`, `progress` |
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
AgentProviders(supervisor, researcher, appraiser)      # .from_settings(settings)
PreparationFailed(RuntimeError)                        # exception, not a schema
StopReason = Literal["sufficient", "planner_finalised", "hop_cap", "budget_spent",
                     "no_followup", "planner_unavailable", "appraiser_unavailable"]
async decide_next_step(state, *, provider, ctx, settings=None)  -> SupervisorDecision | None
async run_research_step(assignment, *, provider, registry, ctx, settings=None,
                        attachment_path=None)                   -> ResearchFindings
async judge_sufficiency(request, *, provider, ctx, settings=None) -> AppraisalVerdict | None
async finalise(state, *, provider, ctx, stop_reason="sufficient", settings=None)
                                                                -> FocusPreparationReport
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
- **`_TOOL_BUDGET` + `_claim_for_tool` are the whole of tool-budget enforcement**, deliberately
  in one piece because Day 4 lifts exactly those two into a registry pre-hook. `web_search` →
  `search`, `fetch_url` → `fetch`; `read_document` is local disk and free; a name the model
  invented is absent from the map, so a guessed name cannot drain a budget.
  **Known over-count Day 4 removes for free:** a correctly named tool with malformed arguments
  is charged, because the claim must precede the call while `ToolRegistry.call` rejects
  arguments before the tool runs. Day 4's pre-hook runs *after* that validation, so the move
  fixes it.
- **`dispatch_all` is not used by the loop** — a claim has to sit between one call and the
  next, so `dispatch` is called per tool call, still strictly in the model's own order.
- **A later hop's question is never written by our code.** `requested_followup` →
  `state.verdict` → `render_progress` → `plan.md`'s "prefer the suggested follow-up" clause.
  The loop only carries it. A null follow-up ends the run (`"no_followup"`); never invent one.
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
`agents.prompt_context` **directly, not via `agents/__init__.py`** — the package `__init__`
now pulls in `single_agent`, and with it `httpx` and `sqlite3`, which the `--no-research` path
does not need.

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

**`LLMError` is an exception, `ToolError` is a value.** Deliberate: an unreachable model is a
broken run, not something the agent can reason around. Preserve the asymmetry.

## Tool and registry architecture

- **One path to every tool.** Nothing calls a tool directly; everything goes through
  `ToolRegistry.call`. Day 3 must dispatch model tool calls through it too.
- **Few tools, enum-routed.** One `web_search` with a `source_type`, one `read_document` with a
  `mode` — a small local model's tool selection degrades as the menu grows. The routing behind
  each enum is deterministic Python.
- **Hook points now, hooks later.** The pre/post lists exist and stay **empty** until Day 4. Do
  not install a hook before then, and do not install one from `wiring.py`.
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
- `MAX_FETCH_CALLS` is **not** enforced here — that is Day 4's registry pre-hook (and Day 3's
  in-memory counter).

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
in `SCHEMA_STATEMENTS` (`schema_meta`, `source_cache`, `search_cache`, `search_budget`);
`SCHEMA_VERSION` is 1 and is only a marker for whoever first changes an existing table's shape.
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
`DB_PATH`, `ALLOWED_ATTACHMENT_DIR`, `SEARCH_FIXTURE_DIR`.

**Declared but not yet consumed — these are Day 3's and Day 4's budgets, already sized:**
`MAX_HOPS=3` (**raised from the plan's 2 on request, Day 3**; 3 is also the ceiling
`FocusPreparationReport.hops_used` allows, so any further raise means changing the most expensive
schema in the project), `MAX_SEARCH_CALLS=3`, `MAX_FETCH_CALLS=4`, `MAX_SOURCES_KEPT=3`,
`MAX_MODEL_CALLS=10`, `MAX_OUTPUT_RETRIES=3`, `MEMORY_RECALL_MAX_AGE_DAYS=30` (Day 4).
**Day 3 must consume these, not invent new ones.**

`SEARCH_BACKEND=fixture` stays the committed default in `Settings` and `.env.example`. Never
change it to make a test pass.

## Testing and offline strategy

**~356 offline test cases, plus 1 `live`-marked test** (a real-Ollama round trip in
`test_llm_provider.py`). The newest are the 11 in `tests/unit/test_single_agent.py` (S5–S8) —
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
4. **Hook points now, hooks later** — the lists stay empty until Day 4.
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

## Verified deviations from the 7-day plan

The repository is authoritative. **Do not "restore" the plan's version of any of these.**

| Plan | Repository | Why |
| --- | --- | --- |
| `trafilatura` for HTML extraction | stdlib `html.parser` in `documents/html.py` | Recorded departure on the user's call. Revisit only if fixtures show the output is too noisy; the swap is `extract_html`'s body and no caller's business |
| `domains.yaml` allow-list | `search/domains.json` | PyYAML is not worth adding for a static file |
| `read_document` handles `.txt`/`.md`/`.pdf` | **`.docx` too**, via stdlib `zipfile` + `ElementTree` reading `word/document.xml` | Added to scope on request. No `python-docx`, no `lxml` |
| `fetch_url` empty extraction → `NO_CONTENT` | `EMPTY_FILE` | Reuses an existing code rather than adding a near-duplicate |
| `ddgs` as the keyless fallback rung | **Deliberately not implemented**; `build_search_backend` refuses it and `web_search`'s ladder has no such rung. It remains in the `SearchBackendName` literal and in CLI `--choices` | A backend that merely looks right is worse than one that refuses |
| Four *agent-callable* tools: `web_search`, `fetch_url`, `read_document`, `recall_previous_preparation` | The four *registered* tools are `web_search`, `fetch_url`, `read_document`, **`normalize_sources`** | `recall_previous_preparation` is **Day 4** (memory). `normalize_sources` is a pipeline tool, registered for traceability, and is **not** intended for the model's menu |
| Day 3 demo CLI `evergrove-agent prepare --task …` (a subcommand) | `main.py` uses flat flags with no subcommand; the tools CLI is a separate module | **Open decision for Day 3 Subtask 12** — adopt the subcommand or keep flat flags, but decide deliberately |
| `validate_report` listed under Day 2 in `README.md` | Not implemented; plan §23 feature 5 places it in the **Day 3** loop | The plan wins over the README here |
| `MAX_HOPS = 2` | **`MAX_HOPS = 3`** in `config.py` and `.env.example` | Raised on the user's explicit instruction during Day 3 S1. It is the ceiling: `FocusPreparationReport.hops_used` is `le=3`. Costs one more possible hop's worth of searches and fetches per run |
| `SearchSourceType` defined in `search/base.py` | Defined in **`schemas/tools.py`**, re-exported unchanged from `search/base.py` | The Supervisor's `source_preference` is the same enum, and `schemas/` may import nothing from the package — so the definition had to move to the layer both sides can see. Every existing importer is unchanged |
| `schemas/agents.py` reuses `NormalizedSource` for the agent's sources | A distinct `GatheredSource` | Reuse is impossible (`search/normalize.py` imports `schemas`) and wrong: a search hit is not the same thing as a source that was opened and read |

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
| Spend or check live-search quota | `memory.budget.reserve_search_call` |
| A prompt | `llm.prompts.render_prompt(name, **values)` + a new `.md` file |
| A prompt's placeholder text | `agents/prompt_context.py` — one renderer per placeholder; never build a block at a call site |
| Show a model the sources a run gathered | `agents.render_sources` (full, for judging) / `agents.render_research_context` (compact, for finalising) |
| Show a model what a tool answered | `agents.render_tool_outcome` — code, message and retryability included |
| Run the whole agent | `agents.run_agent(task, registry=…)` — never call the four stages in sequence yourself |
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
| The source-type enum | `schemas.SearchSourceType` — one definition, re-exported by `search/base.py` |

**What later days depend on:**

- **Day 4** (memory, hooks, tracing) fills the registry's existing hook lists, extends `RunContext`
  with the span stack, moves budget enforcement out of the Day 3 loop into pre-hooks, and adds
  `recall_previous_preparation` + `save_preparation` and the `runs`/`spans` tables. **Day 3 should
  therefore keep budget enforcement in one place, so moving it is a lift and not a rewrite.**
- **Day 5** splits Day 3's four functions into `agents/supervisor.py`, `agents/researcher.py`,
  `agents/appraiser.py`. **This is why Day 3 must be written as four separately-prompted functions
  exchanging Pydantic models** — Day 5 then becomes a file move, not a rewrite. Workers reuse the
  Day 2 tools unchanged; a tool is never re-implemented inside a worker.
- **Day 6** (MCP) wraps `service.py`. The MCP tool's return type is `FocusPreparationReport` and its
  input mirrors `TaskContext`.
- **Day 7** runs five evaluations over all of the above.

## Known limitations and not yet implemented

**Missing (Day 3 still builds these):** `service.py` · the finalise retry ladder.

**Discovered during S5–S8, still owed by later subtasks:**

- **S9 owns grounding, alone, and now exists — but nothing calls it yet.** `finalise()`
  deliberately does not touch `resources`, so until **S10** wires `validate_report` into the
  finalise path, **a report can still cite a URL the run never saw.** The rule is written and
  proven; it is not yet enforced on a live run.
- **S10 wraps `finalise()` rather than replacing it.** The extra-`Message` mechanism the ladder
  needs is already in place (the stop reason travels that way); attempt 2 adds the validation
  errors as a second such message, attempt 3 switches provider. `PreparationFailed` already
  exists — S10 raises it after three attempts instead of one. `MAX_OUTPUT_RETRIES` is still
  unconsumed and is the ladder's bound.
- **S11's `service.py` calls `run_agent` and nothing else.** Building the registry, the providers
  and the `RunContext` is composition, so it belongs there rather than inside the loop — all
  three are already optional injected parameters for exactly that reason.
- **S13 replaces none of `tests/unit/test_single_agent.py`.** That suite proves the loop's own
  decisions with `FakeProvider`; S13's integration suite proves the composition end to end,
  under 2 s, through `service.py`.

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
- **S10 appends validation errors as an extra `Message`**, never as a new `finalise.md`
  placeholder (see the prompt contract above). `finalise()` already sends the stop reason that
  way, so the ladder extends a mechanism rather than introducing one.

**Discovered during S1, still standing:**

- `SupervisorDecision.source_preference` and `WebSearchInput.source_type` are literally the
  same `Literal`, so no translation is ever needed. **S2 does not do this mapping** — the
  bridge never touches a model's arguments. It travels on `ResearchAssignment` and reaches
  the tool through the researcher's prompt (S3) and the research step (S6), which own it.

**Missing (later days):** hooks, tracing, `runs`/`spans` tables, `recall_previous_preparation`,
`save_preparation`, memory-aware prompting (Day 4) · the supervisor/worker split (Day 5) · the MCP
server, client and `.mcp.json` (Day 6) · `evals/`, the requirement audit, the `--offline` demo
(Day 7).

**Outstanding Day 1/Day 2 validation — not Day 3 work, and not yet done:**

- **The Ollama model bake-off has never been run** (`qwen3:4b` vs `qwen3:1.7b`, five structured
  calls each, valid-JSON rate / tokens-per-second / time-to-first-token / wall clock). Ollama is not
  installed on the development machine, **no local run has ever been performed, and no
  tokens-per-second figure has been measured**. `qwen3:4b` is the plan's recommendation, not this
  machine's measurement. Day 3's first live run is where this bites.
- **No live SerpAPI call has ever been made.** All five recordings in `fixtures/search/` are
  `handwritten` — right shape, not knowledge. **No report may cite one.** The live acceptance run
  (one real query, recorded in the same session, behind `@pytest.mark.live`) is still outstanding.
- **No live Gemini/hosted call has been verified** either; `HOSTED_MODEL` defaults to
  `gemini-3.6-flash` and free-tier model ids change — confirm against the AI Studio console before
  relying on it.

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
- **`README.md` is stale** — it still says "Day 1 complete, Day 2 in progress", "100 unit tests",
  that search is unimplemented, and lists `validate_report` and attachments as Day 2 TODOs. All of
  that is wrong. **Known documentation issue; correcting it is not yet approved work.**
- `fixtures/documents/Sample.txt` is an untracked lorem-ipsum scratch file, deliberately not
  committed and not part of the documented fixture set.

---

## Current milestone — Day 3: single research agent (the core loop)

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

## Day 3 subtask breakdown — S1 and S2 done

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
| **S10** | **Structured finalisation and the retry ladder** — `finalise()`; attempt 1 primary model, attempt 2 primary with the validation errors quoted back, attempt 3 the second provider, then fail loudly. A run that cannot produce a valid report **never returns a partial one** | `agents/single_agent.py` | S9 | Not started |
| **S11** | **`service.py`** — the one entry point the CLI and the Day 6 MCP server both call. Thin | `service.py` | S8, S10 | Not started |
| **S12** | **CLI integration** — research mode replaces the current refusal; `--attachment` wired to `read_document`. **Resolves the open subcommand-vs-flat-flags decision** | `main.py` | S11 | Not started |
| **S13** | **Offline integration tests** — full loop on `FakeProvider` + fixture search, valid report, **under 2 s** · a scripted "insufficient" verdict triggers exactly one second hop · the hop cap holds · a `SEARCH_UNAVAILABLE` degrades the report rather than crashing · three invalid outputs raise `PreparationFailed` · grounding rejects a report citing an unfetched URL | `tests/integration/test_single_loop.py` | S12 | Not started |
| **S14** | **Live end-to-end verification** — Ollama + SerpAPI: a valid report on ≥4 of 5 attempts, one visible second hop, every cited URL actually fetched. **Every live search response recorded into `fixtures/search/` in the same session.** Also clears the outstanding Day 1/2 live gaps | `fixtures/search/`, `prompts.md` | S13 | Not started |

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
