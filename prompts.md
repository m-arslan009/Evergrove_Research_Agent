`Title`: Initial Research Agent project setup from the architecture and 7-day plan

`User prompt`: Work only on the Research Agent project. Do not modify the existing
Evergrove frontend or backend. Before making any changes: read and follow all relevant
rules, instructions, and skills inside `.claude/`; open and carefully read the Research
Agent architecture/7-day implementation plan inside the Research Agent folder; treat that
plan as the main source of truth for the project setup, architecture, dependencies,
folder structure, configuration, and Phase 2 constraints; inspect any existing Research
Agent files before creating or changing anything; and do not add unnecessary technologies
or expand the Phase 2 scope.

Set up the initial Research Agent project based on the architecture and Day 1
requirements defined in the plan. Decide yourself: which dependencies are required at
this stage; how the Python project should be initialized; the correct package/folder
structure; development dependencies; environment configuration; initial
schemas/configuration files; and any appropriate initial smoke tests or verification
scripts. Only install or configure dependencies that are justified by the current setup
stage and the plan. Do not implement later-stage functionality prematurely.

Create and maintain the required `prompts.md` file. Create a useful `README.md` for the
Research Agent, using the project plan to document the relevant current information:
what the Research Agent is; the problem it solves; its relationship with Evergrove;
current Phase 2 scope; high-level planned architecture; current project status;
prerequisites; how to install/setup the project and its dependencies; required
environment setup; and development/testing commands that actually exist. Also create
sections that can be expanded later for: running the agent; inputs; outputs; search
configuration; local model setup; MCP; architecture; memory; tracing; testing; offline
demo; troubleshooting. If functionality does not exist yet, mark the section clearly as
TODO rather than inventing commands or behavior.

This is initial project setup only. Do not build the full Research Agent yet. Do not
implement later Day 2–7 features unless they are strictly required to establish the
project foundation. Follow the architecture and dependency decisions in the plan rather
than inventing a new architecture during setup. If the plan contains an obvious
inconsistency with the latest project decisions, identify it before acting on it rather
than silently choosing one side.

After setup: review the complete diff; confirm frontend/backend were untouched; verify
the project installs correctly; run the relevant lint/tests/import checks available at
this stage; verify configuration files contain no real secrets; update `prompts.md` with
the actual result and any corrections; and update README commands so they match what was
actually created and verified.

---

`Title`: Maintain prompts.md under the repository's prompt-recording rule

`User prompt`: Update the `prompts.md` by following the rules mentioned in `.claude`.

---

`Title`: Permanent offline-first development and expensive-call rules for Days 2–7

`User prompt`: Create a permanent development rules file inside `.claude/rules` and follow
it throughout Days 2–7. The rules must enforce: default to offline development using unit
tests, mocks, fixtures, `FakeProvider`, cached data, and `SEARCH_BACKEND=fixture`; do not
run Ollama, Gemini, SerpAPI, live HTTP requests, MCP end-to-end flows, or other
expensive/quota-consuming operations while functionality is incomplete; run live/external
checks only when a meaningful subtask is complete, after a major relevant change, during
required milestone acceptance testing, or when a failure cannot be reproduced offline;
always test in this order: code inspection → focused unit tests → mocks/fixtures → offline
integration tests → live provider/API test → full end-to-end test; stop as soon as a
cheaper test proves the behavior; treat SerpAPI's 250-call quota as scarce — cache results,
never repeat identical live searches unnecessarily, record successful responses as
fixtures, and use live search only when genuinely required; if a live model/API call fails,
inspect and reproduce offline first, fix and pass focused offline tests before retrying the
live call; do not repeatedly re-test already verified infrastructure unless related
code/configuration changed; run focused tests during implementation and the full suite
mainly at completed subtask/day boundaries or after shared-contract/major changes; before
any expensive call, require a clear answer to "What specific uncertainty will this call
resolve, and can it be verified offline instead?"; never skip a live verification when the
requirement specifically depends on real provider/API behavior, performing only the minimum
calls needed; and record important live verifications, benchmarks, failures, and
corrections in `prompts.md` when relevant. Keep the rules short, practical, enforceable, and
project-specific. Do not implement Day 2 functionality while creating these rules.

---

`Title`: Permanent plan-first and dual-explanation communication rules

`User prompt`: Before implementing any feature, module, integration, or major change,
always discuss the plan with the user first. For every discussion, provide both a
**non-technical explanation** (what we are building, why we need it, how it will work, and
what the user/product gains) and a **technical explanation** (main components,
files/modules, inputs/outputs, data flow, dependencies, APIs/models/storage, edge cases, and
tests). Break the work into small subtasks and do not start implementation until the feature
and current subtask are clearly explained. During implementation, inform the user about any
important failure, design change, or scope change in both simple and technical terms. After
implementation, always explain: what was completed; what changed in the code/system; how the
functionality now works; what was tested; whether real APIs/models were tested or only
mocks/fixtures; what is still required; whether the status is **subtask complete, feature
partially complete, feature complete, or blocked**; and what the next subtask is and why.
Never silently redesign the agreed solution or add unnecessary scope. Follow the existing
project plan unless a change is discussed first.

---

`Title`: Git branching rule for major features

`User prompt`: Before implementing any major feature or meaningful functionality, follow
this Git branching rule and add it to the project's development rules. First check the
current branch and existing local/remote branches. If a branch already exists for the
feature, switch to and continue working on that branch. If no suitable branch exists and the
work is a new major feature/functionality, create a clearly named feature branch before
implementation. Do **not** create branches for minor fixes, small updates, comments, tests
added to an existing feature, refactors within the same feature, or small code chunks. Keep
related work for the same feature on the same branch rather than creating multiple small
branches. Create a new branch only when the work represents a meaningful independent
feature, module, integration, or substantial change. Before creating a branch, confirm the
work does not already belong to the current feature branch. Use concise, descriptive branch
names such as `feature/tool-registry`, `feature/web-search`, or `feature/document-reader`.
Do not merge, delete, or push branches unless explicitly required by the current task or
existing workflow. Before starting implementation, report: current branch; whether an
appropriate feature branch already exists; which branch will be used and why. Then continue
with the normal feature planning and implementation rules.

---

`Title`: Branch per major deliverable capability, not per subtask

`User prompt`: Update the existing project branching rules to use **major deliverable
capabilities**, not individual subtasks. Create a new branch only for a meaningful,
independently deliverable feature/capability. Do **not** create branches for individual
subtasks, files, classes, functions, tests, small fixes, refactors, or partial
implementation steps. Before starting work, check the current branch and existing
local/remote branches. If the current branch already represents the capability being
implemented, continue using it. If a suitable branch already exists, switch to it instead of
creating another. Create a new branch only when starting a genuinely separate major
capability. All subtasks required to complete that capability must stay on the same branch.
Branch names must clearly describe **what the branch contains**, not when it was created —
do not use names like `day2-*`, `day3-*`. Prefer concise, meaningful names such as
`feature/research-tools`, `feature/single-agent-loop`, `feature/memory-tracing`,
`feature/multi-agent-orchestration`, `feature/mcp-integration`. Avoid overly narrow names
such as `feature/tool-registry` when the registry is only one subtask of a larger
capability. Do not create a new branch when moving between subtasks of the same capability.
Before creating a branch, ask: "Does this work represent a new deliverable capability, or is
it only part of the current one?" If it is only part of the current capability, stay on the
existing branch. Do not merge, push, rename, or delete branches unless required by the
current workflow or explicitly requested. Before implementation, report: current branch;
capability currently being worked on; whether the current/existing branch is appropriate;
branch being used and why.

---

`Title`: Test-value discipline — prevent unnecessary test growth

`User prompt`: Update the project testing rules to prevent unnecessary test growth. Create
tests only when they protect real behavior, contracts, edge cases, or failure handling that
matters to the feature. Do not create tests just to increase test count or coverage. Avoid
testing trivial getters, constants, obvious assignments, simple wrappers, framework/library
behavior, or implementation details that are already indirectly covered. Prefer a small
number of high-value tests over many repetitive cases. Before adding a test, ask: (1) What
real bug or regression would this catch? (2) Is this behavior already covered by another
test? (3) Can multiple similar cases be combined with parameterization? (4) Is this behavior
important enough to maintain long term? For each new subtask, add only the minimum focused
tests needed for the main success path, important validation/business rules, meaningful
failure paths, and critical integration boundaries. Do not create separate tests for every
minor variation when one parameterized test can cover them. Reuse fixtures and helpers
instead of duplicating test setup. Do not add broad integration/e2e tests until the
underlying functionality is complete. Avoid duplicating the same behavior across unit,
integration, and e2e layers unless each level proves something meaningfully different.
Existing tests should be reviewed before adding new ones — extend or parameterize existing
tests when appropriate instead of creating another file/case. If a feature is simple and
already covered through a higher-value test, adding another dedicated test is optional, not
mandatory. Test count is not a success metric; correctness, regression protection, clarity,
speed, and maintainability are. Keep the offline/default test suite fast and deterministic;
expensive/live provider tests must remain separate. When implementing a feature, briefly
state why each new test is necessary. If a proposed test does not protect meaningful
behavior, do not add it.

---

`Title`: Day 2 Subtask 1 — tool registry only

`User prompt`: Implement **Day 2 — Subtask 1: Tool Registry only**. Do not start document
reading, search, caching, agents, memory, tracing, or other Day 2 work. Implement this flow:
caller → `ToolRegistry.call(...)` → pre-hooks → registered `Tool.run(...)` → post-hooks →
`ToolResult`. Requirements: create `tools/registry.py` and only minimal supporting files if
required; support registration, unique tool-name lookup, and execution through one
`registry.call(...)` path; reuse existing Day 1 schemas/contracts and avoid unnecessary
redesign; add empty pre/post hook support now for Day 4 tracing; unknown tools, invalid
execution, and tool failures must return structured `ToolResult`/`ToolError` instead of
uncontrolled exceptions; keep the registry independent of LLMs, search, HTTP, SQLite, and
specific tool implementations; add focused offline tests for registration, successful
dispatch, unknown tools, duplicate registration, failure handling, and hook ordering. Every
new file, class/feature, and non-obvious function must have a short, descriptive
comment/docstring explaining its responsibility, avoiding obvious or repetitive comments. Do
not run Ollama, Gemini, SerpAPI, live HTTP, or unrelated/full test suites — run only focused
offline tests after the subtask implementation is complete. Do not implement the next
subtask.

---

`Title`: Version-controlled pre-push quality gate

`User prompt`: Configure a **Git pre-push hook** that prevents pushing code when required
offline quality checks fail. This is project tooling, not a major feature, so do not create a
new feature branch unless the current branching rules specifically require it. Prefer a
**version-controlled hook** such as `.githooks/pre-push` with
`git config core.hooksPath .githooks`, rather than an untracked `.git/hooks` script. On every
`git push`, run the project's required offline checks, including `uv run ruff check`, the
normal `pytest` suite excluding live/external tests, and any other fast deterministic
validation already required by the project. **Never run Ollama, Gemini, SerpAPI, live HTTP, or
other quota/token-consuming tests from the pre-push hook.** If any check fails, stop the push
with a clear message identifying the failed check; if all checks pass, allow the push. Keep the
hook fast, deterministic, cross-platform where practical, and avoid unnecessary dependencies.
Reuse existing pytest markers/configuration instead of duplicating test logic. Do not modify
application functionality. Update the appropriate development/rules documentation so future
work preserves this pre-push behavior. Test the hook itself using only offline/local checks
after configuration is complete.


---

`Title`: Day 2 Subtask 3b — deterministic passage / excerpt selector

`User prompt`: Rename the tool-registry branch to some meaningful name that presents the major
deliverable, and implement the Passage / Excerpt Selector as the next subtask of the existing
Research Tools capability. First inspect the current repository structure, config, schemas,
utilities, tests, and coding conventions; do not assume new files/classes are required if the
existing structure already provides an appropriate place. Before coding, explain in both
non-technical and technical form what the feature needs to accomplish, how it fits into the
existing codebase, what existing components can be reused, and the proposed minimal
implementation and why. Functional requirements: given extracted document text and a research
question, deterministically return the most relevant passages within the configured
excerpt-size limit; prefer relevant paragraphs/headings, preserve understandable context and
original order, and return short text unchanged; no LLM, embeddings, database, network, or
external service usage; keep the solution simple, deterministic, fast, and easy to maintain.
Implementation rules: follow existing architecture and naming conventions; reuse existing
config/contracts instead of duplicating them; do not redesign unrelated code or introduce
abstractions without a clear need; add short descriptive comments/docstrings only where they
explain responsibility; add only the minimum high-value tests required to protect meaningful
behavior; use only offline/focused verification — no models, SerpAPI, or live HTTP. After
implementation, explain in non-technical and technical form what changed, why that
implementation was chosen, files/functions affected, tests performed, remaining limitations,
completion status, and the next subtask. Do not implement the next subtask. Do not start
implementation unless explicitly told to.

---

`Title`: Capability context file as the default starting point for Research Tools subtasks

`User prompt`: Create a lightweight capability context file for the current **Research Tools**
capability, e.g. `docs/research-tools-context.md`, and use it as the default starting point for
all related subtasks. The file should be concise and contain: capability purpose and scope;
current feature branch; relevant folders/files; files/folders that should **not** be inspected by
default; ordered subtasks and their dependencies; for each subtask: status
(pending / in progress / complete), **inspect first** files, **inspect only if needed** files,
expected responsibility/output, important dependencies/contracts, what must not be used or
changed; important implementation decisions already made; testing/resource rules specific to this
capability. Rules for using this file: read this context file before every Research Tools
subtask; do not scan the whole repository; inspect only the current subtask's `inspect first`
files initially; use targeted search to locate symbols/references when needed; expand to
`inspect only if needed` files only when the initial context is insufficient; do not open
unrelated modules just for general understanding; treat the context file as a navigation map, not
as a requirement to read every file mentioned; update the relevant subtask section after
implementation with status, files changed, public interfaces, and important decisions; keep the
file short — do not duplicate the full architecture document or implementation details that
already live in code; use meaningful capability names, not `Day 2` naming. Before creating it,
inspect only enough of the current Research Tools structure to accurately map the relevant files.
Do not implement any new feature while creating this context file.

---

`Title`: Source / URL normalization subtask (S4)

`User prompt`: Implement the next Research Tools capability subtask: Source / URL Normalization
only. First read the Research Tools capability context file and inspect only the files listed for
this subtask. Do not scan the whole repository. Expand context only through targeted search if
required. Before coding, briefly explain in both non-technical and technical form: what source
normalization solves; how it fits into search/fetching later; which existing contracts/config can
be reused; the proposed minimal implementation. Required behavior: raw sources → URL
canonicalization → deduplication → domain classification → deterministic authority ranking →
normalized sources. Normalize URLs consistently, including sensible handling of host casing,
fragments, trailing slashes, and tracking parameters such as `utm_*`. Detect duplicate URLs after
normalization and keep one deterministic result. Classify domains using a small maintainable
authority mapping such as `official`, `standards`, `primary`, `secondary`, or `unknown`, following
existing project schemas if already defined. Prefer configuration/data files such as
`domains.yaml` only if they fit the current structure; do not create unnecessary abstractions.
Support deterministic ranking so official/authoritative sources can outrank weaker secondary
sources later. Preserve useful source metadata such as title/snippet/backend where existing
schemas require it. No LLM, embeddings, SerpAPI, live HTTP, SQLite, or external services. Reuse
existing schemas/contracts rather than redesigning them. Add short descriptive responsibility
comments/docstrings for new files and non-obvious functions only; avoid excessive comments. Add
only minimum high-value tests: canonicalization, duplicate collapse, authority classification, and
deterministic ranking. Prefer parameterization over many repetitive tests. Use focused offline
tests only. Do not run Ollama, Gemini, SerpAPI, live HTTP, or unrelated/full test suites. After
implementation, explain in both non-technical and technical form: what changed; files/functions
added or modified; normalization/ranking flow; tests added and why; test results; any limitations;
status: subtask complete / partially complete / blocked; next dependency-first subtask. Update the
Research Tools capability context file with the completed status, relevant files, public
interfaces, and important decisions. Do not start implementation unless user told.

---

`Title`: JSON authority map, one shared classifier, conservative canonicalization

`User prompt`: Use domains.json instead of domains.yaml to avoid introducing PyYAML solely for a
small static authority map. Keep one shared loader/classifier that both source normalization and
later search ranking reuse. Do not duplicate the map. Keep URL canonicalization conservative, and
treat www. equivalence only during authority-domain lookup rather than rewriting every canonical
URL.

---

`Title`: SQLite base layer before any cache

`User prompt`: Implement the next Research Tools capability subtask: SQLite Base Layer only.
Implement a minimal reusable SQLite layer that provides: database path from existing
configuration; connection creation/cleanup; schema initialization mechanism; safe
transaction/commit/rollback handling where needed; reusable helpers only when they remove real
duplication. Use Python's stdlib `sqlite3`; no ORM. Keep the layer independent of agents, LLMs,
HTTP, search providers, and business logic. Do not implement `source_cache`, `search_cache`,
search budgets, memory, or tracing yet. Reuse existing config instead of hardcoding paths. Schema
initialization must be idempotent and safe to run repeatedly. Keep SQL/schema ownership clear so
later tables can be added without redesigning the database layer. Avoid unnecessary
repository/database abstractions. Add short descriptive responsibility comments/docstrings for new
files and non-obvious functions only. Add only minimum high-value offline tests, such as database
initialization, repeat initialization, and basic connection/transaction behavior if not already
naturally covered. Do not run Ollama, Gemini, SerpAPI, live HTTP, or unrelated/full test suites.
Update the Research Tools capability context file. Do not implement caching or the next subtask.


---

`Title`: Provider-independent search backend contract

`User prompt`: Implement the next Research Tools capability subtask: SearchBackend Interface only.
Implement a provider-independent search backend contract that allows future search
implementations to be swapped without changing callers. Define the shared `SearchBackend`
abstraction/protocol and only minimal related types if they do not already exist. Reuse existing
`SearchResult`, source-type enums, schemas, and error contracts where available; do not duplicate
or redesign them unnecessarily. Keep the interface independent of SerpAPI, HTTP, SQLite, LLMs,
agents, and specific provider logic. Do not implement SerpAPI, fixture, academic, caching, quota
logic, or `web_search` yet. Keep the contract small and focused on the behavior every search
provider genuinely shares. Do not create abstractions for hypothetical future providers unless
required by the current plan. Add short descriptive responsibility comments/docstrings for new
files/classes/non-obvious functions only. Add only minimal high-value tests if the contract has
meaningful runtime behavior; do not create tests merely for a `Protocol` or type declaration. Use
focused offline verification only. No Ollama, Gemini, SerpAPI, or live HTTP. Update the Research
Tools capability context file. Do not implement search providers or the next subtask.


---

`Title`: Document Reader with deterministic .txt/.md/.pdf/.docx reading

`User prompt`: Implement the next Research Tools capability subtask: Document Reader only.
Implement deterministic reading for .txt .md .pdf .docx. Support `full`, `outline`, and `section`
modes according to existing schemas. TXT/Markdown should handle normal UTF-8 and malformed
encoding safely. PDF reading should extract text and useful structure using the planned PDF
library. DOCX reading should extract paragraphs/headings and preserve enough structure for
`outline` and `section` modes. Use a lightweight appropriate library only if required; avoid
unnecessary dependencies. Reuse the existing excerpt selector when relevant instead of duplicating
text-selection logic. Handle meaningful failures through existing structured `ToolResult` /
`ToolError` codes rather than uncontrolled exceptions, including file not found, empty file,
unsupported type, corrupt/encrypted/scanned PDF, corrupt DOCX, and configured size/path limits.
Do not implement URL fetching, search, caches, agents, memory, or tracing. No Ollama, Gemini,
SerpAPI, or live HTTP. Follow existing architecture and reuse contracts instead of creating
duplicate abstractions. Add short descriptive comments/docstrings for new files and non-obvious
functions only. Add only minimum high-value tests for normal document types and genuinely
different failure behavior; parameterize similar cases instead of creating excessive tests. Run
only focused offline tests after the functionality is complete. Update the Research Tools
capability context file. Do not implement the next subtask.

---

`Title`: SQLite-backed source cache for fetched content

`User prompt`: Implement the next Research Tools capability subtask: Source Cache only. Implement
persistent SQLite-backed caching for fetched source content. Reuse the existing SQLite base layer;
do not create separate DB connection logic. Reuse URL canonicalization so equivalent URLs map to
the same cache entry where appropriate. Store the useful source fields required by later
`fetch_url`, such as canonical/final URL, title, extracted text, content type, fetch timestamp, and
expiry. Support deterministic cache get/set behavior and configured TTL. Expired entries must not
be returned as valid hits. Keep cache logic independent of HTTP fetching, search, agents, LLMs, and
tracing. Do not implement `fetch_url`, search cache, budgets, or live requests yet. Avoid
unnecessary generic repository/cache abstractions. Add short descriptive responsibility
comments/docstrings for new files and non-obvious functions only. Add only minimum high-value
tests: miss → store → hit, canonical URL reuse, and expiry behavior. Parameterize where useful. Use
focused offline tests only. No Ollama, Gemini, SerpAPI, or live HTTP. Update the Research Tools
capability context file. Do not implement the next subtask.

---

`Title`: Search cache and monthly live-search budget guard

`User prompt`: Implement the next Research Tools capability subtask: Search Cache + Monthly Budget
Guard only. Implement persistent SQLite-backed search caching and monthly live-search budget
tracking. Search cache: reuse the existing SQLite base layer; build deterministic cache keys from
the normalized query and only parameters that genuinely affect results, such as backend and result
limit where applicable; equivalent searches should reuse the same entry where safe; store enough
metadata to reconstruct the existing search-result schema without provider-specific coupling;
respect configured search-cache TTL; expired entries must behave as misses; keep cache code
independent of SerpAPI HTTP calls and agents. Monthly budget guard: implement persistent monthly
usage tracking for quota-consuming search backends; track usage by calendar month so a new month
naturally gets a fresh budget; reuse existing configuration for the monthly limit; do not hardcode
250 if configuration already exposes the value; cache hits must not consume budget; fixture/offline
backends must not consume live-search budget; avoid race-prone read-then-write logic where a simple
atomic SQLite operation can provide safer behavior; follow existing error/schema contracts instead
of inventing unrelated result types; keep the budget component provider-independent enough for
`web_search` to use later, while avoiding unnecessary abstractions. Do not implement SerpAPI HTTP
calls, academic search, fixture backend behavior, `web_search`, `fetch_url`, agents, LLMs, or
tracing. No Ollama, Gemini, SerpAPI, or live HTTP calls. Add only minimum high-value offline tests
covering: search miss to store to hit, equivalent deterministic cache keys, expiry, cache hit not
affecting budget, budget increment, monthly limit enforcement, and month separation/reset behavior.
Parameterize related cases rather than creating excessive tests. Run only focused offline tests.
Update the Research Tools capability context file.

---

`Title`: Search backend implementations (fixture, SerpAPI, academic)

`User prompt`: Implement the next Research Tools capability subtask: Search Backend
Implementations only. Before implementation create an implementation plan. Explain what a search
backend is, how it works, why we need it, what technology we are using, why such technology fits
here and what the other alternatives are. Stay on the existing Research Tools capability branch.
Read the capability context file first and inspect only the files listed for this subtask. Inspect
the existing SearchBackend contract, search schemas, configuration, normalization contracts, and
relevant fixtures before designing anything. Do not scan the whole repository. Before coding,
briefly explain in both non-technical and technical form: what each backend is responsible for; why
all backends must expose the same contract; what provider-specific behavior belongs inside a backend
versus what belongs later in `web_search`; the minimal implementation proposed. Implement: (1)
Fixture backend — primary development/testing backend, reads deterministic recorded search responses
from existing/planned fixtures, no network calls, same search-result schema as every other backend,
clear structured failure when the requested fixture does not exist. (2) SerpAPI backend — minimal
SerpAPI HTTP integration using the existing HTTP/config stack, API key from existing
settings/environment configuration, parse only fields needed by the project's search-result schema,
handle provider/network/invalid-response failures through existing structured error contracts, no
retry complexity unless already defined by the project, no live SerpAPI request during normal
implementation/testing. (3) Academic backend — the academic sources required by the project plan
such as OpenAlex, Crossref and/or arXiv per the existing architecture/context, provider-specific
parsing kept internal, results mapped into the same shared result schema, no unnecessary
abstractions if one small backend can cleanly support the planned providers. Backends do: query →
provider request / fixture lookup → provider-specific parsing → shared SearchResult objects. They do
not own the search cache, monthly budget accounting, source deduplication, authority/domain ranking,
`web_search` orchestration, agent logic, or LLM usage. Reuse the existing SearchBackend, schemas,
errors, configuration, and HTTP conventions; do not duplicate contracts. Add short descriptive
comments/docstrings only for files/classes/functions whose responsibility is not obvious. Add only
minimum high-value offline tests: fixture replay, missing/malformed fixture failure, SerpAPI parsing
with mocked HTTP, meaningful SerpAPI/provider failure, academic parsing with mocked/fixture
responses, and conformance to the shared backend behavior where useful. Parameterize similar
provider cases. Use mocks/fixtures only — do not call SerpAPI, OpenAlex, Crossref, arXiv, Ollama,
Gemini, or any live HTTP service.

---

`Title`: Tests run at push time, not during a session

`User prompt`: Tests should be run when pushing to repo.
