import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from unbundle.evaluate import Flagged, MatchOutcome
from unbundle.reconcile import expected_fee
from unbundle.record_types import BankLine, Payment, Settlement
from unbundle.money import Paise, format_amount

MODEL = "claude-haiku-4-5-20251001"
OUTAGE_WINDOW_HOURS = 4

# More than a pair, two misses in one window on one method is coincidence and calling it an outage is the confabulation the adjudicator exists to stop
OUTAGE_MINIMUM = 5

# A rejected proposal goes back with the reason it failed, so the model answers against what the arithmetic said rather than guessing twice. Bounded 
# because a model that has not answered by the last turn is not going to
MAX_TURNS = 3
BANK_FEE_CEILING: Paise = 10_000

# A closed set, picking from a list is something a small model does reliably while composing an honest paragraph about its own uncertainty is not and 
# a cause outside the set comes back as NONE_OF_THESE rather than invented
CAUSES = (
    "GATEWAY_OUTAGE",
    "BANK_TRANSFER_CHARGE",
    "RATE_CARD_MISMATCH",
    "SETTLEMENT_NEVER_SENT",
    "NONE_OF_THESE",
)

@dataclass(frozen=True, slots=True)
class Group:
    kind: str
    shared: str
    member_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class Incident:
    cause: str
    shared: str
    member_ids: tuple[str, ...]
    cited_ids: tuple[str, ...]
    accepted: bool
    reason: str
    # How many proposals it took, so a first time answer and one that only landed after the arithmetic pushed back are told apart in the report
    turns: int = 1

@dataclass(frozen=True, slots=True)
class Explanation:
    incidents: tuple[Incident, ...]
    # Findings that grouped with nothing else. A group of one is not an incident and counting it as one turns a real reduction into an apparent one
    individual: tuple[str, ...]
    trace: tuple[str, ...]
    model: str | None
    available: bool


# The proposal is the only thing the model does, everything before it is arithmetic and everything after it is a check, so refusal stops depending 
# on the model being willing to admit ignorance
Propose = Callable[[Group, str], tuple[str, tuple[str, ...]] | None]


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
    trace: list[str] = [
        f"grouped {len(outcome.flagged)} findings into {len(groups)} candidate incidents "
        f"and {len(individual)} standing alone"
    ]

    if propose is None:
        propose = _anthropic_propose()
    if propose is None:
        trace.append("no model configured, findings grouped but not diagnosed")
        return Explanation(
            incidents=tuple(
                Incident(
                    cause="NOT_DIAGNOSED",
                    shared=group.shared,
                    member_ids=group.member_ids,
                    cited_ids=(),
                    accepted=False,
                    reason="no model configured",
                )
                for group in groups
            ),
            individual=individual,
            trace=tuple(trace),
            model=None,
            available=False,
        )

    # A transfer charge is a claim about one settlement's own credit, so the evidence is that settlement's line and nothing else. MatchedCredit names 
    # payments rather than the settlement and every payment in one credit carries the same settlement_id
    by_payment_id = {payment.payment_id: payment for payment in payments}
    line_by_settlement: dict[str, BankLine] = {}
    for credit in outcome.matched:
        if not credit.payment_ids:
            continue
        settlement_id = by_payment_id[credit.payment_ids[0]].settlement_id
        if settlement_id is not None:
            line_by_settlement[settlement_id] = bank_lines[credit.line_index]

    incidents = []
    for group in groups:
        evidence = _render(group, payments, settlements, bank_lines)
        trace.append(f"proposing for {group.kind}: {group.shared}, {len(group.member_ids)} members")

        rejections: list[str] = []
        incident = None
        for turn in range(1, MAX_TURNS + 1):
            proposal = propose(group, _with_rejections(evidence, rejections))
            if proposal is None:
                trace.append(f"  turn {turn}: no usable proposal, recorded as undiagnosed")
                incident = _undiagnosed(group, "model returned nothing usable", turns=turn)
                break

            cause, cited = proposal
            accepted, reason = _adjudicate(
                cause, cited, group, payments, settlements, line_by_settlement
            )
            trace.append(
                f"  turn {turn}: proposed {cause} citing {len(cited)}, "
                f"{'accepted' if accepted else 'rejected'}: {reason}"
            )
            if accepted:
                incident = Incident(
                    cause=cause,
                    shared=group.shared,
                    member_ids=group.member_ids,
                    cited_ids=tuple(cited),
                    accepted=True,
                    reason=reason,
                    turns=turn,
                )
                break

            rejections.append(f"{cause} citing {len(cited)} records was rejected because {reason}")
            if turn == MAX_TURNS:
                incident = Incident(
                    cause="NOT_DIAGNOSED",
                    shared=group.shared,
                    member_ids=group.member_ids,
                    cited_ids=(),
                    accepted=False,
                    reason=reason,
                    turns=turn,
                )

        incidents.append(incident)

    return Explanation(
        incidents=tuple(incidents),
        individual=individual,
        trace=tuple(trace),
        model=MODEL,
        available=True,
    )


# Where refusal actually happens. A cause the cited records do not support is rejected whether or not the model was willing to say it did not know
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
    if cause == "NONE_OF_THESE":
        return True, "refused, no cause in the set fits"

    outside = [entity for entity in cited if entity not in group.member_ids]
    if outside:
        return False, f"cited {len(outside)} records that are not in the group"
    if not cited:
        return False, "cited no records"

    by_id = {payment.payment_id: payment for payment in payments}
    settlement_by_id = {s.settlement_id: s for s in settlements}

    if cause == "GATEWAY_OUTAGE":
        members = [by_id[entity] for entity in cited if entity in by_id]
        if len(members) < OUTAGE_MINIMUM:
            return False, f"{len(members)} payments is coincidence, an outage needs {OUTAGE_MINIMUM}"
        methods = {payment.method for payment in members}
        if len(methods) > 1:
            return False, "cited payments do not share a method"
        hours = [payment.happened_at for payment in members]
        if (max(hours) - min(hours)).total_seconds() > OUTAGE_WINDOW_HOURS * 3600:
            return False, "cited payments do not fall in one window"
        return True, f"{len(members)} {methods.pop()} payments within one window"

    if cause == "RATE_CARD_MISMATCH":
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
        members = [by_id[entity] for entity in cited if entity in by_id]
        if any(payment.settlement_id is not None for payment in members):
            return False, "a cited payment was settled after all"
        return True, f"{len(members)} payments captured and never settled"

    return False, "no check exists for that cause"


# The rejection goes back inside the evidence rather than through a new argument, so aproposer written before the loop existed still runs unchanged
def _with_rejections(evidence: str, rejections: Sequence[str]) -> str:
    if not rejections:
        return evidence
    tried = "\n".join(f"- {line}" for line in rejections)
    return (
        f"{evidence}\n\nEarlier answers were checked against the records and rejected:\n"
        f"{tried}\n\nAnswer again, or answer NONE_OF_THESE if no cause fits."
    )


def _undiagnosed(group: Group, reason: str, turns: int = 1) -> Incident:
    return Incident(
        cause="NOT_DIAGNOSED",
        shared=group.shared,
        member_ids=group.member_ids,
        cited_ids=(),
        accepted=False,
        reason=reason,
        turns=turns,
    )


def _render(
    group: Group,
    payments: Sequence[Payment],
    settlements: Sequence[Settlement],
    bank_lines: Sequence[BankLine],
) -> str:
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


# Imported inside the function and not at the top, so a machine without the package still runs the deterministic pipeline rather than failing at import
def _anthropic_propose() -> Propose | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()

    def propose(group: Group, evidence: str) -> tuple[str, tuple[str, ...]] | None:
        prompt = (
            f"These reconciliation findings share: {group.shared}\n\n"
            f"{evidence}\n\n"
            f"Choose exactly one cause from: {', '.join(CAUSES)}\n"
            "Cite only the record ids above that prove it. If none of the causes fit, "
            "answer NONE_OF_THESE with no citations.\n"
            'Reply as JSON only: {"cause": "...", "cited": ["..."]}'
        )
        try:
            reply = client.messages.create(
                model=MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            body = json.loads(reply.content[0].text)
            return body["cause"], tuple(body.get("cited", []))
        except Exception:
            # Any failure is the same failure as far as the run is concerned and the adjudicator would have rejected an unusable answer anyway
            return None

    return propose