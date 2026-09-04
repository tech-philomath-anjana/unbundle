from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from unbundle.diagnose import (
    MAX_TURNS,
    Group,
    Incident,
    Propose,
    Proposal,
    build_evidence_maps,
    build_lookups,
    explain,
    run_tool_loop,
)
from unbundle.synthetic import DEFAULT_SEED, WINDOW_END, generate
from unbundle.reconcile import match

RESULTS = Path("results")


def _scripted_tool_call(name: str, tool_input: dict) -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", name=name, input=tool_input, id=name)
    return SimpleNamespace(content=[block])


# Drives the same run_tool_loop the keyed path runs, with a scripted model call standing in
# for a real one, so the reproducible no-key evidence exercises real lookups and a real
# refusal rather than the older single fixed answer. It still never refuses on its own, every
# group's first member is looked up and every member is then cited, so a group that comes
# back rejected was rejected on the records alone
def always_an_outage(lookups: Mapping[str, Callable[[dict], dict]]) -> Propose:
    def propose(group: Group, evidence: str) -> Proposal:
        lookup_tool, id_field = (
            ("look_up_settlement", "settlement_id")
            if group.kind in ("BANK_FEE_DEDUCTED", "MANGLED_UTR")
            else ("look_up_payment", "payment_id")
        )
        scripted = iter(
            (
                _scripted_tool_call(lookup_tool, {id_field: group.member_ids[0]}),
                _scripted_tool_call("resolve", {"cause": "GATEWAY_OUTAGE", "cited": list(group.member_ids)}),
            )
        )
        return run_tool_loop(lambda messages: next(scripted), [{"role": "user", "content": evidence}], lookups)

    return propose


# cited_ids is empty on a GAVE_UP incident, and the empty set is a subset of every set, so
# leaving it in the clean/dirty split would score a refusal as a correct outage. Split it out
# on its own rather than let it hide inside either bucket
def _split_by_correctness(
    accepted: Sequence[Incident], truly_an_outage: set[str]
) -> tuple[list[Incident], list[Incident], list[Incident]]:
    resolved = [incident for incident in accepted if incident.cause != "GAVE_UP"]
    gave_up = [incident for incident in accepted if incident.cause == "GAVE_UP"]
    clean = [incident for incident in resolved if set(incident.cited_ids) <= truly_an_outage]
    dirty = [incident for incident in resolved if not set(incident.cited_ids) <= truly_an_outage]
    return clean, dirty, gave_up


def run(
    seed: int = DEFAULT_SEED, order_count: int = 5_000, as_of: date = WINDOW_END
) -> None:
    dataset = generate(seed=seed, order_count=order_count)
    outcome = match(
        dataset.orders, dataset.payments, dataset.settlements, dataset.adjustments,
        dataset.bank_lines, as_of=as_of,
    )
    maps = build_evidence_maps(dataset.payments, dataset.settlements, dataset.bank_lines, outcome)
    # No API key is read and no model is called, only the same tool-calling protocol driven by
    # a script, so anyone who clones the repo can run this and get the same numbers
    explanation = explain(
        outcome, dataset.payments, dataset.settlements, dataset.bank_lines,
        propose=always_an_outage(build_lookups(maps)),
    )

    accepted = [incident for incident in explanation.incidents if incident.accepted]
    rejected = [incident for incident in explanation.incidents if not incident.accepted]

    truly_an_outage = {
        label.entity_id for label in dataset.labels if label.kind == "GATEWAY_OUTAGE"
    }
    clean, dirty, gave_up = _split_by_correctness(accepted, truly_an_outage)

    calls = sum(incident.turns for incident in explanation.incidents)
    lookups_made = sum(len(incident.lookups) for incident in explanation.incidents)

    lines = [
        "# Refusal run",
        "",
        f"Seed {seed}, {order_count} orders, as of {as_of}. Every proposal runs through the "
        "same tool-calling loop the keyed path uses: it looks up its own group's first "
        "member, then answers GATEWAY_OUTAGE and cites every member of it, and ignores the "
        "reason it is given when a proposal comes back rejected. The model call is scripted "
        "rather than real, so no API key is read and this is reproducible by anyone who "
        "clones the repository.",
        "",
        "## What the adjudicator did",
        "",
        f"- {len(explanation.incidents)} groups put to the proposer",
        f"- {calls} proposals made, up to {MAX_TURNS} turns each, with the reason for a "
        "rejection fed back before the next turn",
        f"- {lookups_made} record(s) looked up before a proposal, one per group per turn",
        f"- **{len(rejected)} groups still unexplained** after being told, every turn, "
        "exactly what the records did not support",
        f"- {len(accepted)} accepted ({len(gave_up)} of those a give up, scored separately below)",
        f"- of the {len(clean) + len(dirty)} accepted with a cause, {len(clean)} cited only "
        f"payments that really were planted as an outage, and {len(dirty)} did not",
        "",
    ]

    if rejected:
        lines += [
            "## Why each group was refused",
            "",
            "| group | members | turns | lookups | reason on the last turn |",
            "|---|---|---|---|---|",
        ]
        for incident in rejected:
            lines.append(
                f"| {incident.shared} | {len(incident.member_ids)} | {incident.turns} "
                f"| {len(incident.lookups)} | {incident.reason} |"
            )
        lines.append("")

    if accepted:
        lines += ["## What survived", "", "| group | members | cited | lookups |", "|---|---|---|---|"]
        for incident in accepted:
            lines.append(
                f"| {incident.shared} | {len(incident.member_ids)} | {len(incident.cited_ids)} "
                f"| {len(incident.lookups)} |"
            )
        lines.append("")

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "refusal.md").write_text("\n".join(lines))

    print(
        f"{len(explanation.incidents)} groups, {calls} proposals, {MAX_TURNS} turns each at "
        f"most, {len(rejected)} refused, {len(accepted)} accepted\n"
        f"{len(dirty)} of the {len(clean) + len(dirty)} resolved cited a payment that was not "
        f"planted as an outage, {len(gave_up)} gave up"
    )
    print("results/refusal.md")


if __name__ == "__main__":
    run()
