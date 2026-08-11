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
