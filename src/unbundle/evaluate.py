from collections.abc import Sequence
from dataclasses import dataclass

from unbundle.synthetic import Dataset
from unbundle.money import Paise
from unbundle.record_types import Payment
from unbundle.ground_truth import PROBLEMS, Label, LabelKind

REPORTED_KINDS: tuple[LabelKind, ...] = (
    "BANK_FEE_DEDUCTED",
    "BATCHED",
    "CHARGEBACK_LATER",
    "DUPLICATE_PAYMENT",
    "FAILED_RETRY",
    "FEE_MISMATCH",
    "GATEWAY_OUTAGE",
    "HELD_BACK",
    "MANGLED_UTR",
    "MISSING_SETTLEMENT",
    "NETWORK_UNKNOWN",
    "ORDER_ID_MISSING",
    "PARTIAL_REFUND",
    "ROUNDING_DRIFT",
    "T_PLUS_TWO",
    "UNKNOWN_CREDIT",
    "UNLINKED_ORDER",
)

@dataclass(frozen=True, slots=True)
class MatchedCredit:
    # Two bank lines can carry the same narration, so the position in the statement is what tells them apart and the narration is only for the report
    line_index: int
    narration: str
    payment_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class Flagged:
    entity_id: str
    kind: LabelKind

# What reconcile.py has to produce, written before the matcher so the matcher is built to fit the measurement and not the other way round. flagged is 
# what a human has to act on and observed is what nobody needs to do anything about, which stops a batched credit padding the exception list
@dataclass(frozen=True, slots=True)
class MatchOutcome:
    matched: tuple[MatchedCredit, ...]
    flagged: tuple[Flagged, ...]
    observed: tuple[Flagged, ...]
    in_flight: tuple[str, ...]
    # One list each and never two, so the run can add them up and show nothing fell out
    received: tuple[str, ...]
    unconfirmed: tuple[str, ...]
    # What the fee mismatches cost, summed where the charge is known and the agreed rate is too. Accumulated here because the matcher already holds both numbers 
    # and a report saying 44 payments were charged wrong without saying what that came to is not a number to act on
    fee_overcharged: Paise = 0

@dataclass(frozen=True, slots=True)
class ClassResult:
    kind: LabelKind
    expected: int
    detected: int

@dataclass(frozen=True, slots=True)
class Report:
    credits_total: int
    credits_claimed: int
    credits_matched: int
    credit_value_total: Paise
    credit_value_matched: Paise
    # The four fates below have to add up to this
    money_captured: Paise
    # Gross, so the fates add up to what was captured. The statement shows this less the fees, the refunds and the bank's transfer charge
    money_received: Paise
    # Razorpay says it settled and no bank line was matched to it, so the money is neither confirmed arrived nor known to be missing
    money_unconfirmed: Paise
    money_at_risk_reported: Paise
    money_at_risk_actual: Paise
    money_missed: Paise
    money_wrongly_cleared: Paise
    # Both figures above read MISSING_SETTLEMENT alone, so they are silent on the seven other problem kinds. These two cover all eight, one as a count of 
    # what went unnamed and one as the value of the entities nothing named at all
    unnamed_problems: int
    money_unexplained: Paise
    # The money at stake per class, each a different kind of exposure. Deliberately never summed because a credit that arrived unattributed and a payment 
    # owed back to a customer are not the same money and a total of them is a number describing nothing
    money_unattributed_credit: Paise
    money_owed_back: Paise
    money_bank_charges: Paise
    money_fee_overcharged: Paise
    # Due and not arrived yet, the only figure here that is not about a problem
    money_in_flight: Paise
    # Orders the merchant cannot tie to any payment. Not money missing, the payment very likely arrived, what is lost is knowing which order it belongs to
    money_unlinked: Paise
    # Fees on payments whose card network the export does not name. Not money missing and not money overcharged, it is the amount this run has 
    # no published rate to check
    money_fee_unverified: Paise
    flagged_total: int
    flagged_real: int
    in_flight_wrongly_flagged: int
    records: int
    seconds: float
    by_class: tuple[ClassResult, ...]
    # The agent stage is a live model, not reproducible from the seed, so these four never enter ledger.json or by_class. They describe one run's agent output, 
    # not the dataset
    flagged_agent_explained: int
    # Money whose disposition the verdict changes, wait or escalate. Never the whole of the line below it, which counts turnover sitting behind findings 
    # that dispute a fee
    money_agent_reclassified: Paise
    value_behind_agent_findings: Paise
    gateway_outage_agent_verified: int

    @property
    def match_rate_by_count(self) -> float:
        if self.credits_total == 0:
            return 0.0
        return self.credits_matched / self.credits_total

    @property
    def match_rate_by_value(self) -> float:
        if self.credit_value_total == 0:
            return 0.0
        return self.credit_value_matched / self.credit_value_total

    @property
    def precision(self) -> float:
        if self.flagged_total == 0:
            return 0.0
        return self.flagged_real / self.flagged_total

    @property
    def wasted_investigations(self) -> int:
        return self.flagged_total - self.flagged_real

    @property
    def records_per_second(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return self.records / self.seconds


def evaluate(
    dataset: Dataset,
    outcome: MatchOutcome,
    seconds: float,
    # Entity and kind pairs the agent stage named a verified cause for, and the entities it
    # called GATEWAY_OUTAGE specifically. Both come from the adjudicator's arithmetic check,
    # not from the model's word, and default empty so a no-key run scores exactly as before
    agent_resolved: frozenset[tuple[str, str]] = frozenset(),
    agent_outage: frozenset[str] = frozenset(),
) -> Report:
    # Every money figure below is unsound if the fates do not cover the payments, so scoring comes after the check and not instead of it
    check_books(outcome, dataset.payments)

    settlement_by_line = dict(dataset.credit_source)
    payments_by_settlement: dict[str, set[str]] = {}
    for payment in dataset.payments:
        if payment.settlement_id is not None:
            payments_by_settlement.setdefault(payment.settlement_id, set()).add(payment.payment_id)

    amount_by_payment = {payment.payment_id: payment.amount for payment in dataset.payments}
    amount_by_settlement = {s.settlement_id: s.amount for s in dataset.settlements}
    credit_by_line = {index: line.credit for index, line in enumerate(dataset.bank_lines)}

    credits_matched = 0
    credit_value_matched = 0
    claimed_payments: set[str] = set()

    for claim in outcome.matched:
        claimed_payments.update(claim.payment_ids)
        settlement_id = settlement_by_line.get(claim.line_index)
        if settlement_id is None:
            continue
        # A match is correct only when the claimed payment set is exactly the true set because a payment set that is nearly right means money attributed to the
        # wrong orders and a finance person cannot act on nearly
        if set(claim.payment_ids) == payments_by_settlement.get(settlement_id, set()):
            credits_matched += 1
            credit_value_matched += credit_by_line.get(claim.line_index, 0)

    # Observed counts as noticed, a batched credit that was spotted was spotted whether or not a human needs to act. Pairs and not a mapping from entity to kind, 
    # one settlement carries both BATCHED and ROUNDING_DRIFT and a mapping keeps only the last one
    noticed = outcome.flagged + outcome.observed
    noticed_pairs = {(item.entity_id, item.kind) for item in noticed}
    noticed_entities = {item.entity_id for item in noticed}

    # Paired with the kind and not keyed on the entity alone, because a payment that settled late carries a label without being a problem and matching on the 
    # id would score a wrong flag against it as correct
    real_pairs = {
        (label.entity_id, label.kind) for label in dataset.labels if label.kind in PROBLEMS
    }
    in_flight_entities = {label.entity_id for label in dataset.labels if label.kind == "IN_FLIGHT"}

    flagged_real = sum(1 for item in outcome.flagged if (item.entity_id, item.kind) in real_pairs)
    # Only a missing settlement claim counts against an in flight payment, because a fee mismatch on a payment that has not settled yet is a real finding 
    # and not a false alarm
    in_flight_wrongly_flagged = sum(
        1
        for item in outcome.flagged
        if item.entity_id in in_flight_entities and item.kind == "MISSING_SETTLEMENT"
    )

    # Both kinds are money the merchant is owed and did not get, so both belong in the two figures
    # below. A held back payment is the harder half, the settlement it names really did settle and
    # the credit for it really did arrive, so nothing about the record looks wrong
    owed = ("MISSING_SETTLEMENT", "HELD_BACK")
    missing = [label for label in dataset.labels if label.kind in owed]
    money_at_risk_actual = sum(amount_by_payment.get(label.entity_id, 0) for label in missing)
    money_missed = sum(
        amount_by_payment.get(label.entity_id, 0)
        for label in missing
        if label.entity_id not in noticed_entities
    )
    # Separate from money missed because a miss leaves the problem on the pile while this one tells the merchant the money arrived and only one of those is a lie
    money_wrongly_cleared = sum(
        amount_by_payment.get(label.entity_id, 0)
        for label in missing
        if label.entity_id in claimed_payments
    )
    # Money missed reads one kind of the eight, so it is silent on every class the run really does miss and the detection table saying 13 of 13 
    # outages went unnamed sits beside it saying nothing was missed. These two count the rest of them
    unnamed_problems = sum(1 for pair in real_pairs if pair not in noticed_pairs)
    # Value is summed over entities and not over labels, because one settlement carries four labels and adding its amount once per label reports four times 
    # the money there is. Only an entity noticed under no kind at all counts, since an outage payment reported as a missing settlement is money the 
    # merchant was told about under a name that is also true
    amount_by_entity = amount_by_payment | amount_by_settlement
    unexplained_entities = {
        label.entity_id
        for label in dataset.labels
        if label.kind in PROBLEMS and label.entity_id not in noticed_entities
    }
    money_unexplained = sum(amount_by_entity.get(entity, 0) for entity in unexplained_entities)

    # What each class costs, in rupees rather than as a count or a match rate. The largest exposure in a run is the credit nobody could attribute 
    # and reporting it only as a percentage of value matched is the one number a merchant cannot act on
    claimed_lines = {claim.line_index for claim in outcome.matched}
    money_unattributed_credit = sum(
        line.credit for index, line in enumerate(dataset.bank_lines) if index not in claimed_lines
    )
    # A second capture on one order is money owed back to a customer, so it is a liability the merchant carries and never money that failed to arrive
    money_owed_back = sum(
        amount_by_payment.get(item.entity_id, 0)
        for item in outcome.flagged
        if item.kind == "DUPLICATE_PAYMENT"
    )
    # Read off the settlements and the statement, both of which the matcher saw, so the figure is what the run concluded and not what the answer key knows
    payment_settlement = {p.payment_id: p.settlement_id for p in dataset.payments}
    money_bank_charges = 0
    for claim in outcome.matched:
        if not claim.payment_ids:
            continue
        settlement_id = payment_settlement.get(claim.payment_ids[0])
        if settlement_id is None:
            continue
        gap = amount_by_settlement.get(settlement_id, 0) - credit_by_line.get(claim.line_index, 0)
        if gap > 0:
            money_bank_charges += gap
    money_at_risk_reported = sum(
        amount_by_payment.get(item.entity_id, 0)
        for item in outcome.flagged
        if item.kind == "MISSING_SETTLEMENT"
    )
    money_in_flight = sum(
        amount_by_payment.get(payment_id, 0) for payment_id in outcome.in_flight
    )
    money_captured = sum(
        payment.amount for payment in dataset.payments if payment.status == "captured"
    )
    money_received = sum(amount_by_payment.get(payment_id, 0) for payment_id in outcome.received)
    money_unconfirmed = sum(
        amount_by_payment.get(payment_id, 0) for payment_id in outcome.unconfirmed
    )
    # Keyed by the merchant's own number, because an unlinked order has no payment id to look up and order_ref is the only identifier it still carries
    amount_by_order = {order.order_ref: order.amount for order in dataset.orders}
    money_unlinked = sum(
        amount_by_order.get(item.entity_id, 0)
        for item in outcome.flagged
        if item.kind == "UNLINKED_ORDER"
    )
    # The fee and not the payment amount, because the payment reconciled and is not in doubt. What has nothing to check it against is the charge taken off it
    # and reporting the payment would state a figure far larger than the money actually in question
    fee_by_payment = {payment.payment_id: payment.fee for payment in dataset.payments}
    money_fee_unverified = sum(
        fee_by_payment.get(item.entity_id, 0)
        for item in outcome.observed
        if item.kind == "NETWORK_UNKNOWN"
    )

    by_class = tuple(
        _class_result(kind, dataset.labels, noticed_pairs)
        for kind in REPORTED_KINDS
    )

    # Money the agent explained, never money the agent cleared. agent_resolved can only ever contain entities group_findings already grouped from outcome.
    # flagged, so this counts a subset of what was already flagged and moves nothing out of at risk or into received
    # Keyed on the pair the way precision and _class_result already are, so a payment flagged under two kinds is credited only for the one the 
    # agent was actually asked about
    agent_explained = [
        item for item in outcome.flagged if (item.entity_id, item.kind) in agent_resolved
    ]
    flagged_agent_explained = len(agent_explained)
    # Two different claims, kept apart because summing them reads as money recovered and is not. A MISSING_SETTLEMENT verdict decides what the merchant does 
    # with the payment itself, wait for a gateway to recover or escalate money that is never coming. Every other kind disputes a charge against a payment 
    # that arrived, where the amount in question is the fee and the payment value only says how much turnover the finding sits behind
    money_agent_reclassified = sum(
        amount_by_payment.get(item.entity_id, 0)
        for item in agent_explained
        if item.kind == "MISSING_SETTLEMENT"
    )
    value_behind_agent_findings = sum(
        amount_by_payment.get(item.entity_id, amount_by_settlement.get(item.entity_id, 0))
        for item in agent_explained
    )
    # Checked against the planted label, not just counted, because the adjudicator verifies the citation's arithmetic and never whether GATEWAY_OUTAGE 
    # was the label actually planted
    gateway_outage_true = {label.entity_id for label in dataset.labels if label.kind == "GATEWAY_OUTAGE"}
    gateway_outage_agent_verified = len(agent_outage & gateway_outage_true)

    return Report(
        credits_total=len(dataset.bank_lines),
        credits_claimed=len(outcome.matched),
        credits_matched=credits_matched,
        credit_value_total=sum(line.credit for line in dataset.bank_lines),
        credit_value_matched=credit_value_matched,
        money_captured=money_captured,
        money_received=money_received,
        money_unconfirmed=money_unconfirmed,
        money_at_risk_reported=money_at_risk_reported,
        money_at_risk_actual=money_at_risk_actual,
        money_missed=money_missed,
        money_wrongly_cleared=money_wrongly_cleared,
        unnamed_problems=unnamed_problems,
        money_unexplained=money_unexplained,
        money_unattributed_credit=money_unattributed_credit,
        money_owed_back=money_owed_back,
        money_bank_charges=money_bank_charges,
        money_fee_overcharged=outcome.fee_overcharged,
        money_in_flight=money_in_flight,
        money_unlinked=money_unlinked,
        money_fee_unverified=money_fee_unverified,
        flagged_total=len(outcome.flagged),
        flagged_real=flagged_real,
        in_flight_wrongly_flagged=in_flight_wrongly_flagged,
        records=len(dataset.payments) + len(dataset.bank_lines),
        seconds=seconds,
        by_class=by_class,
        flagged_agent_explained=flagged_agent_explained,
        money_agent_reclassified=money_agent_reclassified,
        value_behind_agent_findings=value_behind_agent_findings,
        gateway_outage_agent_verified=gateway_outage_agent_verified,
    )


# Takes the outcome and the payments and never the dataset, so a merchant's own run is checked the same way this one is
def check_books(outcome: MatchOutcome, payments: Sequence[Payment]) -> None:
    captured = {payment.payment_id for payment in payments if payment.status == "captured"}
    at_risk = tuple(item.entity_id for item in outcome.flagged if item.kind == "MISSING_SETTLEMENT")
    fates = (outcome.received, outcome.in_flight, at_risk, outcome.unconfirmed)

    # Counted off the lists and not off sets of them, the money totals are summed from the lists so a payment named twice in one fate doubles its amount 
    # and a set would hide that
    together = set().union(*(set(fate) for fate in fates))
    counted = sum(len(fate) for fate in fates)
    if counted != len(together):
        raise ValueError(f"{counted - len(together)} captured payments are counted more than once")

    if together != captured:
        raise ValueError(
            f"{len(captured - together)} captured payments are in no fate, "
            f"{len(together - captured)} are in a fate without being captured"
        )


def _class_result(
    kind: LabelKind,
    labels: tuple[Label, ...],
    noticed_pairs: set[tuple[str, LabelKind]],
) -> ClassResult:
    entities = {label.entity_id for label in labels if label.kind == kind}
    # Counted on the pair, a settlement noticed under another kind is not a detection of this one
    detected = sum(1 for entity in entities if (entity, kind) in noticed_pairs)
    return ClassResult(kind=kind, expected=len(entities), detected=detected)
