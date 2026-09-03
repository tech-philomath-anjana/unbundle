from datetime import date
from pathlib import Path

from unbundle.diagnose import MAX_TURNS, Group, explain
from unbundle.synthetic import DEFAULT_SEED, WINDOW_END, generate
from unbundle.reconcile import match

RESULTS = Path("results")


# This proposer never refuses, every group gets GATEWAY_OUTAGE and all the members are cited, so a group that still 
# comes back rejected was rejected on the records alone
def always_an_outage(group: Group, evidence: str) -> tuple[str, tuple[str, ...]]:
    return "GATEWAY_OUTAGE", group.member_ids


def run(
    seed: int = DEFAULT_SEED, order_count: int = 5_000, as_of: date = WINDOW_END
) -> None:
    dataset = generate(seed=seed, order_count=order_count)
    outcome = match(
        dataset.orders, dataset.payments, dataset.settlements, dataset.adjustments,
        dataset.bank_lines, as_of=as_of,
    )
    # Passing propose means no key is read and no model is called, so anyone who clones the repo can run this and 
    # get the same numbers
    explanation = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines,
        propose=always_an_outage,
    )

    accepted = [incident for incident in explanation.incidents if incident.accepted]
    rejected = [incident for incident in explanation.incidents if not incident.accepted]

    truly_an_outage = {
        label.entity_id for label in dataset.labels if label.kind == "GATEWAY_OUTAGE"
    }
    clean = [incident for incident in accepted if set(incident.cited_ids) <= truly_an_outage]
    dirty = [incident for incident in accepted if not set(incident.cited_ids) <= truly_an_outage]

    calls = sum(incident.turns for incident in explanation.incidents)

    lines = [
        "# Refusal run",
        "",
        f"Seed {seed}, {order_count} orders, as of {as_of}. The proposer answers "
        "GATEWAY_OUTAGE to every group and cites every member of it, and ignores the "
        "reason it is given when a proposal comes back rejected. No model is called, so "
        "this is reproducible by anyone who clones the repository, without an API key.",
        "",
        "## What the adjudicator did",
        "",
        f"- {len(explanation.incidents)} groups put to the proposer",
        f"- {calls} proposals made, up to {MAX_TURNS} turns each, with the reason for a "
        "rejection fed back before the next turn",
        f"- **{len(rejected)} groups still unexplained** after being told, every turn, "
        "exactly what the records did not support",
        f"- {len(accepted)} accepted",
        f"- of those accepted, {len(clean)} cited only payments that really were planted "
        f"as an outage, and {len(dirty)} did not",
        "",
    ]

    if rejected:
        lines += [
            "## Why each group was refused",
            "",
            "| group | members | turns | reason on the last turn |",
            "|---|---|---|---|",
        ]
        for incident in rejected:
            lines.append(
                f"| {incident.shared} | {len(incident.member_ids)} | {incident.turns} "
                f"| {incident.reason} |"
            )
        lines.append("")

    if accepted:
        lines += ["## What survived", "", "| group | members | cited |", "|---|---|---|"]
        for incident in accepted:
            lines.append(
                f"| {incident.shared} | {len(incident.member_ids)} | {len(incident.cited_ids)} |"
            )
        lines.append("")

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "refusal.md").write_text("\n".join(lines))

    print(
        f"{len(explanation.incidents)} groups, {calls} proposals, {MAX_TURNS} turns each at "
        f"most, {len(rejected)} refused, {len(accepted)} accepted\n"
        f"{len(dirty)} of the accepted cited a payment that was not planted as an outage"
    )
    print("results/refusal.md")


if __name__ == "__main__":
    run()
