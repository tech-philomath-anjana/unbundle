import tempfile
import time
from pathlib import Path

from unbundle.synthetic import DEFAULT_SEED, WINDOW_END, generate, write_csvs
from unbundle.load import load
from unbundle.reconcile import match
from unbundle.evaluate import evaluate, check_books


# run.py writes data/ and results/ by hardcoded path so measuring through it would overwrite
# the committed run
def one(seed: int, order_count: int) -> dict:
    dataset = generate(seed=seed, order_count=order_count)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        write_csvs(dataset, out)

        # the clock goes above load and not above match, so moving it down would drop the
        # parse out of every figure this prints
        started = time.perf_counter()
        data = load(out)
        outcome = match(
            data.orders,
            data.payments,
            data.settlements,
            data.adjustments,
            data.bank_lines,
            as_of=WINDOW_END,
        )
        elapsed = time.perf_counter() - started

        check_books(outcome, data.payments)
        report = evaluate(
            dataset, outcome, seconds=elapsed, agent_resolved=frozenset(), agent_outage=frozenset()
        )
        records = (
            len(data.orders)
            + len(data.payments)
            + len(data.settlements)
            + len(data.bank_lines)
            + len(data.adjustments)
        )
        return {
            "seed": seed,
            "orders": order_count,
            "records": records,
            "seconds": elapsed,
            # Report counts payments and bank lines where records above counts every collection
            # loaded, so the rate column does not divide out of the records column beside it
            "rec_s": report.records_per_second,
            "count": report.match_rate_by_count,
            "value": report.match_rate_by_value,
            "precision": report.precision,
            "credits": f"{report.credits_matched}/{report.credits_total}",
        }


def bench() -> None:
    print("THROUGHPUT, tuned seed", DEFAULT_SEED)
    print(f"{'orders':>8} {'records':>9} {'seconds':>9} {'rec/s':>12}")
    for n in (50, 5_000, 50_000):
        r = one(DEFAULT_SEED, n)
        print(f"{r['orders']:>8,} {r['records']:>9,} {r['seconds']:>9.3f} {r['rec_s']:>12,.0f}")

    print()
    print("UNTUNED SEEDS, 3,000 orders each")
    print(f"{'seed':>10} {'credits':>9} {'count':>8} {'value':>8} {'precision':>10}")
    r = one(DEFAULT_SEED, 3_000)
    print(
        f"{r['seed']:>10} {r['credits']:>9} {r['count']:>7.1%} "
        f"{r['value']:>7.1%} {r['precision']:>9.1%}   <- tuned"
    )
    for seed in (1, 7, 4242, 20260901, 99999):
        r = one(seed, 3_000)
        print(
            f"{r['seed']:>10} {r['credits']:>9} {r['count']:>7.1%} "
            f"{r['value']:>7.1%} {r['precision']:>9.1%}"
        )


if __name__ == "__main__":
    bench()
