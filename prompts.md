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
