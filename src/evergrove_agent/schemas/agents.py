"""The typed messages the reasoning stages exchange (plan sections 8 and 23).

Day 3 runs one agent, but it runs it as four separately-prompted functions —
`decide_next_step`, `run_research_step`, `judge_sufficiency`, `finalise` — each taking
and returning a model from this file. That is what makes Day 5 a file move rather than a
rewrite: the Supervisor, Researcher and Appraiser already speak this vocabulary.

Two families live here, and the distinction matters to every caller:

**Model output** — `SupervisorDecision` and `AppraisalVerdict` are handed to
`LLMProvider.generate(schema=...)` for constrained decoding. They stay small, because a 4B
local model's schema adherence degrades as the target grows, and every field must survive
`to_gemini_schema` for the hosted retry. `extra="forbid"` is what turns model drift into a
retry instead of a silently dropped field.

`AppraisalVerdict`'s two per-source lists are the one place that shape nests (Day 5 T4), and
they nest exactly one level — the depth `FocusPreparationReport.resources` has driven
through both providers since Day 1, so `$defs` inlining in `to_gemini_schema` and Ollama's
`format=` are both already proven at it. Every added field is defaulted and a bare string
still coerces, so widening the target could not narrow what a small model may answer with.

**Code assembled** — everything else is built by our own code from tool results. A model
never sees their schema, so they may be as rich as the loop needs.

Nothing here calls a model, a tool, or the network, and nothing here decides anything: the
hop cap, the budgets and the stop condition belong to the loop and to `RunContext`. These
are the shapes those decisions are expressed in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evergrove_agent.schemas.report import SourceAuthority
from evergrove_agent.schemas.task import TaskContext
from evergrove_agent.schemas.tools import SearchSourceType, ToolError

ContextCheck = Literal["ENOUGH", "MISSING"]
"""Whether the task says enough to be researched at all.

A separate answer from `AgentAction` on purpose. "What do I do next" and "do I have enough
to do anything" are two questions, and collapsing them into a three-valued action would put
them in one token — which is exactly the decode order that made a small model commit to
`RESEARCH` before it had considered whether the task supported one."""

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

    **The field order is behaviour rather than style: think, then admit, then act.**
    Declaration order becomes property order in `model_json_schema()`, which becomes decode
    order under both Ollama's `format=` and Gemini's `responseSchema` — the model emits these
    keys in the order they appear, and each one is committed before the next is written. So
    the order is the reasoning procedure:

    1. `reasoning` — work out what the task is asking, with nothing yet committed;
    2. `missing_context` — name anything only the user could supply;
    3. `action`, `research_question`, `source_preference` — decide, now that both are known.

    Both earlier orders were measured against `qwen3:4b` and both failed. With `action`
    first, the model committed to `RESEARCH` before reaching the field where it could say
    the task was underspecified, then wrote a question that supplied the missing detail
    itself. Moving `missing_context` to the front only inverted the problem: it answered the
    hardest question as its very first token, defaulted to `[]`, and explained afterwards
    that the task was "too vague to start planning". Do not reorder these fields.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(
        max_length=1600,
        description="Think here first, before filling anything below, and reach a "
        "conclusion. In at most three short sentences: what is this task asking, is any "
        "part of it something only this user could tell you rather than something a page "
        "could, and what will you therefore do? Do not spend this space restating the "
        "earlier session.",
    )
    """Declared first, and it is the model's working space rather than a comment on a
    decision already made.

    No `min_length`: a terse model must not fail validation and burn a retry over brevity.
    The field is still required, so the trace always has a reason.

    **It moved to the front because a constrained decode has no scratch pad.** With this
    field last, `qwen3:4b` emitted `missing_context: []` as the very first token of its
    answer — before it had written a word of analysis — and only afterwards explained, in
    `reasoning`, that the task was "too vague to start planning" and that "any plan would be
    speculative". The model was not wrong about the task; it was answering the hardest
    question first and the easiest question last. Deliberating here, then answering, is what
    makes the rest of the object follow from the analysis instead of preceding it.

    **The bound went 400 → 800 → 1600 for the same reason.** Constrained decoding enforces
    `maxLength` by stopping, so the limit is a guillotine rather than a hint: on a
    continuation task the model spent 400 characters restating the recalled preparation and
    was cut off mid-sentence at *"the task description says 'Continue my previous research
    for my application' but does not re"* — exactly one clause short of its own conclusion,
    which then arrived as an empty `missing_context`. A deliberation field has to be long
    enough to finish deliberating; the description now also tells it not to spend the space
    summarising what it was already shown. 800 was still not enough — the same model was cut
    off mid-analysis twice more at that bound, both times on a continuation, because a
    recalled preparation gives it more to restate. The field is bounded to stop a runaway
    generation, not to ration thought, and 1600 is roughly twice what this model writes
    unprompted.
    """

    context_check: ContextCheck = Field(
        default="ENOUGH",
        description="ENOUGH when the task names a subject you can go and read about. "
        "MISSING when any part of it could only be answered by this user — what they "
        "built, what they run it on, what they already have, what they want. Answer this "
        "before choosing an action.",
    )
    """The checkpoint between thinking and deciding, and the reason it is a `Literal`.

    `missing_context` alone was not enough. Given room to think, `qwen3:4b` reached the
    right conclusion in prose — *"since the task does not mention the user's application
    details, the case is missing context"* — and then emitted `missing_context: []` in the
    very next field. Naming a gap in a list is a generative act a model can simply decline
    to perform; choosing between two tokens is not. `AppraisalVerdict` already carries this
    shape for the same reason: `sufficient` decides, `accepted`/`rejected` explain.

    Defaulted to `ENOUGH`, so a reply that omits it is the ordinary researched run and every
    pre-existing scripted decision still validates. The loop stops on **either** signal
    (`supervisor._stop_for_missing_context`), because a model that says `MISSING` and lists
    nothing has still said the thing that matters.
    """

    missing_context: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Details about the user's own situation that the task never stated and "
        "that no page could tell you — what they built, what they run it on, what they "
        "already have. One short phrase each, naming the missing detail rather than asking "
        "a question. Fill this instead of guessing a value for it. Leave empty when the "
        "task names a subject that can be researched exactly as written.",
    )
    """The planner's way of saying "I cannot narrow this without inventing something".

    **Additive and defaulted**, the same shape `AppraisalVerdict.accepted` took in T2: this
    is a constrained-decoding target, and a reply that omits the field entirely — which a
    4B model routinely produces — must still validate. A lost default would fail
    `model_validate_json`, spend `_decode`'s one re-ask and return `None`, which the loop
    reads as `planner_unavailable` and stops the run on.

    It exists because the two-value `AgentAction` had no way to express the third real
    outcome. `plan.md` asks for a question "small enough to matter inside {session_minutes}
    minutes", and narrowing a task whose setting was never stated is only possible by
    supplying that setting — so the prompt's own quality bar manufactured the invention this
    field lets the model decline. **Non-empty means the run stops before a worker is given
    the assignment** (`supervisor._stop_for_missing_context`); the items reach the user
    through `unknowns`, which is the mechanism that already exists for what a run could not
    establish. It is deliberately not a fourth `AgentAction`: widening a `Literal` a small
    model decodes into is a far bigger change to that model's behaviour than a list it may
    leave empty.
    """

    @field_validator("missing_context", mode="after")
    @classmethod
    def _tidy_missing_context(cls, items: list[str]) -> list[str]:
        """Strip, drop blanks, bound each item, and dedupe — never reject.

        A model asked for "short phrases" will sometimes answer with an empty string, a
        sentence, or the same gap twice. None of those is worth a retry: the loop only needs
        to know *whether* anything is missing and what to tell the user, and a validation
        error here costs a model call to re-ask a question the reply already answered.
        """
        seen: list[str] = []
        for item in items:
            cleaned = " ".join(item.split())[:160]
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen[:4]

    action: AgentAction
    research_question: str | None = Field(
        default=None,
        max_length=300,
        description="Required when action is RESEARCH: the one question this hop should "
        "answer, narrow enough to fit the session. It must be answerable by reading a "
        "page. Never ask here for something only the user could tell you — that belongs "
        "in missing_context.",
    )
    source_preference: SearchSourceType = Field(
        default="general",
        description="What kind of source this question wants. Passed straight to "
        "web_search as source_type.",
    )
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


class AcceptedSource(BaseModel):
    """MODEL OUTPUT (nested). One source the Appraiser judged to genuinely help (Day 5 T4).

    T2 made `accepted` a list of names; that answered *which* sources helped and nothing
    about *how*. T4 keeps the same list and gives each entry the three facts a reader — the
    finalise stage, or a human reading the trace back — actually needs: what the source
    establishes, what it leaves open, and how far up the authority ladder it sits.

    **`supports` and `does_not_support` are not opposites.** The first is a claim about the
    text; the second is a *relevant* gap in it, not an inventory of every topic the page
    never mentions. `sufficiency.md` says so in words, because no schema can express it.

    A bare string coerces to `{"source": …}` — see `_a_bare_name_is_still_a_source`.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        max_length=200,
        description="The source, named by its title or URL exactly as it is written in "
        "the evidence above.",
    )
    supports: str = Field(
        default="",
        max_length=160,
        description="What this source actually establishes about the question, in one "
        "short phrase. Only what its own text says.",
    )
    does_not_support: str = Field(
        default="",
        max_length=160,
        description="What this source leaves open that the question needs. Leave empty "
        "when it has no relevant gap.",
    )
    authority: SourceAuthority = Field(
        default="unknown",
        description="Where this source sits on the authority ladder. Use unknown for "
        "anything that was found but never opened.",
    )
    """Reuses `report.SourceAuthority` rather than a second vocabulary. The judgement and
    the citation then classify a page the same way, so `finalise.md`'s authority rule and
    S9's overclaim check are arguing about the same word — and `unknown` keeps the meaning
    it has everywhere else: nobody opened it, so nobody gets to call it authoritative."""

    @model_validator(mode="before")
    @classmethod
    def _a_bare_name_is_still_a_source(cls, value: object) -> object:
        """A plain string becomes an entry with only its name filled in.

        Leniency bought deliberately. The alternative is that a 4B model answering T2's
        shape — a list of names, which is exactly what `sufficiency.md` asked for until this
        task — fails `model_validate_json`, spends `_decode`'s one re-ask, and returns
        `None`; and `None` is read by `_stop_after_hop` as "the appraiser could not answer",
        which ends the run. Losing the detail costs a thinner prompt. Losing the whole
        verdict costs the hop that produced it.

        **The coercion carries more weight since T5**, because the length of this list is now
        part of the stop condition: a bare name still counts towards the two accepted sources
        a `sufficient` verdict needs, so a model that names its sources without describing
        them is not penalised for the shape of its answer.
        """
        return {"source": value} if isinstance(value, str) else value


class RejectedSource(BaseModel):
    """MODEL OUTPUT (nested). One source the Appraiser judged not to help, and why (T4).

    The reason is the entire content of a rejection. "This one does not help" is a verdict
    nobody downstream can act on or argue with; "a personal blog with no version stated" is
    a fact the report can put in `unknowns` and a reader can check against the page.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        max_length=200,
        description="The source, named by its title or URL exactly as it is written in "
        "the evidence above.",
    )
    reason: str = Field(
        default="",
        max_length=200,
        description="Why this source does not help: what is wrong with it, or what it is "
        "about instead. Never leave this to be guessed.",
    )
    """No `min_length`, for `SupervisorDecision.reasoning`'s reason: a terse model must not
    fail validation and burn a retry over brevity. The rule that a rejection needs a stated
    reason is enforced where it belongs — in the prompt, and in what a reader sees."""

    @model_validator(mode="before")
    @classmethod
    def _a_bare_name_is_still_a_source(cls, value: object) -> object:
        """As `AcceptedSource`: a plain string is a rejection with its reason missing."""
        return {"source": value} if isinstance(value, str) else value


class AppraisalVerdict(BaseModel):
    """MODEL OUTPUT. `judge_sufficiency()` → do these sources support a useful session?

    The first four fields steer control flow or explain it; the last three are the
    Appraiser's *semantic* judgement of the evidence, added by Day 5 T2 and given their
    per-source detail by T4.

    **T2's three fields are additive, not a rewrite.** Every one defaults to an empty list,
    so a reply written against the Day 3 shape still validates and the loop behaves exactly
    as it did — which is what lets a 4B model that ignores them cost nothing. That default
    matters more here than anywhere else in the file: this is a constrained-decoding target,
    and the wider it gets the worse a small model's adherence to it becomes. **T4 widened
    two of them and kept that property intact**: the entries are objects now, but a reply
    that lists bare names still validates (see `AcceptedSource`), and a reply that omits the
    lists entirely still validates exactly as it did on Day 3.

    **Three fields steer the run, and `accepted` became one of them (T5).** `_stop_after_hop`
    reads `sufficient`, `requested_followup` and `len(accepted)`: the plan's stop condition is
    `sufficient` **with at least two accepted sources**, so a "yes" backed by one source or
    none finalises honestly instead of counting as success. That supersedes T2's narrower
    "they inform, they never decide" — deliberately, because the alternative is a run that
    reports a confident session plan built on a single page the judge itself only half
    endorsed. `rejected` and `disagreements` still only inform.

    **The defaults are what keep that safe.** A reply that omits `accepted` still validates and
    still produces a report; it produces a *cut-short* one, whose `unknowns` say why. Nothing
    about this makes a small model's reply invalid — it only stops an unsupported "yes" from
    reading like a supported one. Whether a follow-up is affordable remains the Supervisor's
    call.
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

    # --- the semantic judgement (Day 5 T2) ------------------------------------------------

    accepted: list[AcceptedSource] = Field(
        default_factory=list,
        max_length=8,
        description="The sources above that genuinely help answer the question, one entry "
        "each. Empty when none do.",
    )
    rejected: list[RejectedSource] = Field(
        default_factory=list,
        max_length=8,
        description="The sources above that do not help, one entry each with its reason. "
        "Empty when all of them help.",
    )
    """`accepted` and `rejected` are what turn "sufficient: false" from a bare flag into a
    reading of the evidence. Bounded at 8 because `MAX_SOURCES_KEPT` is the ceiling on what
    a run reads, and a judgement listing more sources than the run gathered is drift.

    **The per-entry text is bounded tightly, and the arithmetic is the reason.** Eight
    accepted entries at 200 + 160 + 160 characters is ~4 100 characters ≈ 1 000 tokens, and
    this list travels into the same 4096-token finalise window as the evidence, the
    `FocusPreparationReport` schema and the generated report. A judgement that crowds out
    the evidence it is a judgement of would be the expensive kind of overflow.

    `source` is deliberately not typed as a URL. These are prompt material and a trace line,
    never a citation menu: a cited URL must still be in `RunState.evidence_urls` (S9), so an
    invented name here is caught by the grounding check exactly as it always was, and typing
    it as a URL would only invite the model to produce one."""

    disagreements: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Where two of the sources above contradict each other, one short "
        "phrase each. Empty when they agree or never overlap.",
    )
    """The field with the clearest downstream job: a contradiction the appraiser saw is
    exactly what the report owes its reader in `unknowns` or `assumptions`. Bounded as
    `missing_information` is, and for the same reason — this travels into a 4096-token
    finalise window."""

    # There is deliberately no validator requiring a follow-up when `sufficient` is
    # false. "Not enough, and nothing specific would help" is a real, honest verdict, and
    # the loop's answer to it is to finalise with populated `unknowns`. Forcing a
    # follow-up would turn that into a retry loop and invite an invented question.
    #
    # Nor is there one tying `accepted`/`rejected` to `sufficient`. A verdict may accept
    # two sources and still judge the set insufficient, or reject every one and still find
    # the question answerable from the snippets — both are real readings, and a validator
    # would turn either into a retry the model cannot diagnose.


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
    missing_context: list[str] = Field(default_factory=list, max_length=4)
    """What the planner said the task does not state, when that is why the run stopped.

    Additive and defaulted, so no construction site moved. It lives on the state rather than
    travelling as a `finalise` parameter for the reason `previous` does: the stage that reads
    it already receives this object, and "what this run knows" has one home. Keeping
    `finalise`'s signature frozen also matters more than usual here — `main.py` and four test
    modules call it, and a new required argument would churn all of them to carry a value
    that is empty on almost every run.

    Empty is the ordinary case and must stay indistinguishable from before: a task that
    could be researched as written, a run that stopped for any other reason, and a planner
    that never filled the field all arrive here as `[]`."""

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
