"""The state the Assistant Agent carries between its nodes.

Separate from the Translation Agent's ``AgentState`` on purpose. The two agents
share infrastructure and nothing else: this one is invoked on request rather
than on every message, it may take seconds rather than milliseconds, and it
produces rows a human then approves instead of text delivered immediately.
Merging the two states would make every translation carry fields it can never
use, and every change to one agent a reason to re-read the other.

None of these keys are part of `docs/api/contract.md`. What the client sees is the
`ActionProposalResponse` rows and the reply text, both of which have their own
schemas; the shape in here is free to change without a contract amendment, for
the same reason ADR-16 gives for `telemetry`.
"""

from __future__ import annotations

from typing import Any, TypedDict

# How many times the planner may look at what the tools returned and decide to
# do something else. Bounded because a planner that can always ask for one more
# tool will, and each round is a model call the person is waiting through.
# Three is enough for the shape these requests actually take — find something,
# read it, act on it — and short enough that a confused run ends rather than
# grinding.
MAX_REPLANS = 3


class PlannedStep(TypedDict):
    """One tool call the planner chose, and why.

    ``tool`` must name something in the registry. It is not a free string in
    practice: `route` dispatches on it, and a name the registry does not contain
    reaches nothing — a run that ends looking successful with nothing done. The
    parse step drops unknown names with a warning rather than letting them
    through.
    """

    tool: str
    arguments: dict[str, Any]
    reason: str


class Observation(TypedDict):
    """What one tool call produced, as the planner will read it back.

    Text rather than the tool's own return value. The planner is a language
    model, and handing it an ORM row or a nested payload teaches it to expect
    fields that are part of no contract — then to plan around them.
    """

    tool: str
    ok: bool
    summary: str


class AssistantState(TypedDict, total=False):
    """Everything the assistant graph reads or writes for one request.

    ``total=False`` because most keys are produced partway through: a run that
    stops at `ask_permission` never has a plan, and one that only summarises
    never has proposals.
    """

    # --- Supplied by the caller ---
    conversation_id: str
    user_id: str
    request_text: str
    # IANA name from the client. Needed before resolving a relative time like "tomorrow morning".
    # can be resolved at all, and never guessed: `normalize_action_time` refuses
    # rather than inventing an offset.
    timezone: str | None

    # --- Produced by the nodes ---
    # Set when `check_consent` stops the run; names the scope the user must
    # grant. The flow stays inside the graph rather than raising, so the caller
    # gets a sentence to show instead of an exception to translate.
    missing_consent: str | None
    memory: list[str]
    # Verbatim text of the private messages the recent window contained. Held so
    # a generated summary can be checked against them before it is shown: the
    # window returns what the *caller* may read, which includes the assistant's
    # own private replies to them, and a summary quoting one would put it in
    # front of the group (ADR-31). Never sent anywhere — it only ever leaves
    # this state as a boolean.
    private_texts: list[str]
    # Steps the planner asked for and that have not run yet. Consumed by
    # `run_tools`, which empties it — so a non-empty list after that node means
    # something was left deliberately, not forgotten.
    plan: list[PlannedStep]
    # Everything the tools have returned so far, in order. Grows across replans,
    # because the planner's second decision is only better than its first if it
    # can see what the first one produced.
    observations: list[Observation]
    # How many times the planner has already been asked again. Compared against
    # MAX_REPLANS rather than tracked by the planner itself: a model asked to
    # count its own turns will miscount, and the budget is the only thing
    # standing between a confused run and an unbounded one.
    replan_count: int
    # Set when the request cannot be acted on without an answer from the person.
    # Asking is a successful outcome, not a failure — guessing which of two
    # meetings to move is the failure.
    clarification: str | None
    summary: dict[str, Any] | None
    proposals: list[dict[str, Any]]
    # Proposal ids the human approved, handed back in through `Command(resume=)`.
    approved_proposal_ids: list[str]
    # Per-proposal corrections the approver made at the gate — a fixed time, a
    # different title, how far ahead to be reminded. Keyed by proposal id. This
    # is the thing a direct calendar write could never offer: by the time the
    # person saw it, it would already have happened.
    proposal_annotations: dict[str, dict[str, Any]]
    executed: list[dict[str, Any]]
    reply: str

    # --- Diagnostics ---
    # Same rule the Translation Agent follows: a node records the failure here
    # and the flow continues to a node that can still answer. Nothing in this
    # graph may raise into the caller's request.
    error: str | None
    telemetry: dict[str, Any]
