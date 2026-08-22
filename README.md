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

**Days 1 and 2 of 7 complete; Day 3 is in progress.** The deterministic half of the
project is finished: five tools behind one registry, three search backends, PDF/DOCX/HTML/
text readers, the SQLite caches and the monthly search-quota guard, all usable offline
from a CLI. The agent loop itself now plans, searches, reads, judges whether it has
enough, performs a genuine second hop, and writes a report that is checked against the
run's own evidence and retried when it fails — and the main CLI runs it, through
`service.py`, the one entry point the Day 6 MCP server will also call.

**The loop has now been run live**, against a real local `qwen3:4b` and real SerpAPI: three
end-to-end runs, three valid reports, every cited URL verified as genuinely fetched, and one run
performing a second hop whose query came from a page the first hop had read. Day 3 is **not signed
off**: its criterion is a valid report on ≥4 of 5 attempts and only 3 were run. What is still
missing is those two runs, memory, tracing, the supervisor/worker split, and the MCP server.

| Day | Area | Status |
| --- | --- | --- |
| 1 | Project, config, schemas, `LLMProvider` + three providers, first structured round trip | **Done** |
| 2 | Deterministic tools: registry, search, fetch, document readers, SQLite caches, fixtures, tools CLI | **Done** |
| 3 | Single research agent — the core loop | **S1–S14 done and live-verified** — sign-off pending 2 more acceptance runs |
| 4 | Memory, hooks, tracing | Not started |
| 5 | Supervisor + Researcher + Appraiser | Not started |
| 6 | MCP server, MCP client, hardening | **Server, client, report storage and the offline demo done** — proven over real stdio; graceful degradation and path safety not started |
| 7 | Tests, five evaluations, requirement audit, final demo | Not started |

Day 3's subtasks are all implemented. S14's live verification found and fixed five defects the
offline suite could not see — the largest being that an unconstrained tool-calling turn cost 361 s
against 46 s for a constrained one on this CPU-only machine. See
`docs/research-agent-context.md`, *S14 results*.

What exists today:

```
pyproject.toml · uv.lock · .env.example · .gitignore · README.md · prompts.md
src/evergrove_agent/
  config.py            every tunable value: models, budgets, TTLs, timeouts, paths
  main.py              CLI — research mode and --no-research
  service.py           the one entry point: composes a run and calls the loop
  schemas/             task.py · report.py · tools.py · agents.py
                       (Pydantic only, imports nothing from the package)
  llm/                 base.py · ollama_provider.py · hosted_provider.py ·
                       fake_provider.py · prompts/ (plan · research_step ·
                       sufficiency · finalise)
  agents/              tool_calling.py (the model ↔ tool bridge) ·
                       prompt_context.py (one renderer per prompt placeholder) ·
                       single_agent.py (the four stage functions, the loop and the
                       report retry ladder)
  tools/               base.py (Tool protocol · RunContext · RunBudget) ·
                       registry.py (the only path to a tool) · wiring.py · cli.py ·
                       web_search · fetch_url · read_document · normalize_sources ·
                       validate_report
  search/              base · normalize · domains · fixture · serpapi · academic
  documents/           reader · excerpt · text · pdf · docx · html
  memory/              db.py (all DDL) · cache · search_cache · budget
tests/unit/            22 suites; tests/conftest.py holds the shared fixtures
fixtures/              search/ recordings · documents/ · html/ · README.md (provenance)
docs/                  research-agent-context.md — the implementation context
.githooks/pre-push     ruff, then the offline suite
```

Not present yet: `evals/`.

## Prerequisites

| What | Why | Notes |
| --- | --- | --- |
| **Python 3.12+** | The project targets 3.12 | You do not need to install it yourself — `uv` fetches a managed 3.12 if your system has none |
| **[uv](https://docs.astral.sh/uv/)** | Environment, lockfile and runner | `pip install uv`, or the installer on the uv site |
| **[Ollama](https://ollama.com/download)** | The local `$0` model runtime | Only needed to run against a real model. Tests do not need it |
| A Google AI Studio key | Optional second provider | Free, no credit card. Only if you set a role to `hosted` |
| A SerpAPI key | Optional | Free tier, 250 searches/month. The shipped `SEARCH_BACKEND=fixture` needs none |

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
- `SERPAPI_API_KEY=...` and `SEARCH_BACKEND=serpapi` for live search. Leaving the default
  `fixture` in place keeps every run free and offline.
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
| Offline tests | `uv run pytest -q` | ~12 s |

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

Two modes. **Research mode is the default** — it plans, searches, reads pages, judges
whether it has enough and hops again if it does not:

```bash
uv run evergrove-agent --task "Learn PostgreSQL indexing" --minutes 25

# same thing, without the installed console script
uv run python -m evergrove_agent.main --task "Learn PostgreSQL indexing" --minutes 25
```

`--no-research` prepares from model knowledge alone, with no sources and no search:

```bash
uv run evergrove-agent --task "Learn PostgreSQL indexing" --minutes 25 --no-research
```

Both print a validated `FocusPreparationReport` as JSON on stdout. Both need Ollama
running with `qwen3:4b` pulled, **or** `--provider hosted` with a `GOOGLE_API_KEY` set.

A research run on this hardware takes minutes, so it prints a live status line to
**stderr** while it works — elapsed time and what it has spent of its model-call, search
and page-read allowances:

```
/ preparing  1:12   model calls 4/10   searches 2/3   page reads 1/4
```

That line is terminal-only and suppressible with `--quiet`, so `evergrove-agent … > out.json`
is byte-identical either way: stdout carries the report and nothing else.

Flags that exist:

| Flag | Meaning |
| --- | --- |
| `--task` | The task title, as the user wrote it (required) |
| `--minutes` | Session length, 5–180. Default 25 |
| `--description` | Optional extra context |
| `--no-research` / `--no-search` | Prepare from model knowledge alone. No search, no fetching, no sources |
| `--provider local\|hosted` | Override the configured provider for this run |
| `--fully-local` | Refuse to start if any role resolves to `hosted` |
| `--quiet` | Suppress the progress line. The report is unaffected |
| `--indent` | JSON indent; `0` for one line |
| `--attachment` | A `.txt`/`.md`/`.pdf`/`.docx` to prepare from. A relative path resolves inside `ALLOWED_ATTACHMENT_DIR`, and the path is checked before the run starts. Not available with `--no-research`, because reading it is a tool call and that path makes none |

A research run that cannot produce a valid report **exits 1 and prints why**. It is never
downgraded to a `--no-research` result: the two modes make different promises about what a
report rests on, and answering one with the other would return a sourceless plan under
flags that asked for sources.

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

Attachments are **read** today — `.txt`, `.md`, `.pdf` and `.docx`, in `outline` / `full`
/ `section` modes, constrained to `ALLOWED_ATTACHMENT_DIR` — through the `read_document`
tool and the tools CLI.

`--attachment` on the main CLI passes one into the research loop: it is what puts
`read_document` on the researcher's menu for that run, so the attachment is read alongside
the sources rather than instead of them.

## Outputs

A single `FocusPreparationReport` as JSON — the schema in
`src/evergrove_agent/schemas/report.py`, which is also the MCP tool's return type and the
shape any future Evergrove UI renders. It carries provenance (`run_id`, `generated_at`,
`model_used`), the narrowed goal, topics to cover and skip, resources, an optional
practice exercise, success criteria, and two honesty fields — `assumptions` and
`unknowns` — which are what stop the agent inventing.

Validation runs in three layers: constrained decoding against the JSON Schema → Pydantic
→ business rules. **All three are live.** Layer three is the `validate_report` tool, and
its most important rule is that **every cited URL must appear in the set of URLs this run
actually discovered or fetched** — a model cannot talk its way past a set. A source that
was found but never opened may only be cited as `authority="unknown"`.

A report that fails any layer is not returned. It is quoted back to the model with the
exact problems and rewritten, up to `MAX_OUTPUT_RETRIES` **total attempts** (default 3:
one initial call plus two corrections), with the last attempt going to the second provider
when one is configured. The retry corrects the report only — it never searches or fetches
again, because a validation failure means the report broke the contract, not that the
research was wrong. If no attempt produces a valid report the run raises
`PreparationFailed` carrying the final errors. **A run that cannot produce a valid report
never returns a partial one.**

> **Note:** this applies to the research loop. The `--no-research` path builds its report
> without layer three — it forces `resources: []` and an `unknowns` entry by construction,
> but the topic-sizing and goal-narrowing rules go unchecked there. Extending the ladder to
> that path is an open decision, deliberately left out of the S12 wiring work.

## Search configuration

`SEARCH_BACKEND` selects the backend and nothing else changes — the agents never know
which one is in use.

| Backend | Role | Key |
| --- | --- | --- |
| `fixture` | Default. Replays recorded results; free, offline, repeatable | No |
| `serpapi` | Primary live search | Yes (free tier) |
| `academic` | OpenAlex / Crossref / arXiv, for scholarly tasks | No |
| `ddgs` | Reserved name for a keyless fallback. **Not implemented** — selecting it raises rather than silently returning nothing | — |

The 250-searches/month free tier is the only genuinely finite resource in the project, so
three deterministic guards protect it: a 7-day search cache, a persistent monthly counter
that refuses live calls past `MONTHLY_SEARCH_BUDGET`, and `fixture` as the committed
default.

All three guards are implemented and the backends work.

**Live SerpAPI is verified.** S14 spent 13 calls of the 200-call budget and captured 12
recordings into `fixtures/search/`, one per distinct live query, each in the same session as the
call. The 5 original `handwritten` recordings remain, and the prohibition applies to those alone:
no report may cite a handwritten fixture.

## MCP

The whole capability is reachable from any MCP client. `src/evergrove_agent/mcp/server.py`
is a thin wrapper over `service.py` — 52 lines of code — and holds no research logic.

```
uv run evergrove-mcp                                  # the server, speaking MCP on stdio
uv run python scripts/mcp_demo_client.py --offline    # the whole exchange, in ~10 seconds
uv run python scripts/mcp_demo_client.py              # list the surface, read the last report
uv run python scripts/mcp_demo_client.py --task "Learn PostgreSQL indexing"
```

`scripts/mcp_demo_client.py` is an MCP client and nothing else: it spawns the server as a
child process, speaks JSON-RPC over its stdin and stdout, and imports nothing from
`evergrove_agent` — a client that reached into the package would be proving the package
works, not that the *protocol* does.

**`--offline` is the demo.** It runs the complete exchange — discover the surface, call
`prepare_focus_session`, receive a validated report, read that same report back through
`evergrove://preparation/{run_id}`, then read one that was never stored so the refusal is
visible too — against `scripts/mcp_offline_server.py`. That is the real server with one
substitution: the model is scripted at the `AgentProviders.from_settings` seam, the same seam
the offline tests use. `service.py`, the tool registry, the fixture search backend, tracing,
grounding and the `prep_report` write are all the shipped ones, and the database is a
throwaway file. It proves the **protocol**, not the research — the report comes back with
`model_used: fake-model` and no `resources`, because the scripted plan opens no page and
grounding rejects a citation the run never fetched.

With no flags the client talks to the real server: it lists what is exposed and reads the
most recent stored preparation. On a fresh clone that prints a not-found, because `data/` is
gitignored and `prep_report` starts empty. With `--task` it does the same full round trip
against the real agent, which takes 9–15 minutes and needs Ollama.

Stdio is covered by `tests/integration/test_mcp_client.py`, which runs the shipped demo
command as a subprocess; `tests/integration/test_mcp_server.py` stays the fast in-process
surface test.

| Surface | What it is |
| --- | --- |
| **Tool** `prepare_focus_session` | `(task_title, session_minutes=25, task_description=None, attachment_path=None)` → `FocusPreparationReport`. Calls the same `service.prepare_focus_session` the CLI does. Its input schema is generated from `TaskContext` and its output schema from the report model, so the CLI and MCP describe one thing. |
| **Resource** `evergrove://preparation/{run_id}` | The stored report for that run, as JSON. |
| **Resource** `evergrove://task/current` | The most recently stored report, so a client with no run id has an entry point. |

`.mcp.json` in this directory points Claude Code at the same server; open Claude Code on the
`Research Agent` directory so `uv run` resolves this project. Run `uv sync` once first — the
`evergrove-mcp` console script has to exist in `.venv` before `uv run evergrove-mcp` can
resolve. A host that spawns from somewhere else wants `uv run --directory <path>
evergrove-mcp`; the absolute path stays out of the committed file.

Storage: a completed report is written whole to `prep_report` by the existing
`save_preparation` tool, beside the lossy summary `prep_memory` keeps for continuation. The
two answer different questions on different keys — see `memory/report_store.py`. Reports
produced before this existed are not readable through the resource.

> **Still Day 6's, not done here:** the graceful-degradation matrix, attachment path-safety
> hardening, and the optional FastAPI wrapper.

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

Today: **380 unit tests across 22 suites, running in ~12 s.** They cover the report and
agent schemas and each of their constraints, the tool result envelope, config defaults and
budget overrides, all three providers (via `respx`, so no model runs), the Gemini schema
translation, the tool registry and its wiring, each of the five tools, the three search
backends and URL normalisation, the document readers and the passage selector, the SQLite
caches and the monthly quota guard, the run budget, the model ↔ tool bridge, the prompt
renderers, and the agent loop — every way it stops, how it degrades when a tool or the
budget refuses, report grounding, and the retry ladder.

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
- With a live search backend selected, search queries derived from the task title do reach
  SerpAPI. That is unavoidable for anything with web search — but it is scoped: no
  account, no user identifier, no attachment content, no session history. The shipped
  default (`fixture`) sends nothing anywhere.
- If any `*_PROVIDER=hosted`, task text and source excerpts go to Google AI Studio. On
  the free tier that data may be used to improve Google's products.
- Attachments never leave the machine. Fetched page text is cached locally in SQLite for
  7 days. Traces and memory are local only.

## Project documents

- `Evergrove_Research_Agent_Architecture_and_7_Day_Plan.NEW.docx` — the architecture and
  7-day plan. It is the source of truth for every decision above.
- `prompts.md` — the AI interaction log and its corrections, required by Phase 2 and
  updated daily.
