import json
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from unbundle.diagnose import MAX_TURNS, Explanation, explain
from unbundle.evaluate import MatchOutcome, Report, check_books, evaluate
from unbundle.synthetic import DEFAULT_SEED, WINDOW_END, generate, write_csvs
from unbundle.load import load
from unbundle.reconcile import match
from unbundle.record_types import Payment
from unbundle.money import Paise, format_amount

# Grouping is added here and not in format_amount, because it is presentation and it is locale specific, so a lakh reads 1,00,000 and not 100,000
def rupees(amount: Paise) -> str:
    whole, _, paise = format_amount(amount).partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"Rs {whole}.{paise}"


DATA = Path("data")
RESULTS = Path("results")


# The adjudicator already checked the arithmetic, so this only sorts what it accepted and checks nothing again, and a citation carries the kind its group was 
# built from because a payment flagged under two kinds would otherwise hand the second one a verdict nobody reached
def _agent_verified(explanation: Explanation) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
    accepted = [incident for incident in explanation.incidents if incident.accepted]
    resolved = frozenset(
        (entity, incident.kind) for incident in accepted for entity in incident.cited_ids
    )
    outage = frozenset(
        entity
        for incident in accepted
        if incident.cause == "GATEWAY_OUTAGE"
        for entity in incident.cited_ids
    )
    return resolved, outage


# The agent stage is what this number costs and not the matcher, and a bigger count makes more groups than a free tier's daily
# token ceiling allows, so the published run would be one nobody cloning this could reproduce
def run(seed: int = DEFAULT_SEED, order_count: int = 3_000, as_of: date = WINDOW_END) -> None:
    dataset = generate(seed=seed, order_count=order_count)
    write_csvs(dataset, DATA)

    # Timed from reading the files, not from the records already being in memory, because a merchant's run starts with five CSVs on disk and parsing them is 
    # most of the cost
    started = time.perf_counter()
    data = load(DATA)
    outcome = match(
        data.orders,
        data.payments,
        data.settlements,
        data.adjustments,
        data.bank_lines,
        as_of=as_of,
    )
    elapsed = time.perf_counter() - started

    # Raises rather than reports, a run whose buckets do not add up has lost a payment and every figure below it is wrong. evaluate() checks it
    # again later, this one is ahead of explain() so nothing is spent on a model first
    check_books(outcome, data.payments)

    explanation = explain(outcome, data.payments, data.settlements, data.bank_lines)
    agent_resolved, agent_outage = _agent_verified(explanation)
    report = evaluate(
        dataset, outcome, seconds=elapsed, agent_resolved=agent_resolved, agent_outage=agent_outage
    )

    RESULTS.mkdir(parents=True, exist_ok=True)
    _write_ledger(outcome, report, seed, order_count, as_of)
    _write_report(report, explanation, data.payments, seed, order_count, as_of)
    _write_trace(explanation)

    print(_summary(report, explanation))


# Deterministic and hashable, so two runs on one seed are byte identical. Nothing here comes from the model and no float is written, because a float does not 
# come out the same twice and the file would stop being byte identical
def _write_ledger(
    outcome: MatchOutcome, report: Report, seed: int, order_count: int, as_of: date
) -> None:
    ledger = {
        "seed": seed,
        "order_count": order_count,
        "as_of": as_of.isoformat(),
        "credits_total": report.credits_total,
        "credits_matched": report.credits_matched,
        "credit_value_total": report.credit_value_total,
        "credit_value_matched": report.credit_value_matched,
        "money_captured": report.money_captured,
        "money_received": report.money_received,
        "money_unconfirmed": report.money_unconfirmed,
        "money_at_risk_actual": report.money_at_risk_actual,
        "money_at_risk_reported": report.money_at_risk_reported,
        "money_missed": report.money_missed,
        "money_wrongly_cleared": report.money_wrongly_cleared,
        "unnamed_problems": report.unnamed_problems,
        "money_unexplained": report.money_unexplained,
        "money_unattributed_credit": report.money_unattributed_credit,
        "money_owed_back": report.money_owed_back,
        "money_bank_charges": report.money_bank_charges,
        "money_fee_overcharged": report.money_fee_overcharged,
        "money_in_flight": report.money_in_flight,
        "money_unlinked": report.money_unlinked,
        "money_fee_unverified": report.money_fee_unverified,
        "flagged_total": report.flagged_total,
        "flagged_real": report.flagged_real,
        "in_flight_wrongly_flagged": report.in_flight_wrongly_flagged,
        "matched": sorted(
            (
                {"narration": claim.narration, "payment_ids": sorted(claim.payment_ids)}
                for claim in outcome.matched
            ),
            key=lambda entry: entry["narration"],
        ),
        "flagged": sorted(
            ({"entity_id": item.entity_id, "kind": item.kind} for item in outcome.flagged),
            key=lambda entry: (entry["entity_id"], entry["kind"]),
        ),
        "in_flight": sorted(outcome.in_flight),
        "by_class": [
            {
                "kind": result.kind,
                "expected": result.expected,
                "detected": result.detected,
            }
            for result in report.by_class
        ],
    }
    (RESULTS / "ledger.json").write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")


def _write_report(
    report: Report,
    explanation: Explanation,
    payments: Sequence[Payment],
    seed: int,
    order_count: int,
    as_of: date,
) -> None:
    amount_by_payment = {payment.payment_id: payment.amount for payment in payments}
    diagnosed = [
        incident
        for incident in explanation.incidents
        if incident.accepted and incident.cause != "GAVE_UP"
    ]
    refused = [incident for incident in explanation.incidents if incident.cause == "GAVE_UP"]
    # A group the adjudicator turned down is kept apart from one the model never answered, because the sentence below credits the arithmetic with
    # rejecting a cause and there was no cause to reject when the request failed or the reply carried no tool call
    rejected = [incident for incident in explanation.incidents if incident.cause == "NOT_DIAGNOSED"]
    unanswered = [incident for incident in explanation.incidents if incident.cause == "NOT_ANSWERED"]
    individual_value = sum(amount_by_payment.get(entity, 0) for entity in explanation.individual)

    lines = [
        "# Reconciliation report",
        "",
        f"Run of {order_count} orders on seed {seed}, as of {as_of}.",
        "",
        "## Cash position",
        "",
        f"Of the {rupees(report.money_captured)} captured in this window, every payment ends "
        "in exactly one of these four and the run fails if they do not add up.",
        "",
        f"- {rupees(report.money_received)} received, settled and confirmed by a bank credit",
        f"- {rupees(report.money_in_flight)} in flight, due but not arrived yet",
        f"- {rupees(report.money_unconfirmed)} settled by Razorpay with no bank credit "
        "matched to it, so it is neither confirmed nor known to be missing",
        f"- **{rupees(report.money_at_risk_reported)}** at risk, captured and past due "
        "with nothing settled",
        "",
        "## Money at stake",
        "",
        "Each line is a different kind of exposure and they are not added up, because a credit "
        "that arrived unattributed and a payment owed back to a customer are not the same money.",
        "",
        f"- {rupees(report.money_unattributed_credit)} credited to the bank that this run cannot "
        "tie to any settlement or order",
        f"- {rupees(report.money_at_risk_reported)} captured and past due with nothing settled",
        f"- {rupees(report.money_unlinked)} of orders that tie to no payment at all",
        f"- {rupees(report.money_owed_back)} charged to customers twice and owed back",
        f"- {rupees(report.money_fee_unverified)} of fees with no published rate to check against",
        f"- {rupees(report.money_fee_overcharged)} charged above the agreed rate card",
        f"- {rupees(report.money_bank_charges)} taken by the bank on the transfers",
        "",
        "## What this run may have got wrong",
        "",
        f"- {rupees(report.money_missed)} of money due that this run did not surface. Reads the "
        "missing settlement class alone, so the two lines below cover the seven other kinds",
        f"- {rupees(report.money_wrongly_cleared)} reported as reconciled that was not",
        f"- {report.unnamed_problems} planted problems the run never named, across all eight "
        "kinds. The detection table below says which",
        f"- {rupees(report.money_unexplained)} sitting on records the run noticed under no kind "
        "at all, counted once per record rather than once per label",
        f"- {rupees(report.money_fee_unverified)} of fees charged on payments whose card "
        "network the export does not name, so no published rate exists to check them against",
        "",
        "## What landed",
        "",
        f"- {report.credits_matched} of {report.credits_total} bank credits explained "
        f"({report.match_rate_by_count:.1%} by count, {report.match_rate_by_value:.1%} by value)",
        f"- {report.flagged_total} findings, of which {report.wasted_investigations} would have wasted your time",
        f"- {report.in_flight_wrongly_flagged} healthy in flight payments wrongly reported as a problem",
        f"- {rupees(report.money_unlinked)} of orders that tie to no payment at all",
        f"- {report.records_per_second:,.0f} records per second",
    ]
    if explanation.available:
        lines.append(
            f"- {report.flagged_agent_explained} of those findings were given a cause the agent "
            f"stage verified against the records, covering {rupees(report.value_behind_agent_findings)} "
            f"of turnover, of which {rupees(report.money_agent_reclassified)} is money whose "
            "disposition the verdict decides, wait for a gateway to recover or escalate it"
        )
    lines += ["", "## What to look at", ""]

    if not explanation.available:
        # explanation.trace[-1] carries the real reason from _no_model_reason, so this line cannot name a stale key when the provider list changes, 
        # the way it did when the project moved from Anthropic-only to Groq first
        lines += [
            f"> Agent stage skipped, {explanation.trace[-1]}.",
            "> `make refusal` runs the adjudicator against a model that answers every group",
            "> the same way, and needs no key.",
            "",
        ]

    for incident in diagnosed:
        # cited_ids is what the model actually checked, member_ids is the whole group it was asked about. The two differ when INNER_MAX_ROUNDS stops the 
        # model short, and reporting the group size there would claim a cause for records nobody looked at
        verified, total = len(incident.cited_ids), len(incident.member_ids)
        header = f"{verified} findings" if verified == total else f"{verified} of {total} findings"
        lines.append(f"### {incident.cause}  ({header})")
        lines.append("")
        text = f"{incident.shared}. {incident.reason}."
        if verified < total:
            text += f" The other {total - verified} in this group were not individually checked."
        lines.append(text)
        lines.append("")

    if refused:
        lines += [
            f"### No cause determined  ({len(refused)} groups)",
            "",
            "Grouped, investigated, and left undiagnosed rather than guessed at.",
            "",
        ]
        # The model's own reason for stopping rather than a sentence written here, so the report says what was ruled out
        # and not just that nothing was named
        lines += [f"- {incident.shared}. {incident.reason.rstrip('.')}." for incident in refused]
        lines.append("")

    if explanation.individual:
        lines += [
            f"### {len(explanation.individual)} individual findings, no shared pattern",
            "",
            f"{rupees(individual_value)} across findings that grouped with nothing else.",
            "",
        ]

    if rejected and explanation.available:
        lines += [
            "## Proposals the arithmetic rejected",
            "",
            f"The model proposed {len(diagnosed) + len(rejected)} causes. {len(rejected)} were "
            "rejected because the cited records did not support them.",
            "",
        ]
        for incident in rejected[:5]:
            lines.append(f"- {incident.shared}: {incident.reason}")
        lines.append("")

    if unanswered and explanation.available:
        lines += [
            f"## {len(unanswered)} groups the model never answered",
            "",
            "Not a refusal and not a rejection, the model produced nothing the loop could read "
            f"on any of its {MAX_TURNS} attempts. The last reason is shown.",
            "",
        ]
        for incident in unanswered[:5]:
            lines.append(f"- {incident.shared}: {incident.reason}")
        lines.append("")

    lines += ["## Detection by class", "", "| class | expected | detected |", "|---|---|---|"]
    for result in report.by_class:
        lines.append(f"| {result.kind} | {result.expected} | {result.detected} |")
    lines.append("")
    if explanation.available:
        # This table is the deterministic cascade only, GATEWAY_OUTAGE reads 0 there on purpose because reconcile.py never emits that kind. The agent stage
        # is where it is actually checked, against the planted label and not just the model's word
        lines += [
            f"GATEWAY_OUTAGE reads 0 detected above because the cascade never emits that "
            f"kind. The agent stage separately verified {report.gateway_outage_agent_verified} "
            f"of the {next(r.expected for r in report.by_class if r.kind == 'GATEWAY_OUTAGE')} "
            "planted, checked against the answer key and not just accepted on the model's say.",
            "",
        ]

    (RESULTS / "report.md").write_text("\n".join(lines))


def _write_trace(explanation: Explanation) -> None:
    lines = ["# Agent trace", "", f"Model: {explanation.model or 'none configured'}", ""]
    lines += [f"- {entry}" for entry in explanation.trace]
    lines.append("")
    (RESULTS / "trace.md").write_text("\n".join(lines))


def _summary(report: Report, explanation: Explanation) -> str:
    diagnosed = sum(
        1
        for incident in explanation.incidents
        if incident.accepted and incident.cause != "GAVE_UP"
    )
    # The four outcomes are counted separately and printed beside the group total, so the line adds up and cannot credit the adjudicator with a group the model 
    # never answered. A give up used to fall through every count and the tally came out short of the groups it was describing
    counted = {
        "gave up": sum(1 for incident in explanation.incidents if incident.cause == "GAVE_UP"),
        "refused": sum(1 for incident in explanation.incidents if incident.cause == "NOT_DIAGNOSED"),
        "unanswered": sum(1 for incident in explanation.incidents if incident.cause == "NOT_ANSWERED"),
    }
    tally = ", ".join(f"{count} {name}" for name, count in counted.items())
    # This counts what the agent stage was shown and not flagged_total, because group_findings does not sort every kind
    sorted_count = sum(len(incident.member_ids) for incident in explanation.incidents) + len(
        explanation.individual
    )
    middle = (
        f"sorted {sorted_count} of {report.flagged_total} findings -> {len(explanation.incidents)} groups: "
        f"{diagnosed} diagnosed, {tally}; {len(explanation.individual)} individual; "
        f"{report.flagged_agent_explained} findings given a verified cause, "
        f"{rupees(report.money_agent_reclassified)} reclassified"
        if explanation.available
        else f"sorted {sorted_count} of {report.flagged_total} findings, not diagnosed (no model configured)"
    )
    return (
        f"{report.credits_matched}/{report.credits_total} credits explained "
        f"({report.match_rate_by_count:.1%} count, {report.match_rate_by_value:.1%} value), "
        f"precision {report.precision:.1%}, {report.records_per_second:,.0f} rec/s\n"
        f"{middle}\n"
        f"at risk {rupees(report.money_at_risk_reported)}, "
        f"missed {rupees(report.money_missed)}, "
        f"wrongly cleared {rupees(report.money_wrongly_cleared)}, "
        f"{report.unnamed_problems} problems unnamed on {rupees(report.money_unexplained)}\n"
        f"results/report.md  results/ledger.json  results/trace.md"
    )


if __name__ == "__main__":
    run()
