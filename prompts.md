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

---

`Title`: fetch_url implementation

`User prompt`: Implement the next Research Tools subtask: fetch_url. First read
docs/research-tools-context.md and inspect only the relevant existing files/contracts. Do not scan
the whole repo. Before coding, briefly explain in non-technical + technical form what fetch_url
should do and the minimal flow you plan to use. fetch_url receives a known URL and returns clean,
structured readable content for later agent use. Requirements: reuse existing URL
normalization/canonicalization; reuse Source Cache, and a valid cache hit must avoid HTTP; fetch
through the existing HTTP/config setup only on cache miss; handle redirects/final URL correctly;
extract readable text from HTML using the existing/planned extraction approach; for PDFs reuse the
existing document/PDF reader and do not duplicate PDF parsing/error handling; reuse the existing
excerpt selector for long content when appropriate; respect existing timeout, size, excerpt and
cache limits; cache useful successful results according to the current Source Cache contract;
return failures through existing ToolResult / ToolError / ErrorCode contracts; handle meaningful
cases such as invalid URL, timeout/network failure, HTTP errors, unsupported content, empty
extraction and unreadable PDF. Decide the exact internal flow yourself after inspecting the
existing code. Prefer minimal composition of existing components over new abstractions. If there
is a design choice such as what representation to cache or how PDF bytes should reach the document
reader, choose the approach that best fits current contracts and explain it. Scope: do not
implement web_search, search routing/providers, agents, LLMs, memory or tracing — fetch_url only
retrieves a URL already chosen by the caller. Testing: offline only; use mocked HTTP and existing
fixtures/helpers; add only high-value tests for cache hit, HTML success, PDF path, redirects where
relevant, major HTTP/network failures and unusable content; avoid duplicate/excessive tests; no
live websites, SerpAPI, academic APIs, Gemini or Ollama. Update the fetch_url section in
docs/research-tools-context.md.

---

`Title`: HTML extraction uses the standard library, not trafilatura

`User prompt`: [Design decision, chosen from the options presented] Stdlib now, trafilatura later
if quality is poor — ship documents/html.py behind one extract_html() entry point, and swap the
internals to trafilatura in S8 only if real recorded fixtures show the stdlib output is too noisy.
Add FETCH_TIMEOUT_S and MAX_FETCH_BYTES as their own config settings rather than reusing
SEARCH_TIMEOUT_S and MAX_DOCUMENT_BYTES.

---

`Title`: Source Cache stores reusable source text, not a question-specific excerpt

`User prompt`: Fix the current fetch_url / Source Cache semantics before moving to web_search.
Source Cache must store reusable extracted source text, not a question-specific excerpt. If the
current implementation truncates cached text using the small excerpt/agent-response limit, change
that behavior. A later request for a different excerpt_for must still be able to select different
passages from the same cached source. Desired behavior: fetch URL → extract reusable text → store
reusable text in Source Cache → for each request, select_passages(excerpt_for) → return smaller
question-specific excerpt. If a safety ceiling is needed for cached extracted text, reuse or add an
appropriate source-level limit; do not use the small question-specific excerpt limit for cache
storage. Also improve SQLite cache failure handling: cache read/write failures must remain
non-fatal; fetch_url should still return successfully fetched content; but do not silently swallow
sqlite3.Error with no visibility — log/warn using the project's existing logging approach if one
exists. Do not change: PDF temporary-file approach; document reader contracts; redirect cache
aliasing unless required by existing tests; HTML extraction design; web_search. Add only focused
tests that prove: the same cached source can produce different excerpts for different excerpt_for
values; a cache write/read SQLite failure does not make fetch_url fail; the failure is observable
through logging/warning.

---

`Title`: Implement the `web_search` tool on the existing S7 pieces

`User prompt`: Plan the feature first. Explain the feature, how it is useful in our research
agent, what tech we are using and why (answered independently), what the alternatives are and why
they don't fit. After approval, implement web_search. First read
docs/research-tools-context.md and inspect only the relevant search/cache/backend/normalization/
tool-contract files; do not scan the whole repo. Decide the exact implementation based on existing
contracts; do not force a new architecture if the repo already has the right pieces. Required
behavior: accept the existing search input (query, source_type, result limit); check Search Cache
first; a cache hit must return immediately with no backend call and no budget usage; use the
existing backend factory/selection; SEARCH_BACKEND=fixture must stay fully offline and must never
fall through to live APIs; in live mode route using the existing source intent, e.g. academic →
AcademicBackend, docs/web/general → SerpAPIBackend; apply the monthly budget guard only to
quota-consuming live searches; reuse existing source normalization, canonicalization,
deduplication, authority classification and ranking; cache successful normalized results; return
failures through the existing ToolResult / ToolError / ErrorCode contracts. Keep responsibilities
separate: web_search must not reimplement SerpAPI/OpenAlex/Crossref/arXiv parsing, fetch_url,
agent reasoning, query rewriting, semantic cache matching, memory, or tracing. Testing for this
subtask must be offline only using fixtures/mocks — high-value tests only for cache hit/miss,
backend routing, fixture isolation, budget blocking, normalization/dedupe/ranking, and structured
failures. Do not run live SerpAPI, academic APIs, websites, Gemini, or Ollama yet. Prepare the
design so live acceptance tests can be added afterwards as a separate marked suite (for example
@pytest.mark.live) excluded from normal development and pre-push tests.

`Title`: Live-mode backend routing and fallback cache key

`User prompt`: [Decision on two options offered during planning] Academic-only override:
SEARCH_BACKEND=fixture always stays fixture; when a live backend is configured, source_type
'academic' is redirected to AcademicBackend, and docs/technical/general use whatever
SEARCH_BACKEND names — never redirect a query onto a metered backend the user did not configure.
Truthful fallback cache key: when the ladder falls back to source_type 'general', cache the
results under 'general', the parameters that actually produced them.

`Title`: Final tool registry wiring

`User prompt`: Implement the next Research Tools subtask: Final Tool Registry Wiring. First read
docs/research-tools-context.md and inspect only the registry, completed tools, schemas/contracts,
and existing registration/bootstrap code. Do not scan the whole repo. Before coding, briefly
explain in non-technical + technical form how completed tools should be exposed through one
central registry and what minimal wiring you propose. Required behavior: register the completed
Day 2 tools in the existing ToolRegistry; reuse the existing tool names, schemas,
ToolResult / ToolError contracts, and registry APIs; ensure normal callers use the registry path
instead of bypassing it; wire only tools that are actually complete at this point, such as
read_document, fetch_url, web_search; preserve the existing pre/post hook extension points for
Day 4 and do not implement logging/tracing hooks yet; keep registration deterministic and prevent
duplicate tool-name conflicts according to the current registry contract; do not move
provider/cache/business logic into the registry; do not implement memory, agents, tracing, MCP, or
new tools. Prefer the smallest composition that fits the existing project. Do not create a new
dependency-injection/container framework just for wiring. Testing stays offline: add only
high-value checks that prove expected tools are registered, lookup/call through the registry
works, duplicate/unknown tool behavior follows existing contracts, and registry calls reach the
real completed tool boundary without live external services. Use mocks/fixtures where needed. Do
not call SerpAPI, academic APIs, live websites, Gemini, or Ollama. Update the relevant section in
docs/research-tools-context.md.

`Title`: Wiring scope and placement decisions

`User prompt`: [Decision on two options offered during planning] Register all four completed
tools including normalize_sources, and place the factory in a new tools/wiring.py rather than in
tools/__init__.py.

`Title`: Fixtures and offline replay data

`User prompt`: Implement the next Research Tools subtask: Fixtures and Offline Replay Data. First
read docs/research-tools-context.md and inspect only the existing fixture backend, search
backends, document fixtures, fetch/search tests, and fixture directory structure. Do not scan the
whole repo. Before coding, briefly explain in non-technical + technical form what fixtures are for
and the minimal fixture strategy you propose. Required behavior: create a small, representative
set of deterministic fixtures for offline development/testing; reuse the existing self-describing
search-fixture format and FixtureBackend lookup rules; include only fixtures that support real
current behavior, and do not create large amounts of fake data; cover representative cases for
search results, HTML content, and PDF/TXT/DOCX only where existing tests/tools need them; clearly
mark fixture provenance such as handwritten, serpapi, or academic provider where supported;
handwritten fixtures are acceptable as seed data, but must not be treated as production knowledge;
do not duplicate the same data across multiple fixture formats unnecessarily; keep fixtures stable
and human-readable. For search fixtures, ensure the metadata needed for deterministic lookup is
present, e.g. query, source type, provider/provenance, and normalized results. Do not make live
API calls in this subtask. Real SerpAPI/OpenAlex/Crossref/arXiv responses will be recorded later
during the explicit live-acceptance step. Add only minimal tests necessary to prove fixture
loading/replay works and missing/malformed fixtures fail through existing structured contracts.
Avoid duplicate tests already covering the same behavior. Update the fixtures section in
docs/research-tools-context.md.

`Title`: Tools CLI

`User prompt`: Implement the next Research Tools subtask: Tools CLI. First read
docs/research-tools-context.md and inspect only the existing tool registry, completed tools,
schemas/contracts, and current CLI/main entry points. Do not scan the whole repo. Before coding,
briefly explain in non-technical + technical form what the CLI should expose and the minimal
command structure you propose. Build a thin CLI that lets a person run the completed tools
directly, with no agent or LLM involved. Required commands: search, fetch, read. They should call
the existing Tool Registry / tool contracts, not duplicate tool logic. Required behavior: search
runs web_search with query, source type, result limit, and optional backend override if already
supported; fetch runs fetch_url for a known URL, with optional excerpt/question input if the
existing schema supports it; read runs read_document with path and supported mode such as full,
outline, or section. Use existing schemas, enums, validation, registry calls, and structured
errors. Print useful structured output that is easy to inspect manually. Tool failures should
print their error code/message cleanly rather than crash with a traceback. Keep the CLI thin:
argument parsing + registry call + output formatting only. Do not add agent reasoning,
search/provider logic, cache logic, memory, tracing, MCP, or LLM behavior. Reuse the project's
existing CLI approach/library if one already exists; otherwise choose the smallest stdlib/simple
solution that fits the repo. The CLI should support the Day 2-style flows, for example: `uv run
python -m evergrove_agent.tools.cli search "postgresql indexing" --type docs --backend fixture`,
`uv run python -m evergrove_agent.tools.cli fetch https://example.com/page`, `uv run python -m
evergrove_agent.tools.cli read path/to/file.pdf --mode outline`. Testing must be offline only.
Add only high-value tests proving argument parsing/wiring, successful tool execution through the
registry, and clean structured failure output. Use fixtures/mocks; do not call live websites,
SerpAPI, academic APIs, Gemini, or Ollama. Update the CLI section in
docs/research-tools-context.md.

`Title`: Tools CLI output, exit codes and a fourth command

`User prompt`: (decisions taken during planning) Print a human-readable summary by default, with
--json for the full ToolResult envelope. A tool failure exits 1 (0 success, 2 bad arguments),
superseding the earlier "a failure exits 0" note in docs/research-tools-context.md. Add a
`normalize` command as well as search/fetch/read, so every registered tool has a direct CLI path.

`Title`: Offline integration / acceptance verification of the Day 2 stack

`User prompt`: Implement the next Research Tools subtask: Offline Integration / Acceptance
Verification. Verify the Day 2 stack offline only: read_document works through CLI/registry;
fetch_url works with mocked/local fixture data and Source Cache; web_search works with
FixtureBackend and Search Cache; normalize_sources works through the registry; cache miss → work
→ cache hit behavior is correct; search cache hits do not consume budget; fixture mode never
falls through to live APIs; important failures return structured error codes instead of
tracebacks; CLI commands return usable output and correct exit codes. Prefer a small number of
high-value integration tests. Do not duplicate unit tests that already prove isolated behavior.
Run only offline tests. Do not call SerpAPI, OpenAlex, Crossref, arXiv, real websites, Gemini, or
Ollama. If failures are found, fix only issues required for Day 2 acceptance. Do not redesign
completed components unnecessarily.

`Title`: Close the Research Tools capability and merge it into the main line

`User prompt`: Moving towards our next major deliverable, make sure the research tool is complete
and works correctly if already tested and confirmed. Then merge the research-tool branch into
main, but keep the research branch as it is to work with it in future if required.

`Title`: Rename and repurpose the context document as the project source of truth

`User prompt`: Before starting Day 3 Subtask 1, update the project context documentation. We have
completed Day 1 and Day 2 only; no Day 3 subtask has been implemented yet. Analyze the entire
repository, not only the existing context file, and review the actual implementation from Day 1
and Day 2. Rename the Day 2/tool-focused context document to docs/research-agent-context.md,
using git mv to preserve history — the new name reflects that this file now represents the whole
Research Agent project, not only the tools. Rewrite it as a concise, component-oriented technical
context document rather than an S1–S10 chronological build diary, containing: project purpose and
architecture, current repository map, Day 1 and Day 2 completed summaries, core
contracts/interfaces future work must preserve, existing LLM architecture, tool/registry
architecture, search/fetching/documents/SQLite-cache, configuration relevant to upcoming work,
testing and offline strategy, important engineering decisions, verified deviations from the
original 7-day plan, reuse/dependency guidance, known limitations and not-yet-implemented work,
the current Day 3 milestone, the Day 3 subtask breakdown, and rules for maintaining the document.
Keep completed work summarized; do not keep detailed history of how it was implemented unless it
prevents a future AI from making a wrong architectural decision, and do not remove important
contracts, invariants, deviations or dependencies. Repository reality is authoritative: document
what was actually verified and do not "restore" the original plan where the repository
intentionally differs. Clearly separate "implemented now" from "planned for later". After this
rewrite the document becomes the primary implementation context future AI sessions read first,
but the repository remains the ultimate evidence — if the two disagree, inspect the code and
correct the document. Do not include the stale README edit in this task; record it as a known
documentation issue only.

`Title`: Day 3 subtask decomposition

`User prompt`: Use this Day 3 subtask decomposition, and do not combine agent schemas and
tool-spec integration into one subtask. Day 3 Subtask 1 is Agent schemas: create the typed
Pydantic contracts needed by the four reasoning boundaries — decide_next_step(),
run_research_step(), judge_sufficiency() and finalise() — primarily in schemas/agents.py, reusing
existing Day 1/2 schemas and containing no loop, prompts or model calls. Day 3 Subtask 2 is
model-facing tool integration: implement the missing bridge between the existing tool system and
LLM tool calling — registered Tool to the existing llm.base.ToolSpec, decide which existing tools
are advertised during each reasoning stage, validate model tool arguments, dispatch through the
existing ToolRegistry, and never bypass or duplicate existing tool implementations. Decompose the
remaining Day 3 work logically from the Day 3 plan, covering at minimum agent prompts, the
single-agent core loop, task narrowing, sufficiency judgement, a genuine second research hop with
MAX_HOPS, in-memory Day 3 budgets, validate_report and grounding, structured finalisation and
retries, service.py, CLI integration, integration tests, and live end-to-end verification. Keep
the subtasks small enough that each can be planned, approved, implemented and tested
independently.

`Title`: Day 3 Subtask 1 — agent schema design principles

`User prompt`: Design and implement the typed contracts/schemas needed by the Day 3 single-agent
reasoning flow, primarily in schemas/agents.py, supporting the logical boundaries around
decide_next_step(), run_research_step(), judge_sufficiency() and finalise(). Do not assume the
exact schemas, fields, enums, or number of models in advance — analyze the existing architecture
and decide the minimum clean design that best fits this project. Reuse existing Day 1/2 schemas
and contracts wherever possible; do not duplicate models that already represent the required data.
Prefer the minimum necessary schemas. Use Pydantic v2 and existing project conventions. Keep these
schemas independent from Ollama, Gemini, SerpAPI, CLI, or any provider-specific implementation.
Keep schemas focused on data contracts; do not put orchestration/business logic inside them. Avoid
loosely typed dictionaries when a meaningful typed contract is justified. Use constrained
values/enums only where they genuinely improve correctness. Think about Day 5 compatibility so
today's contracts do not require a rewrite later. Do not redesign stable Day 1/2 contracts unless
there is a strong project-specific reason — flag such a need in the plan instead. Scope is only
the agent schemas/contracts and their focused tests: no loop, prompts, LLM calls, tool
integration, tool dispatch, validate_report, multi-hop execution, budget enforcement, service.py,
CLI changes, or Day 4/5 work. If something required for a later subtask is discovered, record it
as a dependency or follow-up rather than implementing it now. After implementation, update
docs/research-agent-context.md — only the relevant sections, keeping it concise rather than an
implementation diary.

`Title`: SearchSourceType gets one definition, in schemas/

`User prompt`: [Decision on a design question raised during Day 3 S1] Move SearchSourceType into
schemas/ rather than duplicating the literal in schemas/agents.py, and have search/base.py import
it. One definition, even though it edits a stable Day 2 contract.

`Title`: Raise the hop cap from 2 to 3

`User prompt`: Increase the hops from 2 to 3 and continue implementation.

`Title`: Never run the test suite

`User prompt`: Create tests only. I already configured the pre-push hook.

`Title`: Day 3 Subtask 2 — the LLM ↔ tool-system bridge

`User prompt`: Build the missing integration layer that allows the LLM/agent layer to use the
existing tools safely. Conceptually, we need this flow: Existing registered Tool → model can
understand the tool → model requests a tool call → arguments are validated → existing ToolRegistry
executes it → ToolResult is returned for later agent reasoning. Explain the complete tool-calling
flow. For each logical component you propose, explain what is it, why is it, what it produce, what
is recieved, what conditions effect it, tech and its alternates. Reuse the existing ToolRegistry.
Reuse existing input/output Pydantic models. Keep provider-specific details out of this
integration where possible. Keep tool advertisement separate from actual tool implementation.
Invalid model requests should be recoverable structured outcomes where appropriate, not
uncontrolled crashes. Do not let the model bypass registry validation or execution. Preserve
offline testability using existing test infrastructure. Keep the design small enough to remain
understandable when Day 4 hooks/tracing wrap the registry and Day 5 splits the agent roles. Avoid
unnecessary abstraction layers. This subtask is only the LLM ↔ tool-system bridge and its focused
tests.

`Title`: S2 design decisions — module home, bridge scope, conditional attachment tool

`User prompt`: [Decisions taken on the S2 plan] Put the bridge in agents/tool_calling.py rather
than in tools/. The bridge stops at returning the ToolResult — rendering a result back into a
prompt or Message stays with S3/S6. Advertise read_document only when the task actually has an
attachment.

`Title`: Day 3 Subtask 3 — the four reasoning-stage prompts

`User prompt`: Design the prompts required by single-agent reasoning stages. The expected logical
stages are decide_next_step(), run_research_step(), judge_sufficiency(), finalise(). Likely prompt
files belong under the existing llm/prompts/. Do not assume exact prompt contents or structure in
advance. Analyze the schemas, tool bridge, existing finalise.md, and project requirements, then
propose the smallest clean prompt design. Before implementation, explain each reasoning stage in
simple non-technical. The prompts should: produce outputs compatible with the typed schemas;
clearly separate the responsibilities of the four reasoning stages; keep task scope constrained by
session_minutes; prevent the agent from turning one focus session into a large curriculum; tell the
research stage how to use only the tools exposed; avoid inventing tools, URLs, evidence, or
unsupported facts; make tool failures recoverable rather than encouraging hallucinated
replacements; make the sufficiency stage explicitly identify missing information when evidence is
incomplete; allow a follow-up research question to be derived from the evidence collected in the
previous hop; keep the finalisation stage focused on producing the existing FocusPreparationReport;
preserve assumptions and unknowns when information is incomplete; avoid duplicating deterministic
validation rules that belong in later validate_report logic; stay provider-independent where
possible. Do not over-engineer prompts with excessive instructions that small/local models may
struggle to follow. Prefer concise, explicit constraints and structured outputs. Respect the
tool-advertisement rules established. The prompt itself should not invent a second tool-selection
mechanism. If prompt design reveals a requirement for one of those later subtasks, record it as a
dependency instead of implementing it now.

`Title`: S3 design decisions — compact finalise evidence, renderers in agents/

`User prompt`: [Decisions taken on the S3 plan] With NUM_CTX at 4096, sufficiency.md carries the
full excerpts and finalise.md carries a compact block instead (title, url, authority, read/unread,
snippet, plus the researcher's notes, the judge's missing_information and any tool failures) — no
new tunable and no config change. Put the placeholder renderers in agents/prompt_context.py rather
than in llm/prompts/, so the prompt loader keeps its independence from schemas/ and config.

`Title`: Day 3 Subtask 4 — minimum runtime state for the single-agent loop

`User prompt`: Build the minimum runtime state needed for the single-agent loop to remember what has
happened during one research run and enforce its limits in memory. This state will later allow the
agent to: remember the narrowed goal, preserve findings between research hops, avoid repeating
searches/fetches, retain gathered sources and tool failures, know the current hop and latest
sufficiency result, track search/fetch/model usage, stop safely when a configured limit is reached.
Do not assume the exact classes, fields, helper functions, or file locations in advance. Analyze the
existing architecture and propose the smallest clean design. When a limit is reached, the future
loop should be able to detect it and finalise honestly with the information already collected
instead of continuing indefinitely or crashing. Reuse existing schemas and settings. Keep state
strongly typed where useful. Avoid duplicating information already owned by another canonical
object. Keep state separate from prompt rendering. Keep provider-specific details out of state. Do
not put the entire agent orchestration inside state methods. Prefer simple state mutation/update
helpers only when they reduce duplication or protect invariants. Preserve information needed for
later grounding and multi-hop research. Keep Day 4 compatibility in mind, but do not implement Day 4
prematurely. Avoid introducing database persistence for session state in this subtask.

`Title`: S4 design decisions — ledger only, and a refusal is a boolean

`User prompt`: [Decisions taken on the S4 plan] RunState already owns everything the run has seen
(narrowed goal, findings, sources, failures, dedupe sets, hop, verdict), so S4 adds only the spend
ledger and leaves schemas/agents.py untouched. When the budget has nothing left, claim() returns
False rather than raising or building a ToolResult — the ledger stays free of the tool envelope, and
Day 4's pre-hook turns the same False into ToolResult(BUDGET_EXCEEDED).

`Title`: Day 3 S5–S8 — the single-agent orchestration loop

`User prompt`: Implement the single-agent orchestration loop that connects the components
already built. Conceptually, the agent should be able to: understand/narrow task → research →
observe results → judge sufficiency → optionally research again → stop/finalise. Also give one
practical example showing the state transitions for a task. Inspect the existing schemas/prompts
and decide how these should be wired in the single-agent implementation. The loop must support
early stopping: hop 1 → sufficiency check; if sufficient → finalise; if insufficient and a
meaningful follow-up exists, and hop/budgets allow it → next narrower research hop; repeat only
until sufficient or the configured maximum is reached. A later-hop question must be derived from
previously gathered evidence/requested_followup, not from a pre-written fixed sequence. Use budget
ledger. A failed budget.claim(...) is normal control flow. The loop should decide how to
degrade/stop appropriately; RunBudget itself should remain unaware of prompts, tools, or error
envelopes. Plan graceful behaviour for: unknown/malformed model tool request; tool argument
validation failure; ToolResult containing a tool error; search/fetch failure; model/provider
failure where recoverable; hop limit reached; budget exhausted; insufficient evidence after the
final allowed hop. Expected limitations should lead toward an honest final result rather than
uncontrolled looping or fabricated evidence.

`Title`: S5–S8 design decisions — two reserves, and where the sizing rule lives

`User prompt`: [Decisions taken on the S5–S8 plan] Scope is S5–S8 plus a single-attempt finalise();
grounding (S9) and the retry ladder (S10) stay the next subtask. A research hop takes up to three
model turns, so the model can open the pages search actually returned rather than guessing a URL.
max_topics_for() moves from main.py into agents/prompt_context.py, where finalise.md's {max_topics}
placeholder belongs, so one definition serves both the --no-research path and the loop.

`Title`: Reliable final report generation — deterministic validation and a bounded retry ladder

`User prompt`: Make final report generation reliable when the LLM produces an invalid report. The
intended high-level flow is: generate report → validate deterministically → return if valid →
otherwise retry using validation feedback → fail clearly after the configured attempts are
exhausted. The retry mechanism should correct the report, not restart the research run. Across
retries keep stable: user task, interpreted/narrowed goal, gathered sources/evidence, research
findings, sufficiency result, tool failures/limitations, and the grounding evidence set. Only the
final report generation should be retried. Do not search or fetch additional evidence merely
because final report validation failed — a validation failure means the report does not satisfy
our deterministic contract, not that the research must be redone. Use the structured S9 validation
result. Reuse the existing configuration such as max_output_retries; clarify during planning
whether that value means total attempts or retries after the initial attempt, do not silently
change its semantics, keep one canonical interpretation and test it explicitly. Every retry is
another model call and must respect the S4 RunBudget — do not bypass the model-call budget because
the retry mechanism wants another attempt; if no model-call capacity remains, stop retrying and
fail according to the approved failure design, treating budget exhaustion as normal control flow
rather than an uncontrolled exception from RunBudget. After all allowed attempts are exhausted the
system must not return an invalid report as if it were valid, silently discard validation errors,
loop indefinitely, or fabricate corrected evidence. Design a clear terminal failure, likely using
an existing or new PreparationFailed domain error only if consistent with current conventions,
preserving enough information for debugging such as the final validation errors and relevant run
identity/context. Do not couple it prematurely to Day 4 tracing if tracing does not exist yet.

`Title`: Scope and design decisions for the report-validation work

`User prompt`: [Decisions taken on the plan] Cover S9 and S10 in one plan as two separately
approved phases. max_output_retries means total attempts (3 = one initial attempt plus two
retries); keep the existing setting name and document the mismatch in place. The last attempt
switches provider only when an alternate is genuinely configured; otherwise it repeats on the
primary with the accumulated errors, and any LLMError from the alternate is folded into
PreparationFailed rather than propagating.

`Title`: S10 design decisions — one ladder, a structured failure, and citation-free loop scripts

`User prompt`: [Decisions taken on the S10 plan] A reply that fails model_validate_json consumes a
ladder attempt and is re-asked with the Pydantic errors quoted, exactly as a rule failure is
re-asked with as_lines() — one ladder for "this report is not acceptable", whatever made it so,
rather than raising on the first drift. PreparationFailed keeps its message and also carries
run_id, attempts and issues, so service.py and the CLI can render the errors instead of parsing a
sentence. The loop suite's scripted report drops its citation, because those tests prove how the
loop stops and degrades rather than how citations are checked; grounding is proven separately
against a URL the run actually fetched.

`Title`: Wire the research agent into the CLI

`User prompt`: We have completed the Day 3 implementation subtasks, but running the command still
returns "Research mode is not built yet — it is the Day 3 loop...". Verify whether the research
agent is simply wired into the CLI or not. Make the existing CLI execute the research-agent flow
when research mode is requested. First inspect then propose the smallest change required. The
intended architecture should remain approximately: CLI → service/public agent entry point →
single-agent loop → validated FocusPreparationReport → CLI prints JSON. Verify that --no-research
or equivalent command still works. Normal research mode no longer prints the old "not built yet"
message. Command returns a validated FocusPreparationReport. Provider override still works.
--fully-local still enforces local-only provider selection. Attachment input is passed into the
research flow correctly when provided. Research failures produce the project's intended graceful
behaviour rather than falling back to the old placeholder. If the old placeholder/error branch is
now obsolete, remove or replace it cleanly. Do not leave dead code that still claims research mode
is unimplemented. When entering the command show some progress and do not halt the screen while
doing background processing.

`Title`: Offline integration tests for the Day 3 research loop

`User prompt`: Research loop offline using the existing FakeProvider, fixture search backend,
service entry point, tool registry, schemas, and current agent contracts. Cover these required
cases: a full offline run produces a valid FocusPreparationReport in under 2 seconds with no
internet or real model calls; an insufficient sufficiency verdict generates exactly one genuine
second hop based on information from hop 1; the configured hop cap cannot be exceeded;
SEARCH_UNAVAILABLE degrades gracefully and still reaches finalisation with the limitation
reflected honestly; invalid final output retries correctly and three consecutive invalid outputs
raise PreparationFailed; and a report citing a URL outside the run's allowed evidence set is
rejected by grounding validation. Keep the tests deterministic, fast, network-free, and focused on
end-to-end Day 3 behavior rather than re-testing every unit-level detail. Before implementation,
provide a short plan describing the scenarios, fixtures/scripts required, and how the full loop
will be exercised, then wait for approval.

`Title`: Day 3 S14 — live end-to-end verification with real Ollama and SerpAPI

`User prompt`: Implement Day 3 Subtask S14 as the final live end-to-end verification using the
real Ollama provider, SerpAPI, and existing Day 2 tools through the completed CLI/service flow.
During the same session, record successful live SerpAPI responses into fixtures/search/ using the
project's existing fixture format so future tests remain offline and reproducible, without
manually fabricating fixture data. Also close any outstanding Day 1/2 live verification gaps that
are required for this end-to-end run. Do not change core agent architecture merely to make the
demo pass; if a live failure reveals a real bug, diagnose it, make only the smallest necessary
fix, rerun the affected checks, and report it clearly. After verification, run the relevant
tests/full suite, update docs/research-agent-context.md with S14 results, success rate, verified
multi-hop behavior, recorded fixtures, and any remaining limitations, then mark Day 3 complete
only if all Definition-of-Done conditions are satisfied.

`Title`: Contingency option (2) adopted — constrained research decision replaces free-form tool calling

`User prompt`: [Decision taken during S14, on measured evidence] Free-form tool calling produced
correct tool calls on qwen3:4b but cost 361 s per research turn against 46 s for a constrained
call, making a full run ~38 minutes against a 900 s deadline. Spend contingency option 2: swap
free-form tool calling for a structured {action, arguments} decision under a constrained schema
inside run_research_step.

`Title`: Verify the Day 3 research agent end to end on the real local Ollama flow

`User prompt`: Verify the Day 3 research agent end to end using the real local Ollama flow. Run a
representative task such as Learn PostgreSQL indexing and confirm planning completes, tool schemas
are understood, web_search and fetch_url execute without BAD_ARGUMENTS, evidence is gathered,
sufficiency and multi-hop behavior work correctly, budgets/hop limits are respected, finalisation
and grounding validation succeed, and the CLI returns a valid FocusPreparationReport. Also run one
simple task that should stop after one hop. Do not change code initially; if something fails,
identify the exact stage and root cause, then propose the smallest fix before implementing it.

`Title`: Stop the S14 acceptance runs after the third run

`User prompt`: this is the last run do not run 4 to 5 to check validity. stop after 3rd run

`Title`: Build the Day 4 tracing foundation

`User prompt`: Implement Tracing Foundation for the Evergrove Research Agent. Build only the core
tracing infrastructure that later hooks, budget enforcement, memory, and multi-agent tracing will
depend on. Introduce or extend RunContext so every research run has a unique run_id, maintains an
active span stack, generates a unique span_id for each traced operation, and derives parent_span_id
from the currently active span so nested operations automatically form a trace tree. Preserve the
existing budget state and counters, but do not move or redesign budget enforcement in this task.
Add SQLite-backed persistence for runs and spans. Span records should support the existing
architecture requirements, including span_id, run_id, parent_span_id, name, kind, started_at,
ended_at, duration_ms, ok, error_code, from_cache, and concise input/output summaries. Keep tracing
persistence separate from agent/business logic and expose a small, clear API for starting and
finishing runs and spans so the registry hooks can use it later without redesigning the tracing
layer. Do not implement registry pre/post hooks, centralized budget enforcement, persistent
preparation memory, memory-aware prompting, trace rendering, or multi-agent tracing in this task.
Reuse current abstractions where possible, keep tracing deterministic and independent of the LLM,
and do not introduce new frameworks or infrastructure. Add tests only for the core tracing behavior
introduced by this task.

`Title`: Wire registry hooks and centralize budget enforcement

`User prompt`: Implement Registry Hooks + Budget Enforcement for the Evergrove Research Agent.
Build on the existing tracing foundation and wire tracing plus budget checks into the tool
registry so every tool call follows one consistent execution path. Use the registry's existing
pre-hook and post-hook mechanism rather than adding tracing or budget logic inside individual
tools. The pre-hook should run before tool execution, start or prepare the tool span using the
current RunContext, and enforce the applicable run budget before the underlying tool is invoked.
If the next call would exceed the allowed budget, the registry must return a normal ToolResult
with BUDGET_EXCEEDED and must not execute the actual tool. The post-hook should complete the
corresponding span and persist the operation outcome, including timing, success/failure state,
error information, and cache status. Make sure span parenting continues to use the tracing
foundation so tool spans appear under the currently active agent/operation span. The goal is to
make the registry the single choke point for this behavior so no tool has to implement its own
tracing or budget checks, moving budget enforcement into the pre-hook instead of keeping
scattered checks. Preserve the current ToolResult contract and the existing behavior that tool
failures are returned as values rather than raised as normal control-flow exceptions. Avoid
redesigning the tracing model, budget model, tool registry, or agent loop unless a minimal
compatibility change is required. Do not implement persistent memory, memory-aware prompting,
trace rendering, or multi-agent tracing in this task. Keep the implementation deterministic,
centralized, and reusable by all current and future registry-managed tools. Add tests only for
the core functionality introduced here.

`Title`: Build persistent and session memory

`User prompt`: Implement Persistent & Session Memory for the Evergrove Research Agent. Build the
memory layer that allows the system to retain useful information during a research run and recall
validated preparation from previous runs. Implement persistent preparation memory so that after a
preparation has been successfully validated, a compact summary can be stored and retrieved later.
The stored memory should use a normalized `task_key` so semantically equivalent continuation
requests can match prior work; for example, the architecture expects `"Learn PostgreSQL indexing"`
to normalize in a way that can match `"postgresql indexing"`. Store only the information needed to
support future preparation, such as the previous interpreted goal, topics already covered, topics
deferred, relevant summary information, associated run ID, and creation time. Do not save invalid
or failed preparations to persistent memory. Implement `recall_previous_preparation` with the
planned default 30-day recall window and return a clear "not found" result when there is no recent
matching preparation. Memory lookup failures must degrade safely: a database or memory-layer error
must not fail the overall research run. Also implement session/run memory for information that
must survive across hops inside the same research run. Reuse the existing run state where
appropriate instead of creating a second competing state model. The memory should support
retaining the current narrowed goal, findings gathered so far, appraisal/sufficiency information,
decisions, previously seen queries, and previously seen URLs so later hops can reuse earlier work
rather than forgetting or repeating it. Keep session memory logically separate from persistent
cross-run preparation memory even if both use the same SQLite database. The goal of this task is
to provide reliable storage and retrieval only; do not yet change the agent's prompts or
decision-making so that it actively avoids previously covered topics or prefers deferred topics —
that behavior belongs to the next task. Integrate memory through the existing architecture rather
than bypassing it. Add the required memory tool or service functions in a way that can later be
called through the registry and traced using the infrastructure already implemented. Preserve the
existing tool and tracing contracts, avoid unnecessary changes to the research loop, and do not
redesign the database or agent architecture unless a minimal compatibility change is required. Do
not implement memory-aware prompting, the trace renderer, or multi-agent behavior in this task.
Add tests only for the core memory functionality introduced here.

`Title`: Memory-aware agent integration

`User prompt`: Implement Memory-Aware Agent Integration for the Evergrove Research Agent. Build on
the existing persistent and session memory layer so recalled preparation actually influences the
agent's task interpretation, research decisions, and final preparation. When a relevant previous
preparation is recalled, provide the agent with the useful memory needed for continuation,
especially the previous interpreted goal, `topics_covered`, and `topics_deferred`. Update the
task-understanding or planning behavior so the agent is instructed not to repeat topics that were
already covered, to prefer useful deferred topics where appropriate, and to explicitly acknowledge
the continuation in the new `interpreted_goal`. The memory should guide the agent rather than
rigidly dictate its output: if a deferred topic no longer fits the current task or session length,
the agent may choose another appropriate next topic, but it should not silently restart from
previously completed material. When no previous memory is found, preserve the existing behavior
exactly so a fresh task is handled normally. Memory should remain an optional enhancement; a recall
failure or missing memory must not prevent the research run from continuing. Reuse the existing
memory retrieval mechanism rather than adding another memory lookup path, and keep the integration
focused on supplying relevant memory to the existing agent decisions. Do not redesign the research
loop, persistent memory schema, tracing system, budget system, or tool registry unless a minimal
compatibility change is required. Ensure session memory also continues to influence later hops
within the same run so previously gathered findings, seen queries, and seen URLs are reused rather
than unnecessarily repeated. Do not create a second parallel state mechanism if the current run
state already provides this information. Keep cross-run memory and within-run memory conceptually
distinct: persistent memory helps continue previous preparation, while session memory prevents the
current run from forgetting its own earlier hops. Do not implement the trace renderer or
multi-agent Supervisor/Researcher/Appraiser behavior in this task. Add tests only for the core
behavior introduced by this task.

`Title`: Trace renderer and structured JSON trace logging

`User prompt`: Implement **Trace Renderer** for the Evergrove Research Agent. The purpose of this
task is only to make the tracing information already stored by the system easy to inspect.
Implement `scripts/show_trace.py <run_id>` using the existing tracing storage rather than creating
another trace representation. It should load the requested run and its spans, reconstruct their
hierarchy using `parent_span_id`, and print a clear human-readable trace tree. The output should
make it easy to see operation names, nesting, timestamps or durations, success/failure state, cache
hits, and error codes when available. The renderer must remain read-only and separate from tracing
persistence, hooks, memory, and agent logic. It should gracefully handle failed spans, incomplete
spans, missing run IDs, and traces containing different levels of nesting without crashing. Do not
redesign the tracing schema just to simplify rendering, and do not introduce new tracing, memory,
budgeting, or agent behavior in this task. Do not perform model-quality evaluation,
research-quality verification, continuation-quality validation, multi-hop correctness validation,
or full Day 4 acceptance testing here. Those checks should remain for the final testing and
evaluation day. Add tests only for the core renderer behavior where necessary.

`Title`: Structured JSON trace logging belongs to Day 4, not the final validation day

`User prompt`: Do not defer structured JSON trace logging to the final validation day. It is
deterministic Day 4 tracing functionality, not model-quality validation. Include the required
structured JSON log line per operation as part of this task, while still deferring all live model
behavior, research-quality, continuation-quality, multi-hop, and end-to-end acceptance validation
to the final day. Keep this implementation minimal and reuse the existing tracing/hook data rather
than introducing a new tracing path.

`Title`: All live-run testing consolidated into Day 7; implementation first

`User prompt`: Keep all the testing for day 7 such as live run. First we complete the
implementation, later we will focus on testing.

---

`Title`: Day 5 Task 1 — refactor the single-agent loop into Supervisor, Researcher and Appraiser

`User prompt`: Implement Day 5 Task 1 by refactoring the existing working single-agent research
loop into a multi-agent structure consisting of a Supervisor, Researcher, and Appraiser, while
preserving the current behavior and reusing the implementation that already works. This is a
structural refactor, not a rewrite and not an opportunity to redesign unrelated parts of the
system. The existing decision logic belongs to the Supervisor, the research-step logic belongs to
the Researcher, the sufficiency/judgement logic belongs to the Appraiser, and final report
generation belongs to the Supervisor. Create appropriate modules under `agents/` for these three
roles, but decide the exact class/function structure, imports, abstractions, and internal
organization based on the existing codebase rather than forcing a new architecture unnecessarily.
The Supervisor must own the overall orchestration and run state, decide what happens next,
coordinate worker outputs, and produce the final report; the Researcher must perform research
using the existing Day 2 tools and return research findings without deciding that the overall run
is complete or generating the final report; and the Appraiser must evaluate the gathered evidence
and determine its sufficiency without performing research tool calls or producing the final
report. Researcher and Appraiser must not communicate directly; coordination should remain
controlled by the Supervisor. Do not reimplement web search, URL fetching, document reading,
memory, validation, tracing, budget enforcement, retries, grounding, or any other existing
capability inside the new agents; all tool execution must continue through the existing shared
registry so that the hooks, tracing, budgets, and other Day 4 behavior remain intact. Retain
`agents/single_agent.py` and keep the existing single-agent multi-hop flow runnable because it
remains a required demonstration; the application should support both single-agent and
multi-agent reasoning through the existing service entry point, with `--mode single` selecting the
existing single-agent path and `--mode multi` selecting the Supervisor/Researcher/Appraiser path,
with multi intended to be the default. Both modes should share the same tools, registry, memory,
tracing, validation, persistence, schemas where applicable, and surrounding infrastructure,
differing only in their reasoning topology. Avoid unnecessary schema redesign in this task because
detailed typed inter-agent message work belongs to the next Day 5 task; make only minimal schema
changes if they are genuinely required to complete this refactor safely. Do not introduce
LangGraph, LangChain, MCP work, new providers, a new tracing architecture, changes to
`FocusPreparationReport`, or unrelated cleanup. Preserve all existing Day 3 and Day 4 behavior and
tests, and add or adjust only the tests necessary to prove that the refactor did not break the
existing single-agent path and that the new multi-agent path is correctly wired through the same
infrastructure. In particular, verify that both modes can produce a schema-valid report with the
existing offline/FakeProvider setup, that agents do not bypass the registry to execute tools
directly, and that the existing test suite remains green.

---

`Title`: Keep both loops rather than sharing one parameterized loop body

`User prompt`: [Decision taken during planning, in answer to how the two modes should relate.]
Keep both loops, share helpers: `run_agent` stays verbatim as the Day 3 demonstration;
`supervisor.py` gets its own coordinator that delegates to researcher/appraiser. Shared per-hop
mechanics are extracted so only the ~25-line control skeleton appears twice. Zero regression risk
to single mode. The `--mode` default lives in the CLI flag and the service parameter only — no
`AGENT_MODE` setting in `config.Settings` or `.env.example`.

---

`Title`: Day 5 Task 2 — formalize typed inter-agent message contracts

`User prompt`: Implement Day 5 Task 2 by formalizing the communication contracts between the
Supervisor, Researcher, and Appraiser using typed Pydantic models in `schemas/agents.py`. The
required inter-agent message concepts are `SupervisorDecision`, `ResearchAssignment`,
`ResearchFindings`, `AppraisalRequest`, and `AppraisalVerdict`. Their purpose is to make every
transition between agents explicit, validated, serializable, and traceable rather than relying on
free-form dictionaries, loosely structured JSON, or agent-specific ad hoc payloads. Preserve the
architectural rule that the Researcher and Appraiser never communicate directly: the Supervisor
owns orchestration and must be the component that converts its decision into a
`ResearchAssignment`, receives `ResearchFindings`, constructs the `AppraisalRequest`, receives the
`AppraisalVerdict`, and then decides whether to continue or finalize. Reuse existing models or
fields wherever they already express the required information correctly instead of creating
duplicate representations. Do not redesign `FocusPreparationReport`, tool I/O schemas, memory
schemas, tracing schemas, or other unrelated contracts. Add sensible Pydantic validation only
where it protects a real contract; do not add arbitrary restrictions. Update the three agent
modules and Supervisor orchestration so these models are actually used at agent boundaries rather
than merely defined and left unused. LLM structured-output parsing should validate against the
appropriate message model, and invalid output should continue through the existing retry/error
handling. Keep all existing tool execution, tracing, memory, budget enforcement, report
validation, and single-agent behavior intact. The single-agent path may continue using its
existing internal flow. Add focused tests proving that the message schemas accept valid
representative payloads, reject materially malformed payloads, and are actually passed between
Supervisor, Researcher, and Appraiser in the multi-agent flow; verify that no direct
Researcher-to-Appraiser call path is introduced and that the existing suite remains green.

---

`Title`: Wire the Appraiser's semantic judgement into its consumers, not just the schema

`User prompt`: [Decision taken during planning, in answer to whether T2 should add
`AppraisalVerdict.accepted[]` / `rejected[]` / `disagreements[]`, given that the five message
models already existed and were already used at every agent boundary.] Add all three, fully
wired — the fields go on `AppraisalVerdict` **and** into their consumers (`runtime._appraisal_line`
for `run_memory`, `prompt_context.render_research_context` for `finalise`), so they are not dead
fields. Accepted cost: a wider constrained-decoding target for the 4B local model.


---

`Title`: Day 5 Task 3 — independent LLM provider selection per agent role

`User prompt`: Implement Day 5 Task 3 by wiring independent LLM provider selection for the
Supervisor, Researcher, and Appraiser while reusing the provider abstractions and
implementations that already exist in the project. Add or use configuration values named
`SUPERVISOR_PROVIDER`, `RESEARCHER_PROVIDER`, and `APPRAISER_PROVIDER`, with each role
independently supporting the existing `local` and `hosted` provider choices. This task is
provider wiring only: do not create new provider implementations, duplicate Ollama or
hosted-provider code, introduce a second local model, or redesign the existing LLM abstraction.
Resolve each configured role to an existing provider instance through the project's established
provider factory or equivalent shared construction path, and inject the resolved provider into
the appropriate agent so that the Supervisor, Researcher, and Appraiser do not read environment
settings or construct provider clients themselves. The architecture must support an entirely
local run as well as mixed configurations such as a local Supervisor and Researcher with a hosted
Appraiser; changing a role's provider should require configuration only and must not require
modifying agent logic. Preserve the current single-agent mode and determine from the existing
design how its provider should continue to be selected rather than unnecessarily forcing the
three new role settings into the single-agent path. Avoid introducing ambiguous configuration
precedence or additional provider-selection mechanisms beyond what the existing configuration
architecture requires. The primary architectural reason for independent role selection is that
the Appraiser can be configured to use a genuinely different provider from the Researcher for an
independent semantic judgement, while still allowing all-local execution on the target machine;
therefore ensure that a mixed-provider multi-agent run actually routes each role's model calls
through its configured provider rather than merely storing the settings. Keep prompts, message
schemas, tool execution, memory, tracing, budgets, report validation, and multi-hop logic
unchanged unless a minimal adaptation is required to pass the selected provider into an agent.
Add focused tests using fakes or stubs that prove each role receives and calls the provider
selected for that role, that different provider combinations can coexist within one multi-agent
run, that the all-local configuration remains supported, and that an invalid provider value fails
through the project's normal configuration validation rather than silently falling back. Do not
make real hosted API calls in the normal test suite.

---

`Title`: Day 5 Task 4 — make the Appraiser a genuine evidence judge

`User prompt`: Implement Day 5 Task 4 by strengthening the Appraiser so that it performs real
semantic judgement over the Researcher's gathered evidence rather than returning only a shallow
sufficiency decision. First inspect the current `AppraisalVerdict` schema, Appraiser prompt,
Appraiser implementation, source models, finalisation flow, and the changes already made in Task
2 for `accepted`, `rejected`, and `disagreements`. Preserve the existing architecture and extend
the current implementation rather than creating a parallel appraisal mechanism. The Appraiser
should evaluate each supplied source in the context of the research question and produce
structured judgement that includes which sources are accepted, what each accepted source actually
supports, what it does not establish, its authority classification using the existing authority
model or enum, which sources are rejected and the reason for rejection, what information is still
missing, any meaningful disagreement between sources, whether the available evidence is
sufficient, and a specific `requested_followup` when further research is needed. These fields
must represent evidence-based semantic judgement rather than deterministic schema validation; do
not move Pydantic validation, URL grounding, business-rule validation, or other responsibilities
owned by `validate_report` into the Appraiser. Likewise, the Appraiser must not search the web,
fetch URLs, call research tools, decide the final report itself, or communicate directly with the
Researcher. It receives an `AppraisalRequest` through the Supervisor and returns an
`AppraisalVerdict` to the Supervisor. Review the Appraiser prompt carefully so it tells the model
to judge only from the supplied source content, avoid inventing unsupported facts, distinguish
between what a source supports and what it merely does not cover, reject evidence for an explicit
reason rather than arbitrarily, and request follow-up research only when the missing information
materially affects the research goal. Do not add fake disagreements just to populate the field; an
empty list is valid when sources do not genuinely conflict. Similarly, `does_not_support` should
capture relevant gaps in a source rather than every topic the source fails to mention. Wire the
richer verdict into existing consumers where there is a real architectural need: accepted versus
rejected evidence should be available to finalisation so rejected sources cannot appear as trusted
final resources, missing information and requested follow-up should remain available for the
Supervisor's later stop/continue decision, and the structured appraisal should be traceable using
the existing tracing system. Do not invent new orchestration behavior in this task beyond what is
required to make the Appraiser's judgement usable. Add focused tests using `FakeProvider` or
scripted outputs that prove the Appraiser can accept an authoritative source with meaningful
`supports` and `does_not_support`, reject a low-authority or irrelevant source with a concrete
reason, preserve genuine disagreements when supplied, return empty disagreements when none exist,
identify missing information, and produce a specific follow-up question when the evidence is
insufficient. Also add or preserve an integration assertion that a source rejected by the
Appraiser does not appear in the final report's trusted resources. Verify that the Appraiser
performs no tool calls and that existing single-agent, multi-agent, tracing, validation, and
offline tests remain green.
