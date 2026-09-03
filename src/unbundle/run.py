import json
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from unbundle.diagnose import Explanation, explain
from unbundle.evaluate import MatchOutcome, Report, check_books, evaluate
from unbundle.synthetic import DEFAULT_SEED, WINDOW_END, generate, write_csvs
from unbundle.load import load
from unbundle.reconcile import match
from unbundle.record_types import Payment
from unbundle.money import Paise, format_amount

# Grouping is added here and not in format_amount, because it is presentation and it is
# locale specific. Indian grouping puts the last three digits together then pairs, so a
# lakh reads 1,00,000 and not 100,000
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


def run(seed: int = DEFAULT_SEED, order_count: int = 5_000, as_of: date = WINDOW_END) -> None:
    dataset = generate(seed=seed, order_count=order_count)
    write_csvs(dataset, DATA)

    # Timed from reading the files, not from the records already being in memory, because
    # a merchant's run starts with five CSVs on disk and parsing them is most of the cost
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

    # Raises rather than reports, a run whose buckets do not add up has lost a payment and
    # every figure below it is unsound. evaluate() runs the same check, and this call is
    # ahead of explain() so a broken partition stops the run before any model spend
    check_books(outcome, data.payments)

    explanation = explain(outcome, data.payments, data.settlements, data.bank_lines)
    report = evaluate(dataset, outcome, seconds=elapsed)

    RESULTS.mkdir(parents=True, exist_ok=True)
    _write_ledger(outcome, report, seed, order_count, as_of)
    _write_report(report, explanation, data.payments, seed, order_count, as_of)
    _write_trace(explanation)

    print(_summary(report, explanation))


# Deterministic and hashable, so two runs on one seed are byte identical. Nothing here
# comes from the model and no float is written, because a float in a hashed file is both
# a wrong number and a broken guarantee
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
        if incident.accepted and incident.cause != "NONE_OF_THESE"
    ]
    refused = [incident for incident in explanation.incidents if incident.cause == "NONE_OF_THESE"]
    rejected = [incident for incident in explanation.incidents if not incident.accepted]
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
        "## What this run may have got wrong",
        "",
        f"- {rupees(report.money_missed)} missing that this run did not surface",
        f"- {rupees(report.money_wrongly_cleared)} reported as reconciled that was not",
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
        "",
        "## What to look at",
        "",
    ]

    if not explanation.available:
        lines += [
            "> Agent stage skipped: no ANTHROPIC_API_KEY set. Findings are grouped but not",
            "> diagnosed. `make refusal` runs the adjudicator against a model that answers",
            "> every group the same way, and needs no key.",
            "",
        ]

    for incident in diagnosed:
        lines.append(f"### {incident.cause}  ({len(incident.member_ids)} findings)")
        lines.append("")
        lines.append(f"{incident.shared}. {incident.reason}.")
        lines.append("")

    if refused:
        lines += [
            f"### No cause determined  ({len(refused)} groups)",
            "",
            "Grouped, investigated, and left undiagnosed rather than guessed at.",
            "",
        ]

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

    lines += ["## Detection by class", "", "| class | expected | detected |", "|---|---|---|"]
    for result in report.by_class:
        lines.append(f"| {result.kind} | {result.expected} | {result.detected} |")
    lines.append("")

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
        if incident.accepted and incident.cause != "NONE_OF_THESE"
    )
    # Nothing was rejected when nothing was proposed, and reporting a skipped stage as a
    # rejection would understate the number the adjudicator actually earns
    rejected = (
        sum(1 for incident in explanation.incidents if not incident.accepted)
        if explanation.available
        else 0
    )
    middle = (
        f"{report.flagged_total} findings -> {diagnosed} incidents, "
        f"{rejected} proposals rejected, {len(explanation.individual)} individual"
        if explanation.available
        else f"{report.flagged_total} findings grouped, not diagnosed (no model configured)"
    )
    return (
        f"{report.credits_matched}/{report.credits_total} credits explained "
        f"({report.match_rate_by_count:.1%} count, {report.match_rate_by_value:.1%} value), "
        f"precision {report.precision:.1%}, {report.records_per_second:,.0f} rec/s\n"
        f"{middle}\n"
        f"at risk {rupees(report.money_at_risk_reported)}, "
        f"missed {rupees(report.money_missed)}, "
        f"wrongly cleared {rupees(report.money_wrongly_cleared)}\n"
        f"results/report.md  results/ledger.json  results/trace.md"
    )


if __name__ == "__main__":
    run()
