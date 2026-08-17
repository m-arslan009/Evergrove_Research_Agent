"""The typed messages the reasoning stages exchange (plan sections 8 and 23).

Day 3 runs one agent, but it runs it as four separately-prompted functions —
`decide_next_step`, `run_research_step`, `judge_sufficiency`, `finalise` — each taking
and returning a model from this file. That is what makes Day 5 a file move rather than a
rewrite: the Supervisor, Researcher and Appraiser already speak this vocabulary.

Two families live here, and the distinction matters to every caller:

**Model output** — `SupervisorDecision` and `AppraisalVerdict` are handed to
`LLMProvider.generate(schema=...)` for constrained decoding. They stay small and flat,
because a 4B local model's schema adherence degrades as the target grows, and every field
must survive `to_gemini_schema` for the hosted retry. `extra="forbid"` is what turns model
drift into a retry instead of a silently dropped field.

**Code assembled** — everything else is built by our own code from tool results. A model
never sees their schema, so they may be as rich as the loop needs.

Nothing here calls a model, a tool, or the network, and nothing here decides anything: the
hop cap, the budgets and the stop condition belong to the loop and to `RunContext`. These
are the shapes those decisions are expressed in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evergrove_agent.schemas.report import SourceAuthority
from evergrove_agent.schemas.task import TaskContext
from evergrove_agent.schemas.tools import SearchSourceType, ToolError

AgentAction = Literal["RESEARCH", "FINALISE"]
"""What the planner decided to do next. Two values on purpose: every other outcome —
retrying, giving up, degrading to an honest report — is the loop's business, not a
choice we ask a small model to express."""


# --- what a hop gathered ------------------------------------------------------------------


class GatheredSource(BaseModel):
    """One source this run touched, whether or not it was ever opened.

    Deliberately *not* `search.NormalizedSource`. That model describes a search hit;
    this one describes the agent's evidence, which may carry the text we actually read.
    They also cannot be the same type: `schemas/` imports nothing from the package, and
    `search/normalize.py` imports `schemas`. `run_research_step` (S6) builds these from a
    `NormalizedSource` plus, when the page was opened, a `FetchUrlOutput`.

    `retrieved_at is None` is the load-bearing distinction: it separates a URL a search
    *discovered* from one we *read*. Both may be cited, but an unread one may only be
    cited with `authority="unknown"` — we do not get to call a page authoritative when
    nobody opened it.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    """The canonical form, as `search.canonicalize_url` produces it — the same string the
    source cache is keyed on, so the grounding check compares like with like."""
    title: str = Field(default="", max_length=500)
    domain_class: SourceAuthority = "unknown"
    snippet: str = Field(default="", max_length=2000)
    """What the search result claimed the page says. Not evidence — it was not read."""
    excerpt: str = Field(default="", max_length=200_000)
    """The extracted text the model was shown. Bounded at assembly by
    `SOURCE_EXCERPT_CHARS`; the ceiling here is only `MAX_SOURCE_TEXT_CHARS`, so a caller
    that wants to keep more of a source is not blocked by this contract."""
    retrieved_at: datetime | None = None
    """When the page was fetched. `None` means discovered but never opened."""

    @property
    def was_read(self) -> bool:
        """Whether this source is evidence or only a lead.

        One definition, here, because the grounding rule (S9), the authority rule and
        `sources_examined` all ask the same question and must not answer it differently.
        """
        return self.retrieved_at is not None


class ToolFailure(BaseModel):
    """A tool that answered with a failure during a hop.

    Kept rather than discarded because it is why a hop came back thin, and the report has
    to be honest about that: a run whose search was unavailable must say so in `unknowns`
    rather than quietly producing a sourceless plan that looks like a confident one.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=64)
    error: ToolError


# --- stage 1: the planner ------------------------------------------------------------------


class SupervisorDecision(BaseModel):
    """MODEL OUTPUT. `decide_next_step()` → research something, or write the report.

    Becomes `supervisor.decide()`'s return value on Day 5, unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    action: AgentAction
    research_question: str | None = Field(
        default=None,
        max_length=300,
        description="Required when action is RESEARCH: the one question this hop should "
        "answer, narrow enough to fit the session.",
    )
    source_preference: SearchSourceType = Field(
        default="general",
        description="What kind of source this question wants. Passed straight to "
        "web_search as source_type.",
    )
    reasoning: str = Field(
        max_length=400,
        description="One line on why. Read by a human in the trace, never parsed.",
    )
    """No `min_length`: a terse model must not fail validation and burn a retry over
    brevity. The field is still required, so the trace always has a reason."""

    @model_validator(mode="after")
    def _research_needs_a_question(self) -> SupervisorDecision:
        """A hop without a question would reach `web_search` as a blank query — a wasted
        search call, and real SerpAPI quota on a metered backend. Rejecting it here turns
        that into a retry, which is the cheapest place to catch it."""
        if self.action == "RESEARCH" and not (self.research_question or "").strip():
            raise ValueError("action='RESEARCH' requires a non-empty research_question")
        return self


# --- stage 2: the research step -----------------------------------------------------------


class ResearchAction(BaseModel):
    """MODEL OUTPUT. One research turn: call one tool, or stop.

    **Day 3's contingency option (2), spent during S14.** The research step originally used
    free-form tool calling. It worked — `qwen3:4b` chose the right tool with sensible
    arguments — but unconstrained decoding let the model precede the call with ~4 000
    characters of reasoning, measured at **361 s per turn against 46 s for a constrained
    call on the same machine**. Constraining the turn to this schema makes that preamble
    physically impossible. `dispatch` already takes a `ToolCall`, so the loop constructs one
    from this and there is no second tool path.

    Deliberately *not* a `Literal` over the tool names: `advertised_tool_names` is both the
    advertisement and the allow-list, and a second copy of the menu here would drift from it
    the moment an attachment changes what is offered. An invented name stays what it always
    was — a `ToolResult(UNKNOWN)` from `dispatch`, before the registry is reached.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(
        default="",
        max_length=64,
        description="The tool to call, named exactly as in the available tools. Leave "
        "empty when this question has been researched enough.",
    )
    arguments: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="The tool's arguments, exactly as its schema names them.",
    )
    """Scalar values only, which is every argument the advertised tools actually take
    (`query`, `source_type`, `max_results`, `url`, `max_chars`, `excerpt_for`, `path`,
    `mode`, `section_hint`). Typing it beats `dict[str, Any]` twice: constrained decoding
    gets a value grammar instead of "any JSON", and the mapping still reaches
    `registry.call` raw, so `_parse_args` stays the single argument validator."""

    reasoning: str = Field(
        default="",
        max_length=400,
        description="One line on why this call. Read by a human in the trace, never parsed.",
    )
    """Bounded on purpose. The model will reason somewhere; a short sanctioned field costs
    ~100 tokens, while the unconstrained turn this replaced spent ~1 000 on the same thing.
    It becomes the hop's `notes`, which is what `response.text` used to supply."""


class ResearchAssignment(BaseModel):
    """Supervisor → Researcher. Code-assembled from a `SupervisorDecision` and the budget.

    Self-contained on purpose: on Day 5 the Researcher is a separate agent that receives
    this message and nothing else, so everything it needs to avoid repeating itself
    travels with it.
    """

    model_config = ConfigDict(extra="forbid")

    research_question: str = Field(min_length=1, max_length=300)
    session_minutes: int = Field(ge=5, le=180)
    """Mirrors `TaskContext.session_minutes`. The researcher stops looking when it has
    enough for *this* session, not for a course."""
    source_preference: SearchSourceType = "general"
    hop: int = Field(ge=1, le=3)
    """Which hop this assignment belongs to. 1-based, so it matches `hops_used`."""
    max_searches: int = Field(ge=0)
    max_fetches: int = Field(ge=0)
    """The *allowance* for this hop, not the ledger. The live counters are on
    `RunContext` (S4), in one place, so Day 4 can lift enforcement into registry hooks."""
    avoid_queries: list[str] = Field(default_factory=list, max_length=32)
    avoid_urls: list[str] = Field(default_factory=list, max_length=64)
    """What earlier hops already spent. This is what makes hop 2 a genuinely new query
    rather than a repeat of hop 1 — and an identical live query re-run is quota burned
    for a result the cache already held."""


class ResearchFindings(BaseModel):
    """Researcher → Supervisor. Everything one hop produced.

    Code-assembled from tool results; only `notes` comes from the model.
    """

    model_config = ConfigDict(extra="forbid")

    research_question: str = Field(min_length=1, max_length=300)
    hop: int = Field(ge=1, le=3)
    queries_used: list[str] = Field(default_factory=list, max_length=32)
    sources: list[GatheredSource] = Field(default_factory=list, max_length=64)
    """Every source this hop *discovered*, not only the ones opened — `retrieved_at`
    marks which is which. Keeping the leads is what lets a report cite a
    discovered-but-unread URL (as `authority="unknown"`) and what gives the grounding
    check its full evidence set."""
    failures: list[ToolFailure] = Field(default_factory=list, max_length=16)
    notes: str = Field(default="", max_length=2000)
    """The researcher's own summary of what it found. The one free-text field here, and
    it is never parsed — it is prompt material for the later stages."""


# --- stage 3: the sufficiency judgement ------------------------------------------------


class AppraisalRequest(BaseModel):
    """Supervisor → Appraiser. Code-assembled.

    Carries no budget and no hop count, deliberately: a judge that knows a follow-up is
    impossible stops asking for one, and that would hide the real verdict rather than
    change it. Whether a follow-up can be afforded is the loop's decision, taken after.
    """

    model_config = ConfigDict(extra="forbid")

    research_question: str = Field(min_length=1, max_length=300)
    session_minutes: int = Field(ge=5, le=180)
    sources: list[GatheredSource] = Field(default_factory=list, max_length=64)


class AppraisalVerdict(BaseModel):
    """MODEL OUTPUT. `judge_sufficiency()` → do these sources support a useful session?

    Four fields, and every one of them steers control flow or explains it. Day 5 adds the
    richer judgement fields (`accepted[]`, `rejected[]`, `disagreements[]`) as optional
    extras — an additive change, not a rewrite, which is why they are not here today: a
    fatter constrained-decoding target costs reliability on a 4B local model, and Day 3
    exists to find out whether that model can drive the loop at all.
    """

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    missing_information: list[str] = Field(default_factory=list, max_length=5)
    requested_followup: str | None = Field(
        default=None,
        max_length=200,
        description="The one question a further hop should answer. Leave null when "
        "nothing specific would help.",
    )
    """The multi-hop mechanism. Hop 2's query originates here, from content that did not
    exist before the hop ran — which is precisely what makes the second hop agentic
    rather than a scripted retry. Never drop this field (plan section 8.3)."""
    reasoning: str = Field(max_length=400)

    # There is deliberately no validator requiring a follow-up when `sufficient` is
    # false. "Not enough, and nothing specific would help" is a real, honest verdict, and
    # the loop's answer to it is to finalise with populated `unknowns`. Forcing a
    # follow-up would turn that into a retry loop and invite an invented question.


# --- what survives between runs -----------------------------------------------------------


class PreviousPreparation(BaseModel):
    """A validated preparation from an earlier run, compactly enough to reuse (plan 12.2).

    Code-assembled: built from a `FocusPreparationReport` that already passed
    `validate_report`, and never handed to a model as a schema. It lives here rather than
    beside the storage that writes it because it is a *message* — `memory/prep_memory.py`
    produces it, the recall tool returns it, and the memory-aware prompting task puts it in
    front of the planner. `schemas/` is the one layer all three can import.

    **A summary, not a second copy of the report.** Only the fields a later session needs to
    continue rather than restart: what the previous run aimed at, what it covered, what it
    deliberately deferred, and where it read. The full report is not stored — nothing needs
    it, and `FocusPreparationReport` is the most expensive schema in the project to change.
    """

    model_config = ConfigDict(extra="forbid")

    task_key: str = Field(min_length=1, max_length=500)
    """The normalised form the recall lookup matches on — `normalize_task_key`'s output.
    Carried on the record so a caller can see *why* a preparation was recalled."""
    original_task: str = Field(min_length=1, max_length=500)
    """The title as the user actually typed it. The key is for matching; this is for reading."""
    interpreted_goal: str = Field(default="", max_length=400)
    session_objective: str = Field(default="", max_length=300)
    """Both, because they answer different questions: the goal is the slice the previous run
    narrowed the task down to, the objective is what that session was to achieve."""
    topics_covered: list[str] = Field(default_factory=list, max_length=8)
    """`topics_to_cover` from the previous report — the material a continuation must not
    repeat. Bounded as the report bounds it."""
    topics_deferred: list[str] = Field(default_factory=list, max_length=10)
    """`topics_to_skip` — the neighbouring material the previous run left out on purpose,
    which is the most likely place a continuation should start."""
    source_urls: list[str] = Field(default_factory=list, max_length=5)
    """Where the previous session read. Not evidence for this run: a citation must still be
    grounded in what *this* run gathered (S9), so these are context only."""
    run_id: str = Field(min_length=1, max_length=64)
    created_at: datetime
    """When the preparation was saved, timezone-aware UTC. What the recall window compares
    against."""


# --- the run's working state -------------------------------------------------------------


class RunState(BaseModel):
    """What one run has decided, gathered and been told so far (plan section 15).

    Not a message — the loop's own state, mirrored to Day 4's `run_memory` table for the
    trace. It lives here rather than beside the loop because three later subtasks read it
    and must agree on what it means: S6 dedupes against it, S9 grounds cited URLs against
    it, and S3 renders it into prompts.

    The derived properties are pure projections of its own fields. They exist so that
    "what has this run seen" has exactly one definition instead of three slightly
    different ones.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    """`validate_assignment` because the loop mutates this object: without it
    `state.hop = 99` would pass silently, and the hop cap is the one bound that stops a
    model driving the run forever."""

    task: TaskContext
    previous: PreviousPreparation | None = None
    """What an earlier run prepared for this same task, when one was recalled (T5).

    Additive and defaulted, so no construction site moved — the shape T1's `span_stack`
    addition took. It sits here rather than being threaded through two stage signatures
    because both stages that read it already receive this object, and because "what this run
    knows" has one home: `run_agent` recalls once, before the loop, and every later stage
    reads the same answer.

    **`None` is the ordinary case and must stay indistinguishable from today.** A first run,
    a task nobody has prepared, an aged-out row and a memory outage all arrive here as `None`,
    and the renderers answer all four with an empty block.
    """
    hop: int = Field(default=0, ge=0, le=3)
    """Hops completed. 0 before any research; the ceiling matches
    `FocusPreparationReport.hops_used`. `MAX_HOPS` is config and is enforced by the loop —
    this is only the shape's own limit."""
    findings: list[ResearchFindings] = Field(default_factory=list, max_length=3)
    verdict: AppraisalVerdict | None = None
    """The most recent judgement. `None` until the first hop has been appraised."""
    validation_errors: list[str] = Field(default_factory=list, max_length=20)
    """What `validate_report` rejected on the previous attempt, quoted back into the
    retry prompt by S10. Empty on a first attempt."""

    @property
    def all_sources(self) -> tuple[GatheredSource, ...]:
        """Every source from every hop, in the order they were gathered."""
        return tuple(
            source for finding in self.findings for source in finding.sources
        )

    @property
    def evidence_urls(self) -> frozenset[str]:
        """Every URL this run discovered or read — the set a citation must belong to.

        The strongest anti-hallucination guard in the system (plan section 17) is set
        membership against this, not a prompt instruction, because a model cannot talk
        its way past a set.
        """
        return frozenset(source.url for source in self.all_sources)

    @property
    def fetched_urls(self) -> frozenset[str]:
        """The URLs actually opened. A citation outside this set may only claim
        `authority="unknown"`."""
        return frozenset(
            source.url for source in self.all_sources if source.was_read
        )

    @property
    def used_queries(self) -> tuple[str, ...]:
        """Every query already spent, in order — what the next assignment must avoid."""
        return tuple(
            query for finding in self.findings for query in finding.queries_used
        )
