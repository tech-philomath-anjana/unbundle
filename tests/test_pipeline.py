import dataclasses
import json
from types import SimpleNamespace
import pathlib
import types
from datetime import date, datetime

import pytest

from unbundle.diagnose import (
    _as_anthropic_response,
    _as_openai_messages,
    _as_openai_tools,
    CAUSES,
    Group,
    INNER_MAX_ROUNDS,
    Incident,
    MAX_TURNS,
    OUTAGE_MINIMUM,
    Proposal,
    _adjudicate,
    _look_up_payment,
    _look_up_payments,
    _look_up_settlement,
    _look_up_settlements,
    _propose_or_report,
    run_tool_loop,
    explain,
    group_findings,
)
from unbundle.diagnose import Explanation
import unbundle.diagnose
import unbundle.run
from unbundle.ground_truth import PROBLEMS, Label
from unbundle.evaluate import _class_result, check_books, evaluate
from unbundle.run import _agent_verified
from unbundle.load import RowError, load
from unbundle.record_types import Adjustment, BankLine, CardNetwork, Payment, Settlement
from unbundle.money import Paise, format_amount
from unbundle.synthetic import WINDOW_END, generate, write_csvs
from unbundle.reconcile import _drifts, expected_fee, match


def test_generate_is_deterministic_for_a_seed():
    assert generate(seed=20260828, order_count=400) == generate(seed=20260828, order_count=400)


def test_a_different_seed_gives_different_data():
    assert generate(seed=20260828, order_count=400) != generate(seed=20260829, order_count=400)


def test_every_settlement_balances_against_its_payments():
    dataset = generate(order_count=1_000)
    refunds: dict[str, int] = {}
    for adjustment in dataset.adjustments:
        if adjustment.settlement_id is not None:
            refunds[adjustment.settlement_id] = (
                refunds.get(adjustment.settlement_id, 0) + adjustment.amount
            )

    # A held back payment names the settlement and its money was not in the transfer, so it comes
    # off the expected amount here. The identity is not relaxed, the term is added
    held_back = {
        label.entity_id for label in dataset.labels if label.kind == "HELD_BACK"
    }
    for settlement in dataset.settlements:
        members = [p for p in dataset.payments if p.settlement_id == settlement.settlement_id]
        gross = sum(payment.amount for payment in members)
        fees = sum(payment.fee for payment in members)
        deducted = refunds.get(settlement.settlement_id, 0)
        withheld = sum(p.net for p in members if p.payment_id in held_back)
        # Fee carries GST already, so the tax is not subtracted a second time and the
        # settlement is gross minus fees minus refunds
        assert settlement.amount == gross - fees - deducted - withheld


# The wall between the matcher and the answer key is a claim, and a claim about a repo
# is only worth what a test makes of it
def test_matcher_never_imports_the_answer_key():
    source = pathlib.Path("src/unbundle/reconcile.py").read_text()
    assert "truth" not in source
    assert "label" not in source
    assert "credit_source" not in source


def test_matcher_cannot_be_handed_the_dataset():
    dataset = generate(order_count=200)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    # Everything the matcher produced has to be reachable from the data alone, so a
    # payment it never saw cannot appear in what it claims
    known = {payment.payment_id for payment in dataset.payments}
    for claim in outcome.matched:
        assert set(claim.payment_ids) <= known


def test_pipeline_completes_without_an_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dataset = generate(order_count=500)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    explanation = explain(outcome, dataset.payments, dataset.settlements, dataset.bank_lines)

    assert explanation.available is False
    assert explanation.model is None
    assert len(outcome.flagged) > 0


def test_a_confabulating_model_is_rejected_by_the_adjudicator(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dataset = generate(order_count=2_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )

    def always_an_outage(group, evidence):
        return Proposal(cause="GATEWAY_OUTAGE", cited=group.member_ids, gave_up=False, lookups=())

    explanation = explain(
        outcome,
        dataset.payments,
        dataset.settlements,
        dataset.bank_lines,
        propose=always_an_outage,
    )
    rejected = [incident for incident in explanation.incidents if not incident.accepted]
    # A model that calls everything an outage must have most of it thrown out, because
    # refusal here is the adjudicator's job and not the model's willingness to admit it
    assert len(rejected) > len(explanation.incidents) // 2


def test_every_captured_payment_ends_in_exactly_one_fate():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    check_books(outcome, dataset.payments)


# A payment can be flagged under two kinds and the agent is only ever asked about one of them,
# so crediting the entity rather than the pair hands the second kind a verdict nobody reached
# and sums its amount twice. Same shape as the co-label credit _class_result already guards
def test_the_agent_is_credited_for_the_kind_it_was_asked_about_and_not_the_entity():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    twice = next(
        entity
        for entity, kinds in _kinds_by_entity(outcome).items()
        if len(kinds) > 1 and "FEE_MISMATCH" in kinds
    )
    asked_about = "FEE_MISMATCH"
    other = next(kind for kind in _kinds_by_entity(outcome)[twice] if kind != asked_about)

    incident = Incident(
        cause="RATE_CARD_MISMATCH",
        shared="one payment",
        kind=asked_about,
        member_ids=(twice,),
        cited_ids=(twice,),
        accepted=True,
        reason="checked",
    )
    explanation = Explanation(
        incidents=(incident,), individual=(), trace=(), model="test", available=True
    )
    resolved, outage = _agent_verified(explanation)
    assert resolved == {(twice, asked_about)}
    assert (twice, other) not in resolved

    report = evaluate(dataset, outcome, seconds=1.0, agent_resolved=resolved, agent_outage=outage)
    assert report.flagged_agent_explained == 1

    # Only a MISSING_SETTLEMENT verdict decides what happens to the payment itself, so a fee
    # dispute contributes turnover and no reclassified money
    assert report.money_agent_reclassified == 0
    assert report.value_behind_agent_findings > 0


# One settlement carries four labels, so summing this figure per label reports four times the
# money there is, and an outage payment reported as a missing settlement is money the merchant
# was told about. Both traps are the reason the figure counts entities noticed under no kind
def test_unexplained_money_counts_a_record_once_and_skips_one_noticed_elsewhere():
    dataset = generate(order_count=5_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    report = evaluate(dataset, outcome, seconds=1.0)

    noticed = {item.entity_id for item in outcome.flagged + outcome.observed}
    amount = {payment.payment_id: payment.amount for payment in dataset.payments}
    amount.update({s.settlement_id: s.amount for s in dataset.settlements})
    unnoticed = [
        label for label in dataset.labels if label.kind in PROBLEMS and label.entity_id not in noticed
    ]

    once = sum(amount.get(entity, 0) for entity in {label.entity_id for label in unnoticed})
    assert report.money_unexplained == once

    # The same sum taken per label rather than per record, which is what a regression would give
    per_label = sum(amount.get(label.entity_id, 0) for label in unnoticed)
    assert per_label > once

    # Nothing the run noticed under any kind contributes, so a payment surfaced as a missing
    # settlement is not also counted as money nobody named
    assert all(label.entity_id not in noticed for label in unnoticed)
    assert report.unnamed_problems >= len(unnoticed)


# The unattributed figure is the largest number in a run and it is reported nowhere else in
# rupees, so it is pinned to a partition, every bank line is either claimed or unattributed
def test_every_bank_credit_is_either_claimed_or_reported_unattributed():
    dataset = generate(order_count=2_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    report = evaluate(dataset, outcome, seconds=1.0)

    claimed = {claim.line_index for claim in outcome.matched}
    claimed_value = sum(
        line.credit for index, line in enumerate(dataset.bank_lines) if index in claimed
    )
    assert claimed_value + report.money_unattributed_credit == report.credit_value_total

    # Signed against the agreed rate, so an undercharge nets off rather than being read as a loss
    charged = {item.entity_id for item in outcome.flagged if item.kind == "FEE_MISMATCH"}
    by_id = {payment.payment_id: payment for payment in dataset.payments}
    expected_total = sum(
        by_id[entity].fee - expected_fee(by_id[entity])
        for entity in charged
        if expected_fee(by_id[entity]) is not None
    )
    assert report.money_fee_overcharged == expected_total


# The one class in this generator the cascade cannot catch. The payment names a settlement that
# really did settle and whose credit really did arrive, so every step of the matcher passes and
# the merchant is told the money came. It is what makes the two safety figures falsifiable
def test_a_payment_held_back_from_its_settlement_is_reported_as_arrived():
    dataset = generate(order_count=5_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    held_back = [label.entity_id for label in dataset.labels if label.kind == "HELD_BACK"]
    assert held_back, "the seed drew none, the safety figures below would be vacuous"

    flagged = {item.entity_id for item in outcome.flagged}
    for entity in held_back:
        assert entity in outcome.received
        assert entity not in flagged

    # The transfer really is short by that payment, otherwise the label describes a withholding
    # that never happened and every assertion above passes on bookkeeping alone
    by_settlement = {s.settlement_id: s for s in dataset.settlements}
    by_payment = {p.payment_id: p for p in dataset.payments}
    for entity in held_back:
        payment = by_payment[entity]
        settlement = by_settlement[payment.settlement_id]
        members = [p for p in dataset.payments if p.settlement_id == settlement.settlement_id]
        deducted = sum(
            a.amount for a in dataset.adjustments if a.settlement_id == settlement.settlement_id
        )
        sent_in_full = sum(p.net for p in members) - deducted
        assert settlement.amount == sent_in_full - payment.net

    report = evaluate(dataset, outcome, seconds=1.0)
    amount = {payment.payment_id: payment.amount for payment in dataset.payments}
    # Reported at risk is short by exactly the money the run was told had arrived
    assert report.money_at_risk_actual - report.money_at_risk_reported == sum(
        amount[entity] for entity in held_back
    )
    assert report.money_wrongly_cleared > 0


def _kinds_by_entity(outcome):
    kinds: dict[str, set[str]] = {}
    for item in outcome.flagged:
        kinds.setdefault(item.entity_id, set()).add(item.kind)
    return kinds


# The check above is worth only what a broken outcome proves, so a payment is taken out of
# its bucket and the books have to notice
def test_a_payment_in_no_fate_fails_the_books():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    dropped = dataclasses.replace(outcome, received=outcome.received[1:])
    with pytest.raises(ValueError):
        check_books(dropped, dataset.payments)


def test_a_payment_in_two_fates_fails_the_books():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    doubled = dataclasses.replace(outcome, in_flight=outcome.in_flight + outcome.received[:1])
    with pytest.raises(ValueError):
        check_books(doubled, dataset.payments)


# A payment named twice in one bucket is deduplicated by a set but not by the money totals,
# which sum the list, so the count has to come off the list too
def test_a_payment_named_twice_in_one_fate_fails_the_books():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    twice = dataclasses.replace(outcome, received=outcome.received + outcome.received[:1])
    with pytest.raises(ValueError):
        check_books(twice, dataset.payments)


def test_a_kind_noticed_under_another_name_is_not_detected():
    labels = (Label("ROUNDING_DRIFT", "setl_00001", ""),)
    noticed = {("setl_00001", "BATCHED")}
    assert _class_result("ROUNDING_DRIFT", labels, noticed).detected == 0
    assert _class_result("BATCHED", (Label("BATCHED", "setl_00001", ""),), noticed).detected == 1


# The pipeline calls the check too, this one holds the guarantee for anyone who scores an
# outcome without going through the pipeline
def test_scoring_an_unbalanced_outcome_is_refused():
    dataset = generate(order_count=1_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    dropped = dataclasses.replace(outcome, received=outcome.received[1:])
    with pytest.raises(ValueError):
        evaluate(dataset, dropped, seconds=1.0)


# report.md is the artifact a reader acts on, and an incident is stamped with the whole group
# while cited_ids holds only what the model checked. The adjudicator never asks for coverage, so
# the header has to say how much of the group the evidence actually reaches
def test_the_report_never_claims_more_findings_than_the_model_cited(tmp_path, monkeypatch):
    dataset = generate(order_count=500)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    report = evaluate(dataset, outcome, seconds=1.0)

    part_checked = Incident(
        cause="RATE_CARD_MISMATCH",
        shared="upi payments",
        member_ids=tuple(f"pay_{index:06d}" for index in range(20)),
        cited_ids=("pay_000000", "pay_000001", "pay_000002"),
        accepted=True,
        reason="3 payments charged off the agreed rate",
    )
    all_checked = dataclasses.replace(
        part_checked,
        shared="card payments",
        member_ids=("pay_000100", "pay_000101"),
        cited_ids=("pay_000100", "pay_000101"),
    )
    explanation = Explanation(
        incidents=(part_checked, all_checked),
        individual=(),
        trace=(),
        model="scripted",
        available=True,
    )

    monkeypatch.setattr(unbundle.run, "RESULTS", tmp_path)
    unbundle.run._write_report(
        report, explanation, dataset.payments, seed=1, order_count=500, as_of=WINDOW_END
    )
    written = (tmp_path / "report.md").read_text()

    assert "RATE_CARD_MISMATCH  (3 of 20 findings)" in written
    assert "The other 17 in this group were not individually checked." in written
    # A fully cited incident reads plainly, so the qualifier marks the exception and is not noise
    assert "RATE_CARD_MISMATCH  (2 findings)" in written
    assert "(20 findings)" not in written


def _build_card_payment(payment_id: str, card_network: CardNetwork, amount: Paise) -> Payment:
    return Payment(
        payment_id=payment_id,
        order_id=f"order_{payment_id}",
        order_receipt=None,
        happened_at=datetime(2026, 8, 20, 10, 0),
        status="captured",
        method="card",
        card_network=card_network,
        card_type="credit",
        amount=amount,
        fee=0,
        tax=0,
        settlement_id="setl_00001",
    )


# The answer key groups a settlement by the rate each payment was priced at, and a card the
# export calls unknown was priced at some real rate, so the unpriceable payment can be a
# member of the very group being checked and leaving it out compares a group nothing labelled
def test_a_drift_is_not_claimed_on_a_settlement_holding_an_unpriceable_payment():
    drifting = (
        _build_card_payment("pay_000001", "Visa", 10001),
        _build_card_payment("pay_000002", "Visa", 10024),
    )
    # The pair on its own really does drift, so the abstention below is what suppresses the
    # claim and not an absence of drift to find
    assert _drifts(drifting) is True

    unpriceable = _build_card_payment("pay_000003", "unknown", 50000)
    assert _drifts(drifting + (unpriceable,)) is False


# Naming a refund means claiming the rest of the settlement is accounted for, so a settlement
# that does not tie out against its own payments gets no adjustment named at all
def test_an_adjustment_is_not_named_on_a_settlement_that_does_not_tie_out():
    dataset = generate(order_count=2_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    named = {
        flag.entity_id
        for flag in outcome.observed
        if flag.kind in ("PARTIAL_REFUND", "CHARGEBACK_LATER")
    }
    assert named, "no settlement carried an adjustment, so the gate below proves nothing"

    target = sorted(named)[0]
    # One paise is enough, the tie-out is exact because both sides are Razorpay's own numbers
    broken = [
        dataclasses.replace(settlement, amount=settlement.amount + 1)
        if settlement.settlement_id == target
        else settlement
        for settlement in dataset.settlements
    ]
    after = match(
        dataset.orders,
        dataset.payments,
        broken,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    still_named = {
        flag.entity_id
        for flag in after.observed
        if flag.kind in ("PARTIAL_REFUND", "CHARGEBACK_LATER")
    }
    assert target not in still_named


def _build_member(payment_id: str, amount: Paise) -> Payment:
    return Payment(
        payment_id=payment_id,
        order_id=f"order_{payment_id}",
        order_receipt=None,
        happened_at=datetime(2026, 8, 20, 10, 0),
        status="captured",
        method="upi",
        card_network=None,
        card_type=None,
        amount=amount,
        fee=0,
        tax=0,
        settlement_id="setl_00001",
    )


# The refund is raised against a payment in an earlier cycle, which is what makes it the
# PARTIAL_REFUND shape and not an ordinary refund taken against this settlement's own members
def _build_late_refund(amount: Paise) -> Adjustment:
    return Adjustment(
        adjustment_id="adj_00001",
        kind="refund",
        payment_id="pay_earlier",
        amount=amount,
        raised_at=datetime(2026, 8, 1, 10, 0),
        settlement_id="setl_00001",
    )


def _adjustments_named_on(outcome, settlement_id: str) -> set[str]:
    return {
        flag.kind
        for flag in outcome.observed
        if flag.entity_id == settlement_id and flag.kind in ("PARTIAL_REFUND", "CHARGEBACK_LATER")
    }


# A settlement can leave one of its payments out of the transfer, so the tie-out is short by
# exactly that payment's net and an exact comparison refuses the settlement outright, which
# throws away the refund sitting on it. The refund has nothing to do with the withholding and
# is still the only record of money the merchant did not get back
def test_a_refund_is_still_named_on_a_settlement_that_held_a_payment_back():
    members = (
        _build_member("pay_00001", 500000),
        _build_member("pay_00002", 300000),
        _build_member("pay_00003", 200000),
    )
    refund = _build_late_refund(50000)
    held_back = members[1]
    sent_in_full = sum(payment.net for payment in members) - refund.amount
    settlement = _build_settlement("setl_00001", sent_in_full - held_back.net, 22)

    # No other member nets what the transfer is short by, so the withheld payment is the only
    # answer and the settlement ties out once it is taken off
    assert [payment.payment_id for payment in members if payment.net == held_back.net] == [
        held_back.payment_id
    ]

    outcome = match((), members, (settlement,), (refund,), (), as_of=date(2026, 8, 28))

    assert _adjustments_named_on(outcome, "setl_00001") == {"PARTIAL_REFUND"}


# Naming a refund means claiming the rest of the settlement is accounted for, and with two
# members netting what the transfer is short by there are two accounts of where it went, so
# the gap goes back to being unexplained rather than pinned on whichever one is found first
def test_a_refund_is_not_named_when_two_payments_could_be_the_one_held_back():
    members = (
        _build_member("pay_00001", 300000),
        _build_member("pay_00002", 300000),
        _build_member("pay_00003", 400000),
    )
    refund = _build_late_refund(50000)
    shortfall = members[0].net
    sent_in_full = sum(payment.net for payment in members) - refund.amount
    settlement = _build_settlement("setl_00001", sent_in_full - shortfall, 22)

    tied = [payment.payment_id for payment in members if payment.net == shortfall]
    assert len(tied) == 2, "only one member fits the gap, so this is the case above again"

    outcome = match((), members, (settlement,), (refund,), (), as_of=date(2026, 8, 28))

    assert _adjustments_named_on(outcome, "setl_00001") == set()


def _build_settlement(settlement_id: str, amount: Paise, day: int) -> Settlement:
    return Settlement(
        settlement_id=settlement_id,
        utr=f"AXISN2608{day:02d}00001",
        settled_at=datetime(2026, 8, day, 11, 0),
        amount=amount,
        fees=0,
        tax=0,
        status="processed",
    )


def _build_bank_line(narration: str, credit: Paise, day: int) -> BankLine:
    return BankLine(txn_date=date(2026, 8, day), narration=narration, credit=credit, debit=0)


# The default seed draws no failed settlement at all, 57 settlements all read processed, so
# this path has never run against real data. A credit whose UTR and amount match exactly is
# the strongest case, the shape a real transfer would have if it had actually gone through,
# and it is still not evidence the money arrived
def test_a_failed_settlement_is_never_received_even_when_its_credit_would_match():
    settlement = dataclasses.replace(_build_settlement("setl_00001", 500000, 10), status="failed")
    payment = Payment(
        payment_id="pay_00001",
        order_id="order_pay_00001",
        order_receipt=None,
        happened_at=datetime(2026, 8, 8, 10, 0),
        status="captured",
        method="upi",
        card_network=None,
        card_type=None,
        amount=500000,
        fee=0,
        tax=0,
        settlement_id="setl_00001",
    )
    line = _build_bank_line(f"NEFT CR {settlement.utr}", 500000, 10)

    outcome = match((), (payment,), (settlement,), (), (line,), as_of=date(2026, 8, 28))

    assert payment.payment_id not in outcome.received
    assert payment.payment_id not in outcome.unconfirmed
    assert payment.payment_id not in outcome.in_flight
    assert any(
        item.entity_id == payment.payment_id and item.kind == "MISSING_SETTLEMENT"
        for item in outcome.flagged
    )


# BANK_FEE_DEDUCTED and MANGLED_UTR groups name settlement ids, and _render has no detail
# for a settlement id, so this lookup is the only evidence the model ever gets for either kind
def test_look_up_settlement_reports_the_shortfall_against_its_own_claimed_credit():
    settlement = _build_settlement("setl_00001", 500000, 10)
    line = _build_bank_line("NEFT CR RAZORPAY", 495000, 10)
    result = _look_up_settlement("setl_00001", {"setl_00001": settlement}, {"setl_00001": line})
    assert result["amount"] == "5000.00"
    assert result["claimed_credit"] == "4950.00"
    assert result["shortfall"] == "50.00"


def test_look_up_settlement_on_an_unclaimed_settlement_reports_no_credit():
    settlement = _build_settlement("setl_00001", 500000, 10)
    result = _look_up_settlement("setl_00001", {"setl_00001": settlement}, {})
    assert result["claimed_credit"] is None
    assert "shortfall" not in result


# A settlement id the model made up is not a crash, it is a fact the model can act on
def test_look_up_settlement_on_an_unknown_id_returns_an_error_not_a_crash():
    result = _look_up_settlement("setl_nope", {}, {})
    assert "error" in result


# The charged fee is already in _render's evidence, the agreed one is not, so this lookup is
# what makes a RATE_CARD_MISMATCH claim checkable rather than asserted
def test_look_up_payment_reports_the_expected_fee_beside_the_charged_one():
    payment = _build_card_payment("pay_000001", "Visa", 100000)
    overcharged = dataclasses.replace(payment, fee=3000)
    result = _look_up_payment("pay_000001", {"pay_000001": overcharged})
    assert result["fee"] == "30.00"
    assert result["expected_fee"] == format_amount(expected_fee(payment))
    assert result["fee"] != result["expected_fee"]


def test_look_up_payment_on_an_unpriceable_network_reports_no_expected_fee():
    payment = _build_card_payment("pay_000001", "unknown", 100000)
    result = _look_up_payment("pay_000001", {"pay_000001": payment})
    assert result["expected_fee"] is None


def test_look_up_payment_on_an_unknown_id_returns_an_error_not_a_crash():
    result = _look_up_payment("pay_nope", {})
    assert "error" in result


# The plural lookups are the fix for a group that INNER_MAX_ROUNDS could not otherwise cover, so
# each result has to line up with what the singular lookup would have returned for the same id
def test_look_up_payments_batches_the_singular_lookup():
    payment = _build_card_payment("pay_000001", "Visa", 100000)
    other = _build_card_payment("pay_000002", "unknown", 100000)
    by_id = {"pay_000001": payment, "pay_000002": other}
    result = _look_up_payments(["pay_000001", "pay_000002", "pay_nope"], by_id)
    assert [entry["payment_id"] for entry in result["payments"][:2]] == ["pay_000001", "pay_000002"]
    assert result["payments"][2] == {"error": "no payment pay_nope"}


def test_look_up_settlements_batches_the_singular_lookup():
    settlement = _build_settlement("setl_00001", 500000, 10)
    line = _build_bank_line("NEFT CR RAZORPAY", 495000, 10)
    result = _look_up_settlements(
        ["setl_00001", "setl_nope"], {"setl_00001": settlement}, {"setl_00001": line}
    )
    assert result["settlements"][0]["shortfall"] == "50.00"
    assert result["settlements"][1] == {"error": "no settlement setl_nope"}


def _tool_use_response(name: str, tool_input: dict, call_id: str = "call_1"):
    block = types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=call_id)
    return types.SimpleNamespace(content=[block])


# Proves the loop is a loop and not a re-roll, a lookup result is fed back and the next
# decision is made against it rather than against the same fixed evidence again
def test_the_tool_loop_looks_up_before_it_resolves():
    message_lengths = []
    responses = [
        _tool_use_response("look_up_settlement", {"settlement_id": "setl_00001"}),
        _tool_use_response("resolve", {"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_00001"]}),
    ]

    def call_model(messages):
        message_lengths.append(len(messages))
        return responses.pop(0)

    lookups = {"look_up_settlement": lambda args: {"looked_up": args["settlement_id"]}}
    result = run_tool_loop(call_model, [{"role": "user", "content": "go"}], lookups)

    assert (result.cause, result.cited) == ("BANK_TRANSFER_CHARGE", ("setl_00001",))
    assert result.gave_up is False
    assert result.lookups == ("look_up_settlement",)
    # The second call carries the assistant's lookup and its result, not a fresh copy of the first
    assert message_lengths == [1, 3]


# Autonomy over order, not just over the single answer, proved by letting the model choose
# two different tools in a row before it decides
def test_the_tool_loop_can_look_up_more_than_one_thing_before_deciding():
    looked_up = []
    responses = [
        _tool_use_response("look_up_settlement", {"settlement_id": "setl_00001"}),
        _tool_use_response("look_up_payment", {"payment_id": "pay_000001"}),
        _tool_use_response("resolve", {"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_00001"]}),
    ]
    lookups = {
        "look_up_settlement": lambda args: looked_up.append(("settlement", args["settlement_id"])) or {},
        "look_up_payment": lambda args: looked_up.append(("payment", args["payment_id"])) or {},
    }

    result = run_tool_loop(
        lambda messages: responses.pop(0), [{"role": "user", "content": "go"}], lookups
    )

    assert (result.cause, result.cited) == ("BANK_TRANSFER_CHARGE", ("setl_00001",))
    assert looked_up == [("settlement", "setl_00001"), ("payment", "pay_000001")]
    assert result.lookups == ("look_up_settlement", "look_up_payment")


# The stopping rule itself: a model that decides nothing fits has to be distinguishable from
# one that returned nothing at all, or the decision to stop is invisible
def test_give_up_ends_the_loop_with_no_proposal():
    responses = [_tool_use_response("give_up", {})]
    result = run_tool_loop(
        lambda messages: responses.pop(0), [{"role": "user", "content": "go"}], {}
    )
    assert result.cause is None
    assert result.gave_up is True


# The false claim, paired with give_up above: a model that never resolves or gives up is not
# the same event, and it must not spin forever either
def test_the_loop_stops_after_the_round_cap_instead_of_spinning_forever():
    call_count = 0

    def never_resolves(messages):
        nonlocal call_count
        call_count += 1
        return _tool_use_response("look_up_settlement", {"settlement_id": "setl_00001"})

    result = run_tool_loop(
        never_resolves, [{"role": "user", "content": "go"}], {"look_up_settlement": lambda args: {}}
    )
    assert result.cause is None
    assert result.gave_up is False
    assert call_count == INNER_MAX_ROUNDS


# The third way a proposal comes back with no cause, beside give_up and the round cap above:
# the request never reached the model at all. A rate limit, a rejected key and a wrong model
# name are three different jobs and the type and message are the only thing telling them apart
def test_a_request_failure_reports_what_broke():
    def refuses(messages):
        raise RuntimeError("401 invalid api key")

    result = _propose_or_report(refuses, [{"role": "user", "content": "go"}], {})

    assert result.cause is None
    assert result.gave_up is False
    assert result.error == "RuntimeError: 401 invalid api key"


# The true claim paired with the guard above: a model that answered and decided nothing fits
# has no failure to report, so the run keeps the wording that fits rather than naming an error
def test_a_model_that_gave_up_reports_no_failure():
    responses = [_tool_use_response("give_up", {})]

    result = _propose_or_report(
        lambda messages: responses.pop(0), [{"role": "user", "content": "go"}], {}
    )

    assert result.gave_up is True
    assert result.error is None


def _rate_limited(seconds: str = "2.1525"):
    return RuntimeError(
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
        "`openai/gpt-oss-120b` in organization `org_abc123` service tier `on_demand` on "
        f"tokens per minute (TPM): Limit 8000, Used 6636, Requested 1651. Please try again in {seconds}s.', "
        "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )


# A rate limit was left out of the retry entirely, on the reasoning that it answers the same
# way however often it is asked, and a full scale run then lost most of its groups to one. The
# ceiling is tokens per minute and it clears in seconds, so it is a queue and not a refusal
def test_a_rate_limit_is_waited_out_and_the_proposal_still_lands(monkeypatch):
    slept = []
    monkeypatch.setattr(unbundle.diagnose.time, "sleep", slept.append)
    responses = [
        _rate_limited(),
        _tool_use_response("resolve", {"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_00001"]}),
    ]

    def call_model(messages):
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    result = _propose_or_report(call_model, [{"role": "user", "content": "go"}], {})

    assert (result.cause, result.error) == ("BANK_TRANSFER_CHARGE", None)
    # The provider names the window it will clear in, so the wait comes from the message and
    # is not a constant of ours that has to be guessed high enough to work
    assert slept == [pytest.approx(2.2525)]


# The wait cannot be unbounded, a key whose daily cap is gone answers 429 forever and would
# hang the run rather than report it
def test_a_rate_limit_that_never_clears_is_reported_not_waited_on_forever(monkeypatch):
    monkeypatch.setattr(unbundle.diagnose.time, "sleep", lambda seconds: None)

    def call_model(messages):
        raise _rate_limited()

    result = _propose_or_report(call_model, [{"role": "user", "content": "go"}], {})

    assert result.cause is None
    assert "rate_limit_exceeded" in result.error
    # The org id is in every one of these bodies and results/ goes to a public repo
    assert "org_abc123" not in result.error


def _daily_limit(wait: str = "4m59.808s"):
    return RuntimeError(
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
        "`openai/gpt-oss-120b` in organization `org_abc123` service tier `on_demand` on "
        f"tokens per day (TPD): Limit 200000, Used 199481, Requested 1651. Please try again in {wait}.', "
        "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )


# Both ceilings arrive as a 429 and they are opposite problems. Tokens spent for the day do not
# come back by waiting, and the waits handed out run four to nine minutes and grow, so retrying
# costs hours to reach the same answer. Taken from a real run that hit the limit 218 times, 215
# of them this one
def test_a_daily_limit_is_reported_at_once_and_never_waited_out(monkeypatch):
    slept = []
    monkeypatch.setattr(unbundle.diagnose.time, "sleep", slept.append)
    calls = []

    def call_model(messages):
        calls.append(1)
        raise _daily_limit()

    result = _propose_or_report(call_model, [{"role": "user", "content": "go"}], {})

    assert "rate_limit_exceeded" in result.error
    assert slept == []
    assert len(calls) == 1


# The wait was read with a seconds-only pattern, which matched every per-minute message and none
# of the daily ones, so the code fell back to a backoff of its own that bore no relation to what
# the provider asked for. Every string here is copied from a captured run
def test_the_wait_is_read_from_minutes_and_seconds_as_well_as_seconds():
    assert unbundle.diagnose._retry_after("Please try again in 2.1525s.", 1) == pytest.approx(2.2525)
    assert unbundle.diagnose._retry_after("Please try again in 4m59.808s.", 1) == pytest.approx(60.0)
    assert unbundle.diagnose._retry_after("Please try again in 0m3.5s.", 1) == pytest.approx(3.6)
    # Nothing named at all is the only case the backoff of our own is for
    assert unbundle.diagnose._retry_after("Rate limit reached", 3) == pytest.approx(8.0)


# The narrowness is the point and is easy to lose while widening it, a rejected key answers
# the same way however long you wait and retrying it only delays the honest message
def test_a_rejected_key_is_not_waited_out(monkeypatch):
    slept = []
    monkeypatch.setattr(unbundle.diagnose.time, "sleep", slept.append)
    calls = []

    def call_model(messages):
        calls.append(1)
        raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")

    result = _propose_or_report(call_model, [{"role": "user", "content": "go"}], {})

    assert "Invalid API Key" in result.error
    assert slept == []
    assert len(calls) == 1


# The true claim paired with the guard above: the cap must not cut off a resolve that lands
# on the very last round it allows
def test_a_resolve_on_the_last_allowed_round_still_succeeds():
    responses = [
        _tool_use_response("look_up_settlement", {"settlement_id": "setl_00001"})
        for _ in range(INNER_MAX_ROUNDS - 1)
    ] + [_tool_use_response("resolve", {"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_00001"]})]

    result = run_tool_loop(
        lambda messages: responses.pop(0),
        [{"role": "user", "content": "go"}],
        {"look_up_settlement": lambda args: {}},
    )
    assert (result.cause, result.cited) == ("BANK_TRANSFER_CHARGE", ("setl_00001",))


# A tool name that was never offered is not one the client sent, an error goes back so a
# model that names the wrong tool gets a chance to recover instead of crashing the run. It is
# not counted as a lookup, nothing real ran
def test_a_hallucinated_tool_name_does_not_crash_the_loop():
    responses = [
        _tool_use_response("delete_everything", {}),
        _tool_use_response("resolve", {"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_00001"]}),
    ]
    result = run_tool_loop(
        lambda messages: responses.pop(0), [{"role": "user", "content": "go"}], {}
    )
    assert (result.cause, result.cited) == ("BANK_TRANSFER_CHARGE", ("setl_00001",))
    assert result.lookups == ()


# A transfer charge is a claim about one settlement's own credit, and a credit sitting a
# plausible amount short of some other settlement is not evidence about this one
def test_a_transfer_charge_is_proven_by_the_settlements_own_credit():
    settlement = _build_settlement("setl_00001", 500000, 10)
    group = Group(kind="BANK_FEE_DEDUCTED", shared="", member_ids=("setl_00001",))

    own_credit_short = {"setl_00001": _build_bank_line("NEFT CR RAZORPAY", 495000, 10)}
    accepted, _ = _adjudicate(
        "BANK_TRANSFER_CHARGE", ("setl_00001",), group, [], [settlement], own_credit_short
    )
    assert accepted is True

    # This settlement's own credit arrived in full, and another settlement's credit is short
    # by exactly a plausible charge, which is the coincidence the whole statement scan took
    another_settlements_is_short = {
        "setl_00001": _build_bank_line("NEFT CR RAZORPAY", 500000, 10),
        "setl_00002": _build_bank_line("NEFT CR RAZORPAY", 495000, 10),
    }
    accepted, reason = _adjudicate(
        "BANK_TRANSFER_CHARGE",
        ("setl_00001",),
        group,
        [],
        [settlement],
        another_settlements_is_short,
    )
    assert accepted is False
    assert "not short by a bank charge" in reason


def _build_missing_payment(payment_id: str, happened_at: datetime, method: str = "upi") -> Payment:
    return Payment(
        payment_id=payment_id,
        order_id=f"order_{payment_id}",
        order_receipt=None,
        happened_at=happened_at,
        status="captured",
        method=method,
        card_network=None,
        card_type=None,
        amount=10000,
        fee=0,
        tax=0,
        settlement_id=None,
    )


# GATEWAY_OUTAGE and SETTLEMENT_NEVER_SENT are two answers for the same MISSING_SETTLEMENT
# group, and the adjudicator used to accept either one on the same evidence, so a model that
# always said SETTLEMENT_NEVER_SENT was never told the outage bar was there to clear instead
def test_settlement_never_sent_is_refused_when_the_group_clears_the_outage_bar():
    outage_shaped = tuple(
        _build_missing_payment(f"pay_{i:05d}", datetime(2026, 8, 19, 14, i))
        for i in range(OUTAGE_MINIMUM)
    )
    cited = tuple(payment.payment_id for payment in outage_shaped)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    accepted, reason = _adjudicate("SETTLEMENT_NEVER_SENT", cited, group, outage_shaped, [], {})
    assert accepted is False
    assert "GATEWAY_OUTAGE" in reason

    # One fewer than the outage bar is coincidence, not an outage, so the cause actually cited
    # is still due
    too_few = outage_shaped[:-1]
    cited_too_few = tuple(payment.payment_id for payment in too_few)
    group_too_few = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited_too_few)
    accepted, _ = _adjudicate(
        "SETTLEMENT_NEVER_SENT", cited_too_few, group_too_few, too_few, [], {}
    )
    assert accepted is True


# A settlement that was created and then failed is money lost after the capture and not at the
# gateway, so it is a different cause with a different recovery. The grouping window is four
# hours and an outage is shorter, so the two arrive in one group and the shape check reads
# method, window and count and cannot tell them apart
def test_gateway_outage_is_refused_when_a_cited_payment_was_assigned_to_a_settlement():
    never_settled = tuple(
        _build_missing_payment(f"pay_{i:05d}", datetime(2026, 8, 19, 14, i))
        for i in range(OUTAGE_MINIMUM)
    )
    cited = tuple(payment.payment_id for payment in never_settled)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    # These on their own really are an outage, so the refusal below is the settled payments
    # doing the work and not a bar that was never going to be cleared
    accepted, _ = _adjudicate("GATEWAY_OUTAGE", cited, group, never_settled, [], {})
    assert accepted is True

    # Captured before the outage began and assigned to a settlement that later failed, which is
    # what the seed draws either side of the real window
    settled = tuple(
        dataclasses.replace(
            _build_missing_payment(f"pay_1{i:04d}", datetime(2026, 8, 19, 12, i)),
            settlement_id="setl_00001",
        )
        for i in range(3)
    )
    # Buried in the middle, so a check that reads only the first or the last cited payment
    # still has to find them
    members = never_settled[:2] + settled + never_settled[2:]
    cited_all = tuple(payment.payment_id for payment in members)
    group_all = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited_all)

    accepted, reason = _adjudicate("GATEWAY_OUTAGE", cited_all, group_all, members, [], {})
    assert accepted is False
    # The count is what says every cited payment was read, and naming them at all is what
    # separates this refusal from the shape check turning down a group that never looked like
    # an outage in the first place
    assert reason.startswith(f"{len(settled)} cited payments were assigned to a settlement")


# The cited ids are filtered against the payments, so a group of settlement ids leaves nothing
# to check and the claim would be granted on an empty set. The kind gate refuses a
# BANK_FEE_DEDUCTED or MANGLED_UTR group before this runs, so the empty set is only reachable
# now through a MISSING_SETTLEMENT group whose model cited settlements, and this is the second
# line rather than the first and it is what holds if that gate is ever widened
def test_settlement_never_sent_is_refused_when_nothing_cited_is_a_payment():
    cited = ("setl_00001", "setl_00002")
    group = Group(kind="MISSING_SETTLEMENT", shared="2 credits", member_ids=cited)
    accepted, reason = _adjudicate("SETTLEMENT_NEVER_SENT", cited, group, [], [], {})
    assert accepted is False
    assert reason == "cited no payments"

    # The gate itself, on a group of the kind it refuses, so the assertion above is still the
    # empty set doing the work and the gate is covered where it refuses first
    mangled = Group(kind="MANGLED_UTR", shared="2 credits", member_ids=cited)
    accepted, reason = _adjudicate("SETTLEMENT_NEVER_SENT", cited, mangled, [], [], {})
    assert accepted is False
    assert reason == "a settlement never sent does not explain a MANGLED_UTR finding"


def _build_payment_on(payment_id: str, minute: int, settlement_id: str | None) -> Payment:
    return dataclasses.replace(
        _build_missing_payment(payment_id, datetime(2026, 8, 19, 14, minute)),
        settlement_id=settlement_id,
    )


# The settlement was created, named in the export and then never transferred, so the payments
# inside it are flagged MISSING_SETTLEMENT and none of the four older causes can say why, the
# gateway did take them and they were assigned somewhere. The model diagnosed this on the live
# run by looking the settlement up and citing it, and had no cause to name for what it found
def test_a_group_on_a_failed_settlement_is_accepted_and_names_it():
    failed = dataclasses.replace(_build_settlement("setl_00001", 500000, 10), status="failed")
    members = tuple(_build_payment_on(f"pay_{i:05d}", i, "setl_00001") for i in range(4))
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    accepted, reason = _adjudicate("SETTLEMENT_FAILED", cited, group, members, [failed], {})
    assert accepted is True
    # Named rather than counted, because the settlement is what the merchant takes to the
    # gateway and this is the only cause of the five that can name one record for a whole group
    assert reason == "4 payments on setl_00001, which failed"


# A payment the gateway never assigned anywhere is SETTLEMENT_NEVER_SENT's story, so a group
# holding some of each is two stories and this claim has to lose it rather than sweep them
# together and report one cause for both
def test_a_never_assigned_payment_loses_the_failed_settlement_claim():
    failed = dataclasses.replace(_build_settlement("setl_00001", 500000, 10), status="failed")
    # Buried mid-tuple, so a check reading only the first or the last cited payment still has
    # to find it
    members = (
        _build_payment_on("pay_00000", 0, "setl_00001"),
        _build_payment_on("pay_09999", 9, None),
        _build_payment_on("pay_00001", 1, "setl_00001"),
    )
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    accepted, reason = _adjudicate("SETTLEMENT_FAILED", cited, group, members, [failed], {})
    assert accepted is False
    assert reason == "1 cited payments were never assigned to a settlement"


# The whole cause rests on the status and nothing else, so a settlement that processed normally
# has to lose it, otherwise every group with any settlement id in it takes this cause and the
# refusals it was built to fix come back as false answers instead
def test_a_settlement_that_did_not_fail_loses_the_claim():
    failed = dataclasses.replace(_build_settlement("setl_00001", 500000, 10), status="failed")
    processed = _build_settlement("setl_00002", 500000, 10)
    members = (
        _build_payment_on("pay_00000", 0, "setl_00001"),
        _build_payment_on("pay_09999", 9, "setl_00002"),
        _build_payment_on("pay_00001", 1, "setl_00001"),
    )
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    accepted, reason = _adjudicate(
        "SETTLEMENT_FAILED", cited, group, members, [failed, processed], {}
    )
    assert accepted is False
    assert reason == "1 cited payments name a settlement that did not fail"


# Found by running the cause over the real groups rather than by reading it, two FEE_MISMATCH
# groups hold payments that are also on a failed settlement and every fact check below passes
# on them. The fee was already wrong before the settlement was built, so accepting it there
# credits a fee finding with a verdict about the transfer and the report calls it explained
def test_a_failed_settlement_does_not_explain_a_fee_finding():
    failed = dataclasses.replace(_build_settlement("setl_00001", 500000, 10), status="failed")
    members = tuple(_build_payment_on(f"pay_{i:05d}", i, "setl_00001") for i in range(3))
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="FEE_MISMATCH", shared="card payments", member_ids=cited)

    accepted, reason = _adjudicate("SETTLEMENT_FAILED", cited, group, members, [failed], {})
    assert accepted is False
    assert reason == "a failed settlement does not explain a FEE_MISMATCH finding"

    # The identical citation on the finding the cause does answer, so the refusal above is the
    # group's kind doing the work and not evidence that was never going to stand
    missing = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)
    accepted, _ = _adjudicate("SETTLEMENT_FAILED", cited, missing, members, [failed], {})
    assert accepted is True


# The kind gate went on SETTLEMENT_FAILED alone and the same hole is open here, a payment
# flagged FEE_MISMATCH and MISSING_SETTLEMENT sits in a group of each and _look_up_payment hands
# the model the charged fee beside the agreed one, so a model looking at unsettled payments can
# answer the fee instead and the report prints a rate card dispute over captures that never
# settled, which sends the merchant to argue a fee rather than chase a transfer
def test_a_rate_card_dispute_does_not_explain_a_missing_settlement_finding():
    members = tuple(
        _build_missing_payment(f"pay_{i:05d}", datetime(2026, 8, 19, 14, i)) for i in range(3)
    )
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)

    accepted, reason = _adjudicate("RATE_CARD_MISMATCH", cited, group, members, [], {})
    assert accepted is False
    assert reason == "a rate card dispute does not explain a MISSING_SETTLEMENT finding"

    # The identical citation on the finding the cause does answer, so the refusal above is the
    # group's kind doing the work and not a fee that was never off the rate card
    fee = Group(kind="FEE_MISMATCH", shared="upi payments", member_ids=cited)
    accepted, _ = _adjudicate("RATE_CARD_MISMATCH", cited, fee, members, [], {})
    assert accepted is True


# A FEE_MISMATCH group holds payments that never settled too, so before the gate this cause read
# them as captured and never settled and handed the fee finding a verdict about the transfer.
# Found by running the cause over the real groups rather than by reading it, and it is reachable
# on the published seed and not only in a fixture
def test_a_settlement_never_sent_does_not_explain_a_fee_finding():
    members = tuple(
        _build_missing_payment(f"pay_{i:05d}", datetime(2026, 8, 19, 14, i)) for i in range(3)
    )
    cited = tuple(payment.payment_id for payment in members)
    group = Group(kind="FEE_MISMATCH", shared="upi payments", member_ids=cited)

    accepted, reason = _adjudicate("SETTLEMENT_NEVER_SENT", cited, group, members, [], {})
    assert accepted is False
    assert reason == "a settlement never sent does not explain a FEE_MISMATCH finding"

    missing = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)
    accepted, _ = _adjudicate("SETTLEMENT_NEVER_SENT", cited, missing, members, [], {})
    assert accepted is True


# Nothing cited resolves to a payment, so every check below passes over an empty list and the
# claim is granted on no evidence at all, which is the third time this cause list has had that
# hole. The kind gate stands in front of it now, since only MISSING_SETTLEMENT groups get this
# far and their members are all payments, so this is the second line rather than the first and
# it is what holds if that gate is ever widened
def test_the_failed_settlement_claim_is_refused_when_nothing_cited_is_a_payment():
    cited = ("setl_00001", "setl_00002")
    group = Group(kind="MISSING_SETTLEMENT", shared="", member_ids=cited)
    accepted, reason = _adjudicate("SETTLEMENT_FAILED", cited, group, [], [], {})
    assert accepted is False
    assert reason == "cited no payments"


# Every group that can reach this cause names settlements the cascade already claimed, so a
# check that reads only unclaimed credits refuses the true claim on every group there is
def test_the_right_cause_on_a_real_group_is_accepted():
    dataset = generate(order_count=5_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    fee_groups = [
        group
        for group in group_findings(outcome, dataset.payments)
        if group.kind == "BANK_FEE_DEDUCTED" and len(group.member_ids) > 1
    ]
    assert fee_groups, "no bank fee group in the run, so the acceptance below proves nothing"

    def cite_the_group(group, evidence):
        return Proposal(cause="BANK_TRANSFER_CHARGE", cited=group.member_ids, gave_up=False, lookups=())

    explanation = explain(
        outcome,
        dataset.payments,
        dataset.settlements,
        dataset.bank_lines,
        propose=cite_the_group,
    )
    charges = [
        incident for incident in explanation.incidents if incident.cause == "BANK_TRANSFER_CHARGE"
    ]
    assert charges and all(incident.accepted for incident in charges)


# turns is what separates a first time answer from one that only landed after the arithmetic
# pushed back, so a model that returns nothing usable late has to report the turn it reached
def test_a_model_that_returns_nothing_usable_late_reports_the_turn_it_reached(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dataset = generate(order_count=2_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )

    calls: dict[str, int] = {}

    def quiet_on_the_last_turn(group, evidence):
        calls[group.shared] = calls.get(group.shared, 0) + 1
        if calls[group.shared] < 3:
            # An unknown cause is refused outright, so turns one and two are spent for certain
            return Proposal(cause="NOT_A_CAUSE", cited=group.member_ids, gave_up=False, lookups=())
        return Proposal(cause=None, cited=(), gave_up=False, lookups=())

    explanation = explain(
        outcome,
        dataset.payments,
        dataset.settlements,
        dataset.bank_lines,
        propose=quiet_on_the_last_turn,
    )
    undiagnosed = [
        incident
        for incident in explanation.incidents
        if incident.reason == "model returned nothing usable"
    ]
    assert undiagnosed
    assert all(incident.turns == 3 for incident in undiagnosed)
    # None of these were a considered refusal, a model that returns nothing usable is not the
    # same event as one that decided nothing fits
    assert all(incident.cause != "GAVE_UP" for incident in undiagnosed)


# The core of the stopping-rule fix: two models that both end up empty-handed must not produce
# the same record, or the model's own decision to stop is invisible in the report
def test_giving_up_is_recorded_differently_from_returning_nothing_usable():
    dataset = generate(order_count=500)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    groups = [group for group in group_findings(outcome, dataset.payments) if len(group.member_ids) > 1]
    assert groups, "no group in this run, so neither path below proves anything"

    def gives_up(group, evidence):
        return Proposal(cause=None, cited=(), gave_up=True, lookups=("look_up_settlement",))

    def returns_nothing(group, evidence):
        return Proposal(cause=None, cited=(), gave_up=False, lookups=())

    quit_early = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines, propose=gives_up
    )
    empty_handed = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines, propose=returns_nothing
    )

    assert all(incident.cause == "GAVE_UP" for incident in quit_early.incidents)
    assert all(incident.accepted for incident in quit_early.incidents)
    assert all(incident.lookups == ("look_up_settlement",) for incident in quit_early.incidents)

    # NOT_ANSWERED rather than NOT_DIAGNOSED, because nothing was ever proposed for the
    # adjudicator to turn down, and every attempt is spent before the group is given up on
    assert all(incident.cause == "NOT_ANSWERED" for incident in empty_handed.incidents)
    assert all(not incident.accepted for incident in empty_handed.incidents)
    assert all(incident.turns == MAX_TURNS for incident in empty_handed.incidents)


# The retry. A malformed tool call is not the same answer twice, so a reply the loop cannot read
# costs a turn rather than the whole group, and the failure itself reaches the record
def test_an_unusable_reply_spends_a_turn_rather_than_the_group():
    dataset = generate(order_count=500)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    groups = [group for group in group_findings(outcome, dataset.payments) if len(group.member_ids) > 1]
    assert groups, "no group in this run, so this proves nothing"

    calls: dict[str, int] = {}

    def always_fails(group, evidence):
        calls[group.shared] = calls.get(group.shared, 0) + 1
        return Proposal(
            cause=None, cited=(), gave_up=False, lookups=(), error="RuntimeError: boom"
        )

    explanation = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines, propose=always_fails
    )

    assert all(count == MAX_TURNS for count in calls.values())
    assert all(incident.cause == "NOT_ANSWERED" for incident in explanation.incidents)
    assert all("RuntimeError: boom" in incident.reason for incident in explanation.incidents)


# Paired with the above: a group that reached a cause the arithmetic turned down is recorded as
# refused even when a later attempt broke, because the adjudicator did weigh something
def test_a_refusal_outranks_a_failure_on_a_later_turn():
    dataset = generate(order_count=500)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )

    calls: dict[str, int] = {}

    def refused_then_broken(group, evidence):
        calls[group.shared] = calls.get(group.shared, 0) + 1
        if calls[group.shared] == 1:
            return Proposal(cause="NOT_A_CAUSE", cited=group.member_ids, gave_up=False, lookups=())
        return Proposal(
            cause=None, cited=(), gave_up=False, lookups=(), error="RuntimeError: boom"
        )

    explanation = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines, propose=refused_then_broken
    )

    assert explanation.incidents
    assert all(incident.cause == "NOT_DIAGNOSED" for incident in explanation.incidents)


# NONE_OF_THESE is retired in favour of the give_up tool, a stray reference to it must be
# rejected the same as any other cause outside the closed set
def test_none_of_these_is_no_longer_a_recognised_cause():
    assert "NONE_OF_THESE" not in CAUSES
    group = Group(kind="BANK_FEE_DEDUCTED", shared="", member_ids=("setl_00001",))
    accepted, reason = _adjudicate("NONE_OF_THESE", ("setl_00001",), group, [], [], {})
    assert accepted is False
    assert "not one of the allowed causes" in reason


# Incident.lookups is a report of the whole proposal process for that group, not just the
# winning turn, so a group rejected twice before it finally lands has every turn's lookups on it
def test_lookups_accumulate_across_every_turn_not_just_the_last():
    dataset = generate(order_count=2_000)
    outcome = match(
        dataset.orders,
        dataset.payments,
        dataset.settlements,
        dataset.adjustments,
        dataset.bank_lines,
        as_of=WINDOW_END,
    )
    groups = [g for g in group_findings(outcome, dataset.payments) if len(g.member_ids) > 1]
    assert groups, "no group in this run, so the count below proves nothing"

    def always_wrong_after_a_lookup(group, evidence):
        return Proposal(cause="NOT_A_CAUSE", cited=group.member_ids, gave_up=False, lookups=("look_up_settlement",))

    explanation = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines,
        propose=always_wrong_after_a_lookup,
    )
    # Every group is rejected on all MAX_TURNS turns, one lookup each turn, so the incident
    # should carry all of them and not only the last turn's
    assert all(
        incident.lookups == ("look_up_settlement",) * MAX_TURNS for incident in explanation.incidents
    )


# clean and dirty are read off cited_ids, and a GAVE_UP incident cites nothing, so the empty
# set trivially passes any subset check and a refusal would score as a correct outage
def test_a_give_up_does_not_score_as_a_clean_outage():
    from unbundle.refusal import _split_by_correctness

    gave_up = Incident(
        cause="GAVE_UP", shared="", member_ids=("pay_1",), cited_ids=(), accepted=True,
        reason="the model looked and found no cause it could support", turns=1,
        lookups=("look_up_payment",),
    )
    resolved = Incident(
        cause="GATEWAY_OUTAGE", shared="", member_ids=("pay_2",), cited_ids=("pay_2",),
        accepted=True, reason="", turns=1, lookups=(),
    )

    clean, dirty, gave_up_incidents = _split_by_correctness([gave_up, resolved], {"pay_2"})

    assert gave_up not in clean
    assert gave_up not in dirty
    assert gave_up_incidents == [gave_up]
    assert resolved in clean


# The CSVs are the only boundary the pipeline crosses, and a figure in results describes
# the generated data only if every record type comes back the way it went out
def test_every_record_type_survives_a_write_and_a_read(tmp_path):
    dataset = generate(order_count=500)
    write_csvs(dataset, tmp_path)
    loaded = load(tmp_path)

    assert loaded.orders == dataset.orders
    assert loaded.payments == dataset.payments
    assert loaded.settlements == dataset.settlements
    assert loaded.bank_lines == dataset.bank_lines
    assert loaded.adjustments == dataset.adjustments


# csv.DictReader pads a short row to None and settlement_id None already means never
# settled, so a truncated export would report arrived money as at risk with nothing raising
def test_a_payment_row_short_a_column_is_refused_and_an_empty_cell_is_not(tmp_path):
    dataset = generate(order_count=200)
    write_csvs(dataset, tmp_path)

    # The empty cell is the half that has to keep loading, and real data holds both
    loaded = load(tmp_path)
    assert any(payment.settlement_id is None for payment in loaded.payments)
    assert any(payment.settlement_id is not None for payment in loaded.payments)

    payments = tmp_path / "payments.csv"
    lines = payments.read_text().splitlines()
    lines[1] = ",".join(lines[1].split(",")[:-1])
    payments.write_text("\n".join(lines) + "\n")

    with pytest.raises(RowError) as refused:
        load(tmp_path)
    assert "payments.csv row 2, no value for settlement_id" in str(refused.value)


def _groq_completion(name: str, arguments: str):
    call = SimpleNamespace(
        id=f"call_{name}", type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])


# Groq speaks the OpenAI shape and run_tool_loop speaks the Anthropic one, so the adapter
# is the only place that knows the difference and the loop stays untouched
def test_anthropic_messages_convert_to_the_openai_shape():
    block = SimpleNamespace(type="tool_use", name="look_up_settlement", input={"settlement_id": "setl_1"}, id="call_1")
    messages = [
        {"role": "user", "content": "the evidence"},
        {"role": "assistant", "content": [block]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "{}"}]},
    ]
    converted = _as_openai_messages(messages)

    assert converted[0] == {"role": "user", "content": "the evidence"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["tool_calls"][0]["function"]["name"] == "look_up_settlement"
    # arguments travels as a JSON string on the wire, not a dict
    assert json.loads(converted[1]["tool_calls"][0]["function"]["arguments"]) == {"settlement_id": "setl_1"}
    assert converted[2] == {"role": "tool", "tool_call_id": "call_1", "content": "{}"}


# The tool schemas are declared once in the Anthropic shape, so the adapter has to rename
# input_schema to parameters rather than the file carrying two copies of every tool
def test_tool_schemas_convert_to_the_openai_shape():
    converted = _as_openai_tools()
    assert all(tool["type"] == "function" for tool in converted)
    names = {tool["function"]["name"] for tool in converted}
    assert names == {
        "look_up_settlement", "look_up_payment", "look_up_settlements", "look_up_payments", "resolve", "give_up",
    }
    resolve = next(tool for tool in converted if tool["function"]["name"] == "resolve")
    assert "parameters" in resolve["function"]
    assert resolve["function"]["parameters"]["properties"]["cause"]["enum"] == list(CAUSES)


def test_a_groq_completion_converts_back_to_tool_use_blocks():
    response = _as_anthropic_response(
        _groq_completion("look_up_payment", '{"payment_id": "pay_1"}')
    )
    block = response.content[0]
    assert block.type == "tool_use"
    assert block.name == "look_up_payment"
    # arguments arrive as a string and the loop reads block.input as a dict
    assert block.input == {"payment_id": "pay_1"}


# A small model can emit arguments that are not valid JSON, and the lookup then answers
# with an error the model can read rather than the run stopping on a parse
def test_unparseable_arguments_do_not_raise():
    response = _as_anthropic_response(_groq_completion("look_up_settlement", "{not json"))
    assert response.content[0].input == {}


# The whole point of the adapter: run_tool_loop is not changed at all, and still drives a
# lookup then a resolve when the responses arrive in Groq's shape
def test_run_tool_loop_is_unchanged_and_drives_a_groq_shaped_model():
    completions = [
        _groq_completion("look_up_settlement", '{"settlement_id": "setl_1"}'),
        _groq_completion("resolve", '{"cause": "BANK_TRANSFER_CHARGE", "cited": ["setl_1"]}'),
    ]
    looked_up = []

    def call_model(messages):
        _as_openai_messages(messages)
        return _as_anthropic_response(completions.pop(0))

    lookups = {"look_up_settlement": lambda args: looked_up.append(args["settlement_id"]) or {"ok": True}}
    result = run_tool_loop(call_model, [{"role": "user", "content": "go"}], lookups)

    assert (result.cause, result.cited) == ("BANK_TRANSFER_CHARGE", ("setl_1",))
    assert result.lookups == ("look_up_settlement",)
    assert looked_up == ["setl_1"]
