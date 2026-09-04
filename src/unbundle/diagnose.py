import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from unbundle.evaluate import Flagged, MatchOutcome
from unbundle.reconcile import expected_fee
from unbundle.record_types import BankLine, Payment, Settlement
from unbundle.money import Paise, format_amount

MODEL = "claude-haiku-4-5-20251001"

# Limits are at console.groq.com/docs/rate-limits, and the one that binds is the token ceiling rather than the request count because every round resends 
# the whole history

# Overridable so which model answers can be asked of the run rather than edited, and the trace records the name either way
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OUTAGE_WINDOW_HOURS = 4

# More than a pair, two misses in one window on one method is coincidence and calling it an outage is the confabulation the adjudicator exists to stop
OUTAGE_MINIMUM = 5

# A rejected proposal goes back with the reason it failed, so the model answers against what the arithmetic said rather than guessing twice. Bounded
# because a model that has not answered by the last turn is not going to
MAX_TURNS = 3
BANK_FEE_CEILING: Paise = 10_000

# Bounded so a model that never resolves or gives up cannot spin forever, distinct from MAX_TURNS above, which retries a whole proposal after a rejection
INNER_MAX_ROUNDS = 4

# The provider refuses a malformed generation before the model has decided anything, so the request goes again rather than spending an outer turn on it.
# Narrow on purpose, a rejected key answers the same way however many times it is asked, and a rate limit has RATE_LIMIT_ATTEMPTS below because it does not
TOOL_CALL_ATTEMPTS = 2

# A per minute ceiling clears in about two seconds, so a 429 naming it is a queue rather than a refusal and treating it as one loses most of a run's groups.
# A daily one is the opposite and gets no retry at all, see _is_daily_limit
RATE_LIMIT_ATTEMPTS = 6

# Only reached when the provider names no wait of its own, since the message carries one whenever the minute is what ran out
RATE_LIMIT_MAX_WAIT = 60.0

# A closed set, because a small model picks from a list reliably and writes about its own uncertainty badly. A cause outside the set is rejected by _adjudicate
# and giving up is its own tool rather than an entry here, so it never has to be checked against the records the way a real cause does
CAUSES = (
    "GATEWAY_OUTAGE",
    "BANK_TRANSFER_CHARGE",
    "RATE_CARD_MISMATCH",
    "SETTLEMENT_NEVER_SENT",
    "SETTLEMENT_FAILED",
)

@dataclass(frozen=True, slots=True)
class Group:
    kind: str
    shared: str
    member_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class EvidenceMaps:
    by_payment_id: Mapping[str, Payment]
    settlement_by_id: Mapping[str, Settlement]
    line_by_settlement: Mapping[str, BankLine]

@dataclass(frozen=True, slots=True)
class Incident:
    cause: str
    shared: str
    member_ids: tuple[str, ...]
    cited_ids: tuple[str, ...]
    accepted: bool
    reason: str
    # The flagged kind the group was built from, kept beside cause so a credit for this incident lands on the finding the agent actually reasoned about. 
    # An entity can be flagged under two kinds and crediting the entity alone hands the second one a verdict nobody reached
    kind: str = ""
    # How many proposals it took, so a first time answer and one that only landed after the arithmetic pushed back are told apart in the report
    turns: int = 1
    # Tool names looked up on the turn that produced this incident, in order, so what the model chose to check before deciding has somewhere to go
    lookups: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class Explanation:
    incidents: tuple[Incident, ...]
    # Findings that grouped with nothing else. A group of one is not an incident and counting it as one turns a real reduction into an apparent one
    individual: tuple[str, ...]
    trace: tuple[str, ...]
    model: str | None
    available: bool


# cause is None for both a model that gave up and one that returned nothing usable, gave_up is what tells those two apart, so the model's own decision
# to stop is a fact the loop can see rather than something indistinguishable from a broken reply
@dataclass(frozen=True, slots=True)
class Proposal:
    cause: str | None
    cited: tuple[str, ...]
    gave_up: bool
    lookups: tuple[str, ...]
    # The request failure that produced a cause of None, so a first keyed run reports what broke instead of reporting the model as unusable
    error: str | None = None
    # Why the loop came back empty when the request itself was fine, because a reply with no tool call in it and a model that looked things up until the
    # round cap stopped it are different problems with the same empty answer, and one of them is fixed by raising a constant we own
    stopped_because: str | None = None
    # The model's own words for giving up, distinct from error and stopped_because above which
    # describe the plumbing failing rather than the model deciding. Kept here rather than
    # discarded, since a refusal nobody can read tells a merchant no more than an empty answer does
    reason: str | None = None

# Always a Proposal rather than a bare answer or None, so the model's own decision to stop stays visible to the caller. Only the resolution is checked
# against the records, a lookup in between is a read the model chose for itself
Propose = Callable[[Group, str], Proposal]


def group_findings(outcome: MatchOutcome, payments: Sequence[Payment]) -> list[Group]:
    captured = {payment.payment_id: payment for payment in payments}
    groups: list[Group] = []

    missing = [item for item in outcome.flagged if item.kind == "MISSING_SETTLEMENT"]
    groups.extend(_by_method_and_window(missing, captured))

    fee = [item for item in outcome.flagged if item.kind == "FEE_MISMATCH"]
    groups.extend(_by_method(fee, captured))

    for kind in ("BANK_FEE_DEDUCTED", "MANGLED_UTR"):
        members = tuple(item.entity_id for item in outcome.flagged if item.kind == kind)
        if members:
            groups.append(Group(kind=kind, shared=f"{len(members)} credits", member_ids=members))

    # Each on its own, because a credit with nothing behind it shares nothing with the next one and grouping them would invent a pattern that is not there
    for item in outcome.flagged:
        if item.kind == "UNKNOWN_CREDIT":
            groups.append(Group(kind=item.kind, shared="no settlement found", member_ids=(item.entity_id,)))

    return groups


def _by_method_and_window(
    items: Sequence[Flagged], captured: dict[str, Payment]
) -> list[Group]:
    buckets: dict[tuple[str, str, int], list[str]] = {}
    for item in items:
        payment = captured.get(item.entity_id)
        if payment is None:
            continue
        window = payment.happened_at.hour // OUTAGE_WINDOW_HOURS
        key = (payment.method, payment.happened_at.date().isoformat(), window)
        buckets.setdefault(key, []).append(item.entity_id)

    groups = []
    for (method, day, window), members in sorted(buckets.items()):
        start = window * OUTAGE_WINDOW_HOURS
        groups.append(
            Group(
                kind="MISSING_SETTLEMENT",
                shared=f"{method} captured {day} between {start:02d}:00 and {start + OUTAGE_WINDOW_HOURS:02d}:00",
                member_ids=tuple(sorted(members)),
            )
        )
    return groups


def _by_method(items: Sequence[Flagged], captured: dict[str, Payment]) -> list[Group]:
    buckets: dict[str, list[str]] = {}
    for item in items:
        payment = captured.get(item.entity_id)
        if payment is not None:
            buckets.setdefault(payment.method, []).append(item.entity_id)
    return [
        Group(kind="FEE_MISMATCH", shared=f"{method} payments", member_ids=tuple(sorted(members)))
        for method, members in sorted(buckets.items())
    ]


# The adjudicator and the lookup tools both need a settlement's own claimed credit, so this is built once and shared rather than reconstructed by whichever 
# caller needs it
def build_evidence_maps(
    payments: Sequence[Payment], settlements: Sequence[Settlement],
    bank_lines: Sequence[BankLine], outcome: MatchOutcome,
) -> EvidenceMaps:
    by_payment_id = {payment.payment_id: payment for payment in payments}
    settlement_by_id = {settlement.settlement_id: settlement for settlement in settlements}

    # A transfer charge is a claim about one settlement's own credit, so the evidence is that settlement's line and nothing else. MatchedCredit names
    # payments rather than the settlement and every payment in one credit carries the same settlement_id
    line_by_settlement: dict[str, BankLine] = {}
    for credit in outcome.matched:
        if not credit.payment_ids:
            continue
        settlement_id = by_payment_id[credit.payment_ids[0]].settlement_id
        if settlement_id is not None:
            line_by_settlement[settlement_id] = bank_lines[credit.line_index]

    return EvidenceMaps(by_payment_id, settlement_by_id, line_by_settlement)


# BANK_FEE_DEDUCTED and MANGLED_UTR groups name settlement ids, and _render has no detail for an id it cannot resolve to a payment, so this is the only evidence 
# the model ever gets for either kind unless it asks
def _look_up_settlement(
    settlement_id: str, settlement_by_id: Mapping[str, Settlement], line_by_settlement: Mapping[str, BankLine],
) -> dict:
    settlement = settlement_by_id.get(settlement_id)
    if settlement is None:
        return {"error": f"no settlement {settlement_id}"}

    line = line_by_settlement.get(settlement_id)
    result = {
        "settlement_id": settlement.settlement_id,
        "amount": format_amount(settlement.amount),
        "status": settlement.status,
        "settled_at": settlement.settled_at.isoformat(),
        "claimed_credit": format_amount(line.credit) if line is not None else None,
    }
    if line is not None:
        result["claimed_narration"] = line.narration
        result["shortfall"] = format_amount(settlement.amount - line.credit)
    return result


# The charged fee is already in _render's evidence, the agreed one never is, so this is what turns a RATE_CARD_MISMATCH claim from asserted into checkable
def _look_up_payment(payment_id: str, by_payment_id: Mapping[str, Payment]) -> dict:
    payment = by_payment_id.get(payment_id)
    if payment is None:
        return {"error": f"no payment {payment_id}"}

    expected = expected_fee(payment)
    return {
        "payment_id": payment.payment_id,
        "method": payment.method,
        "amount": format_amount(payment.amount),
        "fee": format_amount(payment.fee),
        "expected_fee": format_amount(expected) if expected is not None else None,
        "settlement_id": payment.settlement_id,
        "happened_at": payment.happened_at.isoformat(),
    }


# The plural forms of the two lookups above, so a group does not cost one round per member against INNER_MAX_ROUNDS. A group big enough to run out of
# rounds never finishes citing itself, and the member it never reached is the one the report has to admit was not individually checked
def _look_up_settlements(
    settlement_ids: Sequence[str], settlement_by_id: Mapping[str, Settlement], line_by_settlement: Mapping[str, BankLine],
) -> dict:
    return {
        "settlements": [
            _look_up_settlement(settlement_id, settlement_by_id, line_by_settlement)
            for settlement_id in settlement_ids
        ]
    }


def _look_up_payments(payment_ids: Sequence[str], by_payment_id: Mapping[str, Payment]) -> dict:
    return {"payments": [_look_up_payment(payment_id, by_payment_id) for payment_id in payment_ids]}


def build_lookups(maps: EvidenceMaps) -> dict[str, Callable[[dict], dict]]:
    return {
        "look_up_settlement": lambda args: _look_up_settlement(
            args.get("settlement_id", ""), maps.settlement_by_id, maps.line_by_settlement
        ),
        "look_up_payment": lambda args: _look_up_payment(args.get("payment_id", ""), maps.by_payment_id),
        "look_up_settlements": lambda args: _look_up_settlements(
            args.get("settlement_ids", []), maps.settlement_by_id, maps.line_by_settlement
        ),
        "look_up_payments": lambda args: _look_up_payments(args.get("payment_ids", []), maps.by_payment_id),
    }


LOOKUP_TOOLS = (
    {
        "name": "look_up_settlement",
        "description": "Look up one settlement by id. Returns its amount, status, and the "
        "bank credit matched to it if any, with the shortfall.",
        "input_schema": {
            "type": "object",
            "properties": {"settlement_id": {"type": "string"}},
            "required": ["settlement_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "look_up_payment",
        "description": "Look up one payment by id. Returns its method, amount, the fee it "
        "was charged, and the fee the agreed rate card expects.",
        "input_schema": {
            "type": "object",
            "properties": {"payment_id": {"type": "string"}},
            "required": ["payment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "look_up_settlements",
        "description": "Look up several settlements by id in one call. Returns the same "
        "detail as look_up_settlement for each.",
        "input_schema": {
            "type": "object",
            "properties": {"settlement_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["settlement_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "look_up_payments",
        "description": "Look up several payments by id in one call. Returns the same detail "
        "as look_up_payment for each, so a whole group can be checked in one round instead of "
        "one lookup per member.",
        "input_schema": {
            "type": "object",
            "properties": {"payment_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["payment_ids"],
            "additionalProperties": False,
        },
    },
)

# Structured output through the schema rather than a parsed reply, so a markdown-fenced answer cannot be mistaken for one that named no cause
DECISION_TOOLS = (
    {
        "name": "resolve",
        "description": "Answer with a cause and the ids of this group's own members it explains. "
        "A record looked up along the way is evidence, not something to cite.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cause": {"type": "string", "enum": list(CAUSES)},
                "cited": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["cause", "cited"],
            "additionalProperties": False,
        },
    },
    {
        "name": "give_up",
        "description": "Answer that no cause fits, without citing anything.",
        # A required field rather than an empty schema, a tool with no parameters at all is the
        # shape a model under forced tool choice struggles to call and falls back to prose,
        # the wrong tool name, or raw JSON instead
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
)
TOOLS = LOOKUP_TOOLS + DECISION_TOOLS


def _first_tool_use(response):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block
    return None


# The model chooses which tool and how many times before it stops. A lookup is executed and fed back as the next round's evidence, resolve or give_up ends 
# the loop on the model's own say, and the round cap is only a backstop for a model that reaches neither
def run_tool_loop(
    call_model: Callable[[list], object], messages: list[dict], lookups: Mapping[str, Callable[[dict], dict]],
) -> Proposal:
    looked_up: list[str] = []
    for _ in range(INNER_MAX_ROUNDS):
        response = call_model(messages)
        block = _first_tool_use(response)
        if block is None:
            return Proposal(
                cause=None,
                cited=(),
                gave_up=False,
                lookups=tuple(looked_up),
                stopped_because="the reply carried no tool call",
            )
        messages.append({"role": "assistant", "content": response.content})

        if block.name == "resolve":
            cause = block.input.get("cause", "")
            cited = tuple(block.input.get("cited", []))
            return Proposal(cause=cause, cited=cited, gave_up=False, lookups=tuple(looked_up))
        if block.name == "give_up":
            return Proposal(
                cause=None,
                cited=(),
                gave_up=True,
                lookups=tuple(looked_up),
                reason=block.input.get("reason"),
            )

        lookup = lookups.get(block.name)
        # A tool name outside the offered set is not one the client sent, an error goes back rather than a crash so a model that names the wrong tool can
        # recover next round. It is not counted as a lookup, the client never ran anything real
        if lookup is not None:
            result = lookup(block.input)
            looked_up.append(block.name)
        else:
            result = {"error": f"no such tool {block.name}"}
        messages.append(
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}]}
        )
    return Proposal(
        cause=None,
        cited=(),
        gave_up=False,
        lookups=tuple(looked_up),
        stopped_because=f"the model looked things up {INNER_MAX_ROUNDS} times without deciding",
    )


def explain(
    outcome: MatchOutcome,
    payments: Sequence[Payment],
    settlements: Sequence[Settlement],
    bank_lines: Sequence[BankLine],
    propose: Propose | None = None,
) -> Explanation:
    all_groups = group_findings(outcome, payments)
    groups = [group for group in all_groups if len(group.member_ids) > 1]
    individual = tuple(
        entity for group in all_groups if len(group.member_ids) == 1 for entity in group.member_ids
    )
    # group_findings does not sort every flagged kind, so the entities across all_groups are usually fewer than outcome.flagged, and saying the full count 
    # was grouped overstates what the agent stage ever saw
    sorted_count = sum(len(group.member_ids) for group in all_groups)
    trace: list[str] = [
        f"sorted {sorted_count} of {len(outcome.flagged)} flagged findings into "
        f"{len(groups)} candidate incidents and {len(individual)} standing alone"
    ]

    # Built ahead of the propose-is-None check so both the lookup tools and the adjudicator can use it, and so a no-key run pays the same small cost as a 
    # keyed one rather than a branch that only exists to skip building it
    maps = build_evidence_maps(payments, settlements, bank_lines, outcome)

    # An injected proposer is scripted rather than a model, so naming a model here would put a name in results/ that never answered anything
    model_name = "scripted"
    if propose is None:
        propose, model_name = _configured_propose(build_lookups(maps))
    if propose is None:
        missing = _no_model_reason()
        trace.append(f"no model configured, findings grouped but not diagnosed: {missing}")
        return Explanation(
            incidents=tuple(
                Incident(
                    cause="NOT_DIAGNOSED",
                    shared=group.shared,
                    kind=group.kind,
                    member_ids=group.member_ids,
                    cited_ids=(),
                    accepted=False,
                    reason=missing,
                )
                for group in groups
            ),
            individual=individual,
            trace=tuple(trace),
            model=None,
            available=False,
        )

    incidents = []
    for group in groups:
        evidence = _render(group, payments)
        trace.append(f"proposing for {group.kind}: {group.shared}, {len(group.member_ids)} members")

        rejections: list[str] = []
        # Every turn's lookups, not only the winning one, so a group rejected twice before it lands still reports the full cost of getting there
        lookups_so_far: list[str] = []
        incident = None
        for turn in range(1, MAX_TURNS + 1):
            proposal = propose(group, _with_rejections(evidence, rejections))
            lookups_so_far.extend(proposal.lookups)

            # A model that decided nothing fits is not the same event as one that returned nothing usable, and folding them together is what made the 
            # stopping rule invisible
            if proposal.gave_up:
                trace.append(
                    f"  turn {turn}: looked up {len(proposal.lookups)} record(s) this turn, gave up, no cause fit"
                )
                incident = Incident(
                    cause="GAVE_UP",
                    shared=group.shared,
                    kind=group.kind,
                    member_ids=group.member_ids,
                    cited_ids=(),
                    accepted=True,
                    reason=proposal.reason or "the model looked and found no cause it could support",
                    turns=turn,
                    lookups=tuple(lookups_so_far),
                )
                break

            if proposal.cause is None:
                failed = proposal.error or proposal.stopped_because or "model returned nothing usable"
                trace.append(f"  turn {turn}: {failed}, retrying" if turn < MAX_TURNS else f"  turn {turn}: {failed}")
                # A model that answered badly has decided nothing, so the turn is spent and not the group, and another attempt is worth more than an incident saying 
                # nothing was found. give_up above still ends a group at once, because that is the model deciding rather than failing
                if turn == MAX_TURNS:
                    incident = _undiagnosed(
                        group,
                        failed,
                        # Refused outranks unanswered, since a group the adjudicator turned down did get a cause to weigh, whatever the last turn did
                        cause="NOT_DIAGNOSED" if rejections else "NOT_ANSWERED",
                        turns=turn,
                        lookups=tuple(lookups_so_far),
                    )
                continue

            cause, cited = proposal.cause, proposal.cited
            accepted, reason = _adjudicate(
                cause, cited, group, payments, settlements, maps.line_by_settlement
            )
            trace.append(
                f"  turn {turn}: looked up {len(proposal.lookups)} record(s) this turn, proposed "
                f"{cause} citing {len(cited)}, {'accepted' if accepted else 'rejected'}: {reason}"
            )
            if accepted:
                incident = Incident(
                    cause=cause,
                    shared=group.shared,
                    kind=group.kind,
                    member_ids=group.member_ids,
                    cited_ids=tuple(cited),
                    accepted=True,
                    reason=reason,
                    turns=turn,
                    lookups=tuple(lookups_so_far),
                )
                break

            rejections.append(f"{cause} citing {len(cited)} records was rejected because {reason}")
            if turn == MAX_TURNS:
                incident = Incident(
                    cause="NOT_DIAGNOSED",
                    shared=group.shared,
                    kind=group.kind,
                    member_ids=group.member_ids,
                    cited_ids=(),
                    accepted=False,
                    reason=reason,
                    turns=turn,
                    lookups=tuple(lookups_so_far),
                )

        incidents.append(incident)

    return Explanation(
        incidents=tuple(incidents),
        individual=individual,
        trace=tuple(trace),
        model=model_name,
        available=True,
    )


# Shared by both causes that can be argued over the same MISSING_SETTLEMENT group, so the outage bar is one fact and not two. GATEWAY_OUTAGE needs it
# to hold, SETTLEMENT_NEVER_SENT needs it to fail
def _outage_shape(members: Sequence[Payment]) -> tuple[bool, str]:
    if len(members) < OUTAGE_MINIMUM:
        return False, f"{len(members)} payments is coincidence, an outage needs {OUTAGE_MINIMUM}"
    methods = {payment.method for payment in members}
    if len(methods) > 1:
        return False, "cited payments do not share a method"
    hours = [payment.happened_at for payment in members]
    if (max(hours) - min(hours)).total_seconds() > OUTAGE_WINDOW_HOURS * 3600:
        return False, "cited payments do not fall in one window"
    return True, ""


# Where a defended cause is checked against the records, so how sure the model sounded decides nothing. Giving up never reaches this function, it is
# caught in explain() before a cause exists to check
def _adjudicate(
    cause: str,
    cited: Sequence[str],
    group: Group,
    payments: Sequence[Payment],
    settlements: Sequence[Settlement],
    line_by_settlement: Mapping[str, BankLine],
) -> tuple[bool, str]:
    if cause not in CAUSES:
        return False, f"{cause} is not one of the allowed causes"

    outside = [entity for entity in cited if entity not in group.member_ids]
    if outside:
        # Named and not just counted, so a model that cited one wrong id among several has
        # something to remove on the next turn instead of resending the identical proposal
        return False, f"cited {', '.join(outside)}, not in the group"
    if not cited:
        return False, "cited no records"

    by_id = {payment.payment_id: payment for payment in payments}
    settlement_by_id = {s.settlement_id: s for s in settlements}

    if cause == "GATEWAY_OUTAGE":
        members = [by_id[entity] for entity in cited if entity in by_id]
        fits, reason = _outage_shape(members)
        if not fits:
            return False, reason
        # A payment the gateway assigned to a settlement was taken, so a settlement that then failed is its own cause and not this one. The grouping
        # window is wider than an outage, so a real one arrives mixed in with payments either side of it and the claim has to name the ones that never settled
        settled = [payment for payment in members if payment.settlement_id is not None]
        if settled:
            return False, f"{len(settled)} cited payments were assigned to a settlement, so the gateway took them"
        return True, f"{len(members)} {members[0].method} payments within one window"

    if cause == "RATE_CARD_MISMATCH":
        # A fee charged off the rate card is what a FEE_MISMATCH finding asks, and a payment flagged under both kinds sits in a group of each, so accepting
        # this on the other one prints a rate dispute over captures that never settled and sends the merchant to argue a fee rather than chase a transfer
        if group.kind != "FEE_MISMATCH":
            return False, f"a rate card dispute does not explain a {group.kind} finding"
        members = [by_id[entity] for entity in cited if entity in by_id]
        # A payment with no agreed rate cannot support a rate card claim, comparing its fee against nothing reads as a mismatch on the one payment 
        # nothing can price. Citing one is enough to lose the claim, the model chose what to cite and every citation has to stand on its own
        priced = [(payment, expected_fee(payment)) for payment in members]
        wrong = [payment for payment, fee in priced if fee is not None and payment.fee != fee]
        if len(wrong) != len(members) or not wrong:
            return False, "not every cited payment was charged off the rate card"
        return True, f"{len(wrong)} payments charged off the agreed rate"

    if cause == "BANK_TRANSFER_CHARGE":
        for entity in cited:
            settlement = settlement_by_id.get(entity)
            if settlement is None:
                return False, f"{entity} is not a settlement"
            # The claim is about this settlement's own credit, so the evidence is a lookup and never a search. Scanning the statement accepts the claim 
            # on any credit that happens to sit a plausible amount short of an unrelated settlement
            line = line_by_settlement.get(entity)
            if line is None:
                return False, f"no credit was matched to {entity}"
            if not 0 < settlement.amount - line.credit <= BANK_FEE_CEILING:
                return False, f"{entity} was not short by a bank charge"
        return True, f"{len(cited)} credits short by a transfer charge"

    if cause == "SETTLEMENT_NEVER_SENT":
        # Why money never arrived is what a MISSING_SETTLEMENT finding asks, and a FEE_MISMATCH group holds payments that never settled too, so without this
        # the fee finding is handed a verdict about the transfer and the merchant is told to check the fee
        if group.kind != "MISSING_SETTLEMENT":
            return False, f"a settlement never sent does not explain a {group.kind} finding"
        members = [by_id[entity] for entity in cited if entity in by_id]
        # Cite a settlement id and the filter above drops it, leaving nothing for the check below to disagree with, so a group of bank credits reads as nought
        # payments never settled and the claim is granted on an empty set
        if not members:
            return False, "cited no payments"
        if any(payment.settlement_id is not None for payment in members):
            return False, "a cited payment was settled after all"
        # Outage is the more specific claim, so a group that clears the outage bar is not this cause's to take even though nothing here contradicts it directly
        fits, _ = _outage_shape(members)
        if fits:
            return False, f"{len(members)} payments sharing one method and one window is GATEWAY_OUTAGE's shape, not this cause's"
        return True, f"{len(members)} payments captured and never settled"

    if cause == "SETTLEMENT_FAILED":
        # This answers why money never arrived, which is the only thing a MISSING_SETTLEMENT finding asks. A fee charged off the rate card was already wrong
        # before the settlement was built, so accepting it there would hand a FEE_MISMATCH finding a verdict about something else and report it as explained
        if group.kind != "MISSING_SETTLEMENT":
            return False, f"a failed settlement does not explain a {group.kind} finding"
        members = [by_id[entity] for entity in cited if entity in by_id]
        # The same empty set the cause above carries a guard for, a group of settlement ids leaves nothing for the checks below to disagree with and every
        # one of them passes over an empty list, so the claim would be granted on no evidence at all
        if not members:
            return False, "cited no payments"
        # A payment the gateway never assigned anywhere is SETTLEMENT_NEVER_SENT's story, and a group holding some of each is two stories, so the claim has
        # to name only the ones that were assigned and lose it if it reaches wider
        unassigned = [payment for payment in members if payment.settlement_id is None]
        if unassigned:
            return False, f"{len(unassigned)} cited payments were never assigned to a settlement"
        alive = [
            payment
            for payment in members
            if payment.settlement_id not in settlement_by_id
            or settlement_by_id[payment.settlement_id].status != "failed"
        ]
        if alive:
            return False, f"{len(alive)} cited payments name a settlement that did not fail"
        failed_ids = sorted({payment.settlement_id for payment in members})
        return True, f"{len(members)} payments on {', '.join(failed_ids)}, which failed"

    return False, "no check exists for that cause"


# The rejection goes back inside the evidence rather than through a new argument, so a proposer written before the loop existed still runs unchanged
def _with_rejections(evidence: str, rejections: Sequence[str]) -> str:
    if not rejections:
        return evidence
    tried = "\n".join(f"- {line}" for line in rejections)
    return (
        f"{evidence}\n\nEarlier answers were checked against the records and rejected:\n"
        f"{tried}\n\nAnswer again, or give up if no cause fits."
    )


def _undiagnosed(
    group: Group,
    reason: str,
    cause: str = "NOT_DIAGNOSED",
    turns: int = 1,
    lookups: tuple[str, ...] = (),
) -> Incident:
    return Incident(
        cause=cause,
        shared=group.shared,
        kind=group.kind,
        member_ids=group.member_ids,
        cited_ids=(),
        accepted=False,
        reason=reason,
        turns=turns,
        lookups=lookups,
    )


# Payment detail only, so a BANK_FEE_DEDUCTED or MANGLED_UTR group whose members are settlement ids gets the bare id and nothing else. The model has 
# look_up_settlement for those
def _render(group: Group, payments: Sequence[Payment]) -> str:
    by_id = {payment.payment_id: payment for payment in payments}
    lines = []
    for entity in group.member_ids[:40]:
        payment = by_id.get(entity)
        if payment is not None:
            lines.append(
                f"{payment.payment_id} {payment.method} {format_amount(payment.amount)} "
                f"captured {payment.happened_at.isoformat()} fee {format_amount(payment.fee)} "
                f"settlement {payment.settlement_id or 'none'}"
            )
        else:
            lines.append(entity)
    return "\n".join(lines)


# Groq first because its free tier is enough to run this, Anthropic second, and the name comes back with the proposer so the trace records which model actually 
# answered rather than which one the file was written against
def _configured_propose(
    lookups: Mapping[str, Callable[[dict], dict]]
) -> tuple[Propose | None, str | None]:
    groq_propose = _groq_propose(lookups)
    if groq_propose is not None:
        return groq_propose, GROQ_MODEL
    anthropic_propose = _anthropic_propose(lookups)
    if anthropic_propose is not None:
        return anthropic_propose, MODEL
    return None, None


# A key with no SDK behind it and no key at all both leave _configured_propose with nothing to return, and the two are opposite jobs, one is a signup
# and the other is an install, so the run says which rather than saying only that no model was configured
def _no_model_reason() -> str:
    keyed = [
        variable
        for variable in ("GROQ_API_KEY", "ANTHROPIC_API_KEY")
        if os.environ.get(variable)
    ]
    if not keyed:
        return "neither GROQ_API_KEY nor ANTHROPIC_API_KEY is set"
    return (
        f"{' and '.join(keyed)} set but the SDK is not installed, "
        'install it with pip install -e ".[model]"'
    )


# One wording for both providers, so a change to what the model is asked cannot land on one and miss the other
def _prompt(group: Group, evidence: str) -> str:
    return (
        "Work out why this group of reconciliation findings is unexplained. These findings "
        f"share: {group.shared}\n\n{evidence}\n\n"
        "Look up a settlement or a payment before you decide, if the evidence above is not "
        "enough to support a cause on its own. look_up_settlements and look_up_payments check "
        "several ids in one call, use them instead of one lookup per member when the group is "
        f"large. Resolve with exactly one cause from: {', '.join(CAUSES)}, citing every member "
        "you have evidence for rather than a partial sample of the group. If none of the "
        "causes fit, give up rather than guess."
    )


# Groq speaks the OpenAI tool-calling shape and run_tool_loop speaks the Anthropic one. The functions below are the only place that difference is written down, 
# so the loop, the adjudicator and every test stay as they are and the provider is a choice rather than an architecture. From console.groq.com/docs/tool-use
def _as_openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in TOOLS
    ]


def _as_openai_messages(messages: Sequence[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": content})
            continue
        if message["role"] == "assistant":
            converted.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": block.id,
                            "type": "function",
                            # Arguments travel as a JSON string on the wire where Anthropic sends a dict
                            "function": {"name": block.name, "arguments": json.dumps(block.input)},
                        }
                        for block in content
                        if getattr(block, "type", None) == "tool_use"
                    ],
                }
            )
            continue
        converted += [
            {"role": "tool", "tool_call_id": part["tool_use_id"], "content": part["content"]}
            for part in content
            if part.get("type") == "tool_result"
        ]
    return converted


def _as_anthropic_response(completion) -> SimpleNamespace:
    blocks = []
    for call in completion.choices[0].message.tool_calls or ():
        try:
            arguments = json.loads(call.function.arguments)
        except (TypeError, ValueError):
            # A small model can emit arguments that are not valid JSON, and an empty dict reaches the lookup as a
            # missing id, which answers with an error the model can read rather than stopping the run on a parse
            arguments = {}
        blocks.append(
            SimpleNamespace(type="tool_use", name=call.function.name, input=arguments, id=call.id)
        )
    return SimpleNamespace(content=blocks)


# Matched on the message rather than the exception type, because the two SDKs raise their own classes and importing either one here is what the lazy imports
# below exist to avoid
def _is_rate_limit(message: str) -> bool:
    return "rate_limit" in message or "Error code: 429" in message


# The two ceilings arrive as the same 429 and are opposite problems. A run that spent the day's tokens cannot get them back by waiting, and the waits it is
# handed run minutes rather than seconds and grow, so RATE_LIMIT_ATTEMPTS of them per group is hours spent on a budget that only returns tomorrow
def _is_daily_limit(message: str) -> bool:
    return "TPD" in message or "tokens per day" in message


# Minutes and seconds as well as bare seconds, because the daily ceiling states its wait as 4m59.808s and reading only the seconds form matches none of
# those, falling back to a backoff of our own that has no relation to what was asked for
def _retry_after(message: str, attempt: int) -> float:
    named = re.search(r"try again in (?:([0-9]+)m)?([0-9.]+)s", message)
    if named is None:
        return min(float(2**attempt), RATE_LIMIT_MAX_WAIT)
    minutes = float(named.group(1) or 0)
    # A tenth of a second past what the provider named, since the moment it names is when the window clears and asking exactly then can arrive before it has
    return min(minutes * 60 + float(named.group(2)) + 0.1, RATE_LIMIT_MAX_WAIT)


# Both providers fail the same way and the handling sits here rather than once inside each closure, because a copy per provider is a rule that can go out
# of step with itself, and because an except reachable only through an installed SDK and a live key is an except nothing can watch
def _propose_or_report(
    call_model: Callable[[list[dict]], object],
    messages: list[dict],
    lookups: Mapping[str, Callable[[dict], dict]],
) -> Proposal:
    # Every attempt starts from the opening prompt rather than resuming the one that broke, so the lookups the loop reports are the ones that attempt
    # really made and a retry cannot inherit tool results it never asked for
    opening = list(messages)
    # Counted apart, because a group that meets both would otherwise spend one budget on the other and report whichever it ran out of second
    malformed = waited = 0
    while True:
        try:
            return run_tool_loop(call_model, list(opening), lookups)
        except Exception as failure:
            # A request failure is not the model giving up, it never got to decide anything, so the type and message go out with it, because a rate limit,
            # a rejected key and a wrong model name are different jobs and one sentence about the reply names none of them

            # Groq's rate limit body carries the account's org id, useful to nobody reading a trace and not something a public repo needs, so it is stripped here
            # rather than left in results/ when that goes into the commit
            message = re.sub(r"org_[A-Za-z0-9]+", "org_<redacted>", str(failure))
            # Waited out rather than retried straight away, because asking again inside the same minute spends tokens on a window that has not moved and
            # brings the failure forward. The daily ceiling falls through to the report below, so a run that has spent the budget says so rather than
            # waiting an hour to say the same thing
            if _is_rate_limit(message) and not _is_daily_limit(message) and waited < RATE_LIMIT_ATTEMPTS:
                waited += 1
                time.sleep(_retry_after(message, waited))
                continue
            # output_parse_failed is Groq's name for the same event tool_use_failed names elsewhere, the provider refusing a malformed generation before
            # the model decided anything, so it gets the same narrow retry
            if (
                "tool_use_failed" in message or "output_parse_failed" in message
            ) and malformed < TOOL_CALL_ATTEMPTS - 1:
                malformed += 1
                continue
            return Proposal(
                cause=None,
                cited=(),
                gave_up=False,
                lookups=(),
                error=f"{type(failure).__name__}: {message}",
            )


# Imported inside the function and not at the top, so a machine without the package still runs the deterministic pipeline rather than failing at import
def _groq_propose(lookups: Mapping[str, Callable[[dict], dict]]) -> Propose | None:
    if not os.environ.get("GROQ_API_KEY"):
        return None
    try:
        import groq
    except ImportError:
        return None

    client = groq.Groq()

    def propose(group: Group, evidence: str) -> Proposal:
        messages: list[dict] = [{"role": "user", "content": _prompt(group, evidence)}]

        def call_model(messages: list[dict]):
            return _as_anthropic_response(
                client.chat.completions.create(
                    model=GROQ_MODEL,
                    max_tokens=1024,
                    tools=_as_openai_tools(),
                    # Every round has to end in a tool call, a plain sentence is not an answer the loop can read
                    tool_choice="required",
                    parallel_tool_calls=False,
                    messages=_as_openai_messages(messages),
                )
            )

        return _propose_or_report(call_model, messages, lookups)

    return propose


# Imported inside the function and not at the top, so a machine without the package still runs the deterministic pipeline rather than failing at import
def _anthropic_propose(lookups: Mapping[str, Callable[[dict], dict]]) -> Propose | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()

    def propose(group: Group, evidence: str) -> Proposal:
        messages: list[dict] = [{"role": "user", "content": _prompt(group, evidence)}]

        def call_model(messages: list[dict]):
            return client.messages.create(
                model=MODEL,
                max_tokens=1024,
                tools=list(TOOLS),
                tool_choice={"type": "any", "disable_parallel_tool_use": True},
                messages=messages,
            )

        return _propose_or_report(call_model, messages, lookups)

    return propose