# Evergrove Before-Focus Research Agent

A standalone Python service that prepares a focus session *before* the timer starts.

You give it a task and a session length — `"Learn PostgreSQL indexing"`, 25 minutes —
and it returns a validated, source-grounded **focus preparation**: one narrowed
objective, the handful of topics that fit the session, real sources it actually read,
what to deliberately skip, one practice exercise, and one success criterion.

## The problem it solves

Evergrove is a Pomodoro app. A user writes a task and starts a 25-minute timer.

The timer protects the time, but it does not make the task clear. Someone who writes
*"Learn PostgreSQL indexing"* and hits Start spends the first ten minutes of a
twenty-five minute session searching, opening tabs, and deciding what to read. The timer
is running, but the user is still preparing.

This agent moves that preparation before the timer starts.

```
User has a goal        "Learn PostgreSQL indexing", 25 minutes
        ↓
Research Agent         narrows the goal, searches, reads, judges sources
        ↓
Focus Preparation      objective · topics · real sources · what to skip ·
                       one exercise · one success criterion
        ↓
Evergrove timer        user reviews, accepts, presses Start
```

### Why an agent and not a pipeline

Because the right *second* search query cannot be written until the first search results
have been read. Three decisions in this product genuinely need a model — how big a slice
fits one session, whether the sources found actually support it, and what to search for
next when they do not. Everything else is deterministic code, and is written as such.

## Relationship with Evergrove

This is a **separate project in its own repository**. During Phase 2 it does not talk to
Evergrove at all.

| Stays in Evergrove (untouched) | Belongs to the Research Agent |
| --- | --- |
| Timer, sessions, tasks, history | Understanding the task |
| Points, titles, gamification | Web search and source reading |
| Auth, users, profile, theme | Source appraisal |
| PostgreSQL product data | Building the focus preparation |
| React UI | Memory of previous preparations, traces |

- **Research Agent** = prepare the focus session.
- **Evergrove** = run and record the focus session.
- **User** = do the actual work.

The agent never starts the timer, never writes the user's assignment, never becomes a
chatbot, and never returns a curriculum when the user asked for one session.

Integration — a "Prepare This Session" button calling an HTTP wrapper — is **Phase 3**.
No React change, no NestJS change, no Prisma change, no shared database, no
`CONTRACT.md` amendment.

## Phase 2 scope

**In scope (the 7-day build):** web-search skill · file-read plugin (`.txt`/`.md`/`.pdf`)
· session and cross-run memory · a hook that logs every tool call with timestamps ·
multi-hop research · a supervisor routing to two worker agents · an MCP server with one
resource and one tool, plus a client · tracing across the whole agent graph · structured
output with validation · offline tests and five agent evaluations · `prompts.md`.

**Explicitly out of scope:** any Evergrove frontend or backend change · HTTP/FastAPI ·
Docker · LangGraph/LangChain · vector databases or RAG · Redis/Celery · Kubernetes or
cloud deployment · OCR · multi-user auth.

**Cost: $0 mandatory.** The local model path is the default and needs no key. The two
free tiers used (SerpAPI, Google AI Studio) require a signup but no credit card.

## Planned architecture

One process. A modular monolith, layered internally — "multi-agent" describes how the
reasoning is organised, not how many services are deployed.

```
                    CLI  ·  MCP server            ← surfaces
                          │
                     service.py                   ← one entry point
                          │
       Supervisor ──► Researcher ──► Appraiser     ← three prompt+schema pairs
                          │
                   tool registry                  ← the ONLY path to a tool;
                          │                          runs the pre/post hooks
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   search backends   documents/         SQLite
   (serpapi ·        (pdf · html ·      (memory · cache ·
    academic ·        text · excerpt)    budget · traces)
    fixture)
        │                 │                 │
        └──────── schemas/ (Pydantic) ──────┘      ← imports nothing; everything
                          │                           imports it
                       config.py
```

| Decision | Choice |
| --- | --- |
| Language | Python 3.12 |
| Package manager | `uv` |
| Orchestration | Plain Python state machine — no LangGraph, no LangChain |
| Model runtime | Ollama, local |
| Primary model | `qwen3:4b` — sized for a CPU-bound machine |
| Second provider | Google AI Studio (Gemini Flash) free tier, opt-in |
| Web search | SerpAPI free tier + keyless OpenAlex / Crossref / arXiv |
| Storage | One SQLite file, stdlib `sqlite3`, no ORM |
| Surface | CLI first, MCP server on Day 6 |
| Structured output | Pydantic → JSON Schema → constrained decoding → re-validated |

## Current status

**Day 1 of 7 complete; Day 2 in progress.** A task description goes into a model and a
validated `FocusPreparationReport` comes out; the tool registry — the single path every
tool will be called through — exists with no tools registered in it yet; and the
deterministic passage selector that decides how much of a source the model ever sees is
in place, though nothing calls it until the readers land. There is no search, no file
reading, no memory, no tracing, no MCP server, and no multi-agent loop yet.

| Day | Area | Status |
| --- | --- | --- |
| 1 | Project, config, schemas, `LLMProvider` + three providers, first structured round trip | **Done** |
| 2 | Deterministic tools: search backends, fetch, document readers, SQLite cache, registry | **In progress** — registry and passage selector |
| 3 | Single research agent — the core loop (`--mode single`) | Not started |
| 4 | Memory, hooks, tracing | Not started |
| 5 | Supervisor + Researcher + Appraiser (`--mode multi`) | Not started |
| 6 | MCP server, MCP client, hardening | Not started |
| 7 | Tests, five evaluations, requirement audit, final demo | Not started |

What exists today:

```
pyproject.toml · uv.lock · .env.example · .gitignore · README.md · prompts.md
src/evergrove_agent/
  config.py            every tunable value: models, budgets, timeouts, paths
  main.py              CLI — only --no-research works today
  schemas/             task.py · report.py · tools.py   (Pydantic only, imports nothing)
  llm/                 base.py · ollama_provider.py · hosted_provider.py ·
                       fake_provider.py · prompts/finalise.md
  tools/               base.py (Tool protocol · RunContext · hook signatures) ·
                       registry.py (the only path to a tool; hook lists empty until Day 4)
  documents/           excerpt.py (deterministic passage selector — keyword overlap only,
                       no model and no embeddings; readers join it on Day 2)
tests/unit/            test_schemas.py · test_llm_provider.py · test_config.py ·
                       test_main.py · test_tool_registry.py · test_excerpt.py
```

## Prerequisites

| What | Why | Notes |
| --- | --- | --- |
| **Python 3.12+** | The project targets 3.12 | You do not need to install it yourself — `uv` fetches a managed 3.12 if your system has none |
| **[uv](https://docs.astral.sh/uv/)** | Environment, lockfile and runner | `pip install uv`, or the installer on the uv site |
| **[Ollama](https://ollama.com/download)** | The local `$0` model runtime | Only needed to run against a real model. Tests do not need it |
| A Google AI Studio key | Optional second provider | Free, no credit card. Only if you set a role to `hosted` |
| A SerpAPI key | Optional, from Day 2 | Free tier, 250 searches/month. `SEARCH_BACKEND=fixture` needs none |

## Install

```bash
cd "Research Agent"
uv sync                 # creates .venv, installs runtime + dev dependencies
```

`uv sync` is the whole setup. It reads `pyproject.toml`, resolves against `uv.lock`, and
downloads a Python 3.12 interpreter if the machine has none.

## Environment setup

```bash
cp .env.example .env    # Windows: copy .env.example .env
```

Every setting has a working default, so **an empty `.env` is a valid, fully local, $0
configuration**. Edit it only to opt into something:

- `GOOGLE_API_KEY=...` plus `SUPERVISOR_PROVIDER=hosted` (etc.) to use the free hosted
  tier. Be aware this sends the task text to Google; on the free tier that data may be
  used to improve Google's products.
- `SERPAPI_API_KEY=...` and `SEARCH_BACKEND=serpapi` for live search — **from Day 2**.
- Any of the budget values, all of which live in one place (`src/evergrove_agent/config.py`).

`.env` is gitignored. `.env.example` contains no real keys.

### Local model setup

Not yet exercised by this repository — Ollama is not installed on the development
machine, so no local run has been performed and **no tokens/sec figure has been
measured**. When you install it:

```bash
ollama pull qwen3:4b
setx OLLAMA_KEEP_ALIVE 60m        # Windows; bash: export OLLAMA_KEEP_ALIVE=60m
ollama serve
```

`OLLAMA_KEEP_ALIVE` matters more than it looks: without it the 2.6 GB model unloads
between calls and reloads from disk every time, which is 5–20 s of pure waste per model
call and roughly ten calls per run.

> **TODO (Day 1 review):** the mandatory model bake-off — `qwen3:4b` vs `qwen3:1.7b`,
> five structured calls each, recording valid-JSON rate, tokens/sec, time to first
> token, and total wall clock — has **not** been run, because Ollama is absent. Until it
> is, `qwen3:4b` is the plan's recommendation rather than this machine's measurement.
> Results belong in `prompts.md` and in the table below.

| Model | Valid JSON | Decode tok/s | Time to first token | Wall clock |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | TODO | TODO | TODO | TODO |
| `qwen3:1.7b` | TODO | TODO | TODO | TODO |

## Development and testing commands

These all exist and have been run:

```bash
uv sync                        # install / reinstall from the lockfile
uv run pytest                  # the offline suite: no model, no network, no cost
uv run pytest -q               # quiet
uv run pytest tests/unit/test_schemas.py
uv run pytest -m live          # opt in to tests needing a real model (none pass without Ollama)
uv run ruff check .            # lint
uv run python -c "import evergrove_agent, evergrove_agent.main"   # import check
```

The default run excludes anything marked `live`, so `uv run pytest` is safe with no
Ollama, no keys, and no internet.

Ruff is configured in `pyproject.toml` with its default rule set (`E4`, `E7`, `E9`, `F`)
— real errors and unused names, not style opinions — at line length 88. There is still
no type checker; mypy is not in the Phase 2 dependency list.

## Pre-push quality gate

`.githooks/pre-push` blocks `git push` when the offline checks fail. It is
version-controlled, so it is reviewed like any other file rather than living untracked
in `.git/hooks`.

**Enable it once per clone:**

```bash
git config core.hooksPath .githooks
```

That one line is the whole setup — it is a local git setting, so every fresh clone needs
it. On Windows use Git Bash or any shell where `git` is on PATH; the hook is POSIX `sh`
and Git for Windows supplies the interpreter.

On every push it runs, in order, stopping at the first failure:

| Check | Command | Typical cost |
| --- | --- | --- |
| Lint | `uv run ruff check .` | < 1 s |
| Offline tests | `uv run pytest -q` | ~2 s |

**It never spends quota or tokens.** No Ollama, no Gemini/`GOOGLE_API_KEY`, no SerpAPI,
no live HTTP. The live exclusion is not re-implemented in the hook: `pyproject.toml`
already sets `addopts = "-m 'not live' --strict-markers"`, and the hook calls bare
`pytest` so that single setting stays the only source of truth. Anything needing a real
model or key must carry `@pytest.mark.live`, which keeps it out of the gate
automatically.

Pushing a **branch deletion** skips the checks — there is no new code to validate.

When a check fails, the hook names it and aborts before any network traffic:

```
pre-push blocked: ruff lint failed.
  Command: uv run ruff check .
  Fix it and push again, or bypass with `git push --no-verify`.
```

`git push --no-verify` bypasses the gate. It exists for genuine emergencies; the checks
take about two seconds, so routine use of it just moves the failure to CI or to someone
else's clone.

## Running the agent

One mode works today — preparation from model knowledge alone, with no sources:

```bash
uv run python -m evergrove_agent.main --task "Learn PostgreSQL indexing" --minutes 25 --no-research

# same thing, via the installed console script
uv run evergrove-agent --task "Learn PostgreSQL indexing" --minutes 25 --no-research
```

This prints a validated `FocusPreparationReport` as JSON. It needs Ollama running with
`qwen3:4b` pulled, **or** `--provider hosted` with a `GOOGLE_API_KEY` set.

Flags that exist:

| Flag | Meaning |
| --- | --- |
| `--task` | The task title, as the user wrote it (required) |
| `--minutes` | Session length, 5–180. Default 25 |
| `--description` | Optional extra context |
| `--no-research` / `--no-search` | Prepare from model knowledge alone. The only mode today |
| `--provider local\|hosted` | Override the configured provider for this run |
| `--fully-local` | Refuse to start if any role resolves to `hosted` |
| `--indent` | JSON indent; `0` for one line |
| `--attachment` | Accepted but refused with a message — the reader is Day 2 |

Running **without** `--no-research` exits with a message saying research mode is the
Day 3 loop. It does not silently fall back.

> **TODO (Days 3, 5):** `--mode single` (the single-agent multi-hop demo) and
> `--mode multi` (supervisor + two workers, the eventual default), plus `--trace` and
> `--offline`.

## Running one tool directly

The Day 2 tools are usable without an agent or a model at all, which is how a suspect tool
gets ruled out before the loop is blamed for it:

```bash
uv run python -m evergrove_agent.tools.cli search "postgresql b-tree index" --type docs
uv run python -m evergrove_agent.tools.cli fetch https://example.com/page --excerpt-for "b-tree"
uv run python -m evergrove_agent.tools.cli read documents/indexing-brief.md --mode outline
uv run python -m evergrove_agent.tools.cli normalize https://a.dev/x https://a.dev/x/
```

| Command | Tool | Options |
| --- | --- | --- |
| `search QUERY` | `web_search` | `--type docs\|technical\|academic\|general`, `--max-results N`, `--backend NAME` |
| `fetch URL` | `fetch_url` | `--excerpt-for QUESTION`, `--max-chars N` |
| `read PATH` | `read_document` | `--mode full\|outline\|section`, `--section HINT` |
| `normalize URL…` | `normalize_sources` | — |

Every command also takes `--json`, which prints the whole `ToolResult` envelope instead of
the summary. Exit codes: `0` the tool succeeded, `1` the tool refused (its error code and
message, never a traceback), `2` unusable arguments.

With the shipped `SEARCH_BACKEND=fixture`, `search` replays `fixtures/search/` and costs no
quota; `read` resolves a relative path inside `ALLOWED_ATTACHMENT_DIR`, which defaults to
`fixtures/`. `fetch` is the one command that does reach the network. `--backend` overrides
`SEARCH_BACKEND` for a single call.

## Inputs

Today: a task title, a session length, and an optional free-text description
(`TaskContext` in `src/evergrove_agent/schemas/task.py`).

> **TODO (Day 2):** attachments — `.txt`, `.md`, and `.pdf` via `--attachment`, read with
> `outline` / `full` / `section` modes, and constrained to a configured allowed
> directory.

## Outputs

A single `FocusPreparationReport` as JSON — the schema in
`src/evergrove_agent/schemas/report.py`, which is also the MCP tool's return type and the
shape any future Evergrove UI renders. It carries provenance (`run_id`, `generated_at`,
`model_used`), the narrowed goal, topics to cover and skip, resources, an optional
practice exercise, success criteria, and two honesty fields — `assumptions` and
`unknowns` — which are what stop the agent inventing.

Validation runs in three layers: constrained decoding against the JSON Schema → Pydantic
→ business rules. Layers one and two are live. Layer three is the `validate_report` tool
and arrives on Day 2; its most important rule is that **every cited URL must appear in
the set of URLs this run actually discovered or fetched**.

> **TODO (Day 2):** business-rule validation and the retry ladder — attempt 1 primary
> model, attempt 2 primary with the validation errors quoted back, attempt 3 the second
> provider, then fail loudly. A run that cannot produce a valid report never returns a
> partial one.

## Search configuration

`SEARCH_BACKEND` selects the backend and nothing else changes — the agents never know
which one is in use.

| Backend | Role | Key |
| --- | --- | --- |
| `fixture` | Default. Replays recorded results; free, offline, repeatable | No |
| `serpapi` | Primary live search | Yes (free tier) |
| `academic` | OpenAlex / Crossref / arXiv, for scholarly tasks | No |
| `ddgs` | Keyless fallback if the SerpAPI quota runs out | No |

The 250-searches/month free tier is the only genuinely finite resource in the project, so
three deterministic guards protect it: a 7-day search cache, a persistent monthly counter
that refuses live calls past `MONTHLY_SEARCH_BUDGET`, and `fixture` as the committed
default.

> **TODO (Day 2):** none of this is implemented yet — the settings exist, the backends do
> not. Recording real SerpAPI responses into `fixtures/search/` is what makes the rest of
> the week free and offline.

## MCP

> **TODO (Day 6):** an MCP server exposing one resource
> (`evergrove://preparation/{run_id}`) and one tool (`prepare_focus_session`), a
> `.mcp.json` for Claude Code, and a ~40-line custom stdio client in
> `scripts/mcp_demo_client.py`. The server is a thin wrapper over `service.py` — if it
> grows past 100 lines, logic has leaked into it. The `mcp` SDK is deliberately not a
> dependency yet; its 2.x server API differs from the 1.x examples most tutorials show,
> so the version gets pinned and the installed package's own quickstart gets read first.

## Memory

> **TODO (Day 4):** two things share the name. *Session memory* is what the agent knows
> during one run — the goal, findings, appraisals, `seen_urls`, `seen_queries` — and is
> what makes a second research hop possible at all. *Persistent memory* is what survives
> between runs, so `"Continue PostgreSQL indexing"` produces a genuinely different
> session from the first one. Both live in the same SQLite file, alongside the page
> cache, the search cache, the monthly budget counter, and the trace tables.

## Tracing

> **TODO (Day 4):** one `run_id` per run, one `span_id` per operation,
> `parent_span_id` building the tree, written to SQLite and to stdout as structured JSON.
> Every tool call goes through a single registry that runs the pre/post hook chains, so
> timestamps, durations, budget checks, cache lookups and trace rows cannot be bypassed.
> `scripts/show_trace.py <run_id>` renders the ASCII tree.

## Testing

Deterministic code gets unit tests; agent behaviour gets evaluations. The two are never
confused. Everything runs offline by default — `FakeProvider` replays scripted
responses, and the fixture search backend replays recorded results — and the whole suite
must finish in under 60 seconds at zero cost.

Today: 100 unit tests, running in ~1.2 s, covering the report schema and each of its constraints, the tool
result envelope, config defaults and budget overrides, all three providers (via `respx`,
so no model runs), the Gemini schema translation, the Day 1 round trip, the tool
registry (dispatch, unknown tools, invalid arguments, tool failures, hook ordering), and
the passage selector (budget ceiling, relevance, document order, heading context, and the
pages nothing matches).

> **TODO (Day 7):** the five agent evaluations (`evals/`), integration tests across
> search → fetch → read, and the Phase 2 requirement audit.

## Offline demo

> **TODO (Day 6–7):** `--offline` will run the whole pipeline against recorded fixtures —
> saved HTML, saved PDFs, recorded SerpAPI JSON, and recorded model transcripts — so the
> demo works with no internet, no quota, and no waiting on a local model. Today the only
> offline artefact is the test suite.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `could not reach Ollama at http://localhost:11434` | Ollama is not running, or is on another port. Start it with `ollama serve`, or set `OLLAMA_HOST` |
| `Ollama returned 404 … try: ollama pull qwen3:4b` | The model is not pulled |
| `GOOGLE_API_KEY is not set` | A role is set to `hosted` with no key. Either add the key or set every `*_PROVIDER` back to `local` |
| `--fully-local was requested but these roles resolve to the hosted provider` | Working as intended — the flag refuses rather than silently rewriting your config |
| `The model's reply did not satisfy FocusPreparationReport` | The model ignored the schema. Check `TEMPERATURE=0.0`; if a small model does this repeatedly, that is the signal to drop a model size |
| A local run takes minutes | Expected on CPU-only hardware: 4–8 minutes cold, 2–4 warm. Set `OLLAMA_KEEP_ALIVE=60m`, or iterate with `--provider hosted` |

> **TODO:** extend as real failures appear — search quota exhaustion, PDF extraction
> failures, and MCP client version mismatches are the ones the plan expects.

## Privacy

- Task text goes to the **local model**, and never leaves the machine unless you opt in.
- With `--no-research`, the task text reaches no search provider at all. It is the only
  fully private mode.
- From Day 2, search queries derived from the task title do reach SerpAPI. That is
  unavoidable for anything with web search — but it is scoped: no account, no user
  identifier, no attachment content, no session history.
- If any `*_PROVIDER=hosted`, task text and source excerpts go to Google AI Studio. On
  the free tier that data may be used to improve Google's products.
- Attachments never leave the machine. Fetched page text is cached locally in SQLite for
  7 days. Traces and memory are local only.

## Project documents

- `Evergrove_Research_Agent_Architecture_and_7_Day_Plan.NEW.docx` — the architecture and
  7-day plan. It is the source of truth for every decision above.
- `prompts.md` — the AI interaction log and its corrections, required by Phase 2 and
  updated daily.
