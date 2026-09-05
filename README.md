# unbundle

Razorpay AI Buildathon: Track 04 | AI Finance Controller.

A bank credit is one payment covering many orders and nothing in it says which orders those
are, so the merchant's records, the gateway's and the bank's never line up on their own.
unbundle works out what is inside each credit, checks that the money actually arrived and
names what it could not account for.

The agent works only the residue that the deterministic code could not explain. It gets six
tools there, four read-only lookups and two ways to answer and it picks what to look up and
how many times before it answers. It either names a cause and cites the records behind it, or
says no cause fits, which it did for four groups rather than inventing one and for two more
the adjudicator turned down every cause it offered so those stayed undiagnosed.

Everything else is deterministic. The matching, the sums and the fees are code, so the model
never writes a figure, the cause it picks comes from a closed list rather than free text and
every cause is checked against the ledger before it counts.

## Contents

- [At a glance](#at-a-glance)
- [Architecture](#architecture)
- [One credit, unbundled](#one-credit-unbundled)
- [Why this is hard](#why-this-is-hard)
- [How it works](#how-it-works)
- [Incidents, not a list of rows](#incidents-not-a-list-of-rows)
- [What a run produces](#what-a-run-produces)
- [What it did](#what-it-did)
- [The cash position](#the-cash-position)
- [On seeds it was never tuned on](#on-seeds-it-was-never-tuned-on)
- [The honest exception list](#the-honest-exception-list)
- [Run it](#run-it)
- [Limitations](#limitations)
- [The repo](#the-repo)

## At a glance

```
make test          108 passed. No network, no key
make refusal        34 groups, 102 proposals, 0 accepted. No key either
make reproduce      90.0% of bank credits matched by count, 93.3% by value
                    316 of 316 findings raised were real
make dashboard      dashboard.html, one file, opens with no server
```

Every number in this file is read out of the committed run in `results/` and the sections below
say where each one comes from, apart from the throughput and untuned seed tables, which come from
`make bench`. The seed figures there reproduce exactly, and the records a second are wall clock so
they move a little from machine to machine and run to run.

The quickest way in is the dashboard, live at
https://tech-philomath-anjana.github.io/unbundle/dashboard.html, so it needs no clone, no setup,
no key and no terminal. The same page is committed as `dashboard.html`, so double clicking it out
of a clone works the same way.
Click a bank credit to see the arithmetic behind it, click View source files for the five raw
CSVs it was built from.

<img width="1494" height="880" alt="Screenshot 2026-09-05 at 9 34 28 AM" src="https://github.com/user-attachments/assets/62882597-07fc-4262-83da-467ffaea4664" />

---

## Architecture

<img width="1760" height="1184" alt="unbundle — architecture" src="https://github.com/user-attachments/assets/f18a62be-9b95-4216-b564-c1f1095c75a4" />

Six stages, matching the diagram above.

Input is three sources across five files, 6,368 rows: `orders.csv` from the merchant,
`payments.csv` / `settlements.csv` / `adjustments.csv` from the gateway, `bank_statement.csv`
from the bank. `load.py` reads all five into typed, frozen records with every amount already
an integer of paise, so nothing downstream ever touches a float or a raw string.

Deterministic matching is `reconcile.py` running the cascade: parse the UTR out of the bank
narration and look up the settlement it names, fall back to amount and date for the lines a
damaged UTR leaves behind, then tie each settlement's own arithmetic out against what the bank
actually credited.

The books check is `check_books`, which puts every captured payment in exactly one of four
buckets, received, in flight, at risk or unconfirmed, and raises rather than reports if they
do not sum to what was captured. It runs before the model is ever called, so a run whose books
do not close never reaches the point of spending a model call on numbers that are already
wrong. On the published run this step alone accounts for 6,052 of 6,368 records, 95% of
everything in the dataset, leaving 316 findings for the next two stages.

Measurement is `ground_truth.py`, which holds the answer key, and `evaluate.py`, which scores
against it. Neither is imported by `reconcile.py` or `diagnose.py`, and that is asserted by a
test that reads both files off disk rather than merely stated here.

The agent stage is `diagnose.py` grouping the 316 findings into incidents, which the section
below sets out, and handing each incident to a model with six tools: four
read-only lookups plus `resolve` and `give_up`. The model picks what to look up and how many
times, and a cause has to come off a closed list rather than free text. Every `resolve` is
re-checked by an adjudicator that is code and not a second model call, which re-reads the
cited records against the ledger and accepts or rejects with a reason, up to three turns per
incident. Four incidents ended in `give_up`, and two more had every cause the model proposed
rejected, so both stayed undiagnosed.

Outputs are `results/ledger.json`, deterministic and hashed, byte identical on a re-run, and
`results/report.md` and `results/trace.md`, which carry the run in prose including what it may
have got wrong. `dashboard.html` renders all of it as one self contained page with no
server.

---

## One credit, unbundled

`setl_00040` from the published run. The bank statement gives one line:

```
2026-08-21   CR   Rs 71,679.42   NEFT-AXISN26082100040-RAZORPAY
```

Nothing on either side of that line names the orders inside it. What unbundle makes of it:

```
UTR AXISN26082100040, parsed out of the narration, resolves to setl_00040

    30 payments, gross                          73,876.09
       less fees, the Rs 271.65 GST included    -1,780.66
       less refund adj_000059, raised 20 Aug      -391.41
                                              ------------
    what Razorpay said it sent                   71,704.02
    what the bank actually credited              71,679.42
                                              ------------
    short by                                         24.60   BANK_FEE_DEDUCTED
```

The same breakdown, open in `dashboard.html`:

<img width="1492" height="858" alt="Screenshot 2026-09-05 at 9 36 40 AM" src="https://github.com/user-attachments/assets/5d8183e4-604f-4083-8f76-2a615b6e70c1" />

The 30 payments come off `settlement_id`, so that part is a read and not a search. The fees
come off once because `fee` already contains the GST and subtracting `tax` again would deduct
it twice. The refund is raised against `pay_001293` which settled here, so it is ordinary,
where a refund against a payment that settled in an earlier cycle is `PARTIAL_REFUND` instead
and the payment id is the only thing separating the two.

The Rs 24.60 left at the bottom is small enough to be the bank's transfer charge so it is
flagged as one rather than being absorbed into the match. Finding the settlement is not the
same as the money being right, so every claimed credit is tied out against what the settlement
said it sent and a credit that arrives short is reported short.

---

## Why this is hard

Three sources record the same money across five files and each of them says a different total.

```
Merchant            orders.csv           3,000 rows
Gateway (Razorpay)  payments.csv         3,168 rows
                    settlements.csv         57 rows
                    adjustments.csv         83 rows
Bank                bank_statement.csv      60 rows
```

The UTR that ties a bank line back to a settlement is a clean field on the gateway side and
free text inside the narration on the bank side, so the join that would make this easy does not
exist. If the bank statement had a `utr` column this would be one line of SQL.

Eight credits in this run have that field damaged and the damage comes in two shapes:

```
setl_00018   utr AXISN26081200018   narration  NEFT-AXISN26081200-RAZORPAY
setl_00021   utr AXISN26081300021   narration  NEFT-AXISN26081300-RAZORPAY
setl_00023   utr AXISN26081300023   narration  NEFT-AXISN26081300-RAZORPAY
setl_00050   utr AXISN26082600050   narration  NEFT CR RAZORPAY SOFTWARE
```

`setl_00021` and `setl_00023` settled on the same day, so once the last five characters are
gone their narrations are identical and the reference stops identifying anything. Those are
recovered on amount and date instead, and that fallback refuses two candidates: one fit is a
match, zero or two or more is `UNKNOWN_CREDIT`, because picking one of two attributes the money
to the wrong orders and then calls it reconciled.

`Payment.settlement_id` is populated in the gateway export, so working out which payments sit
inside a settlement is a dictionary read and not a subset sum search, and this project does not
pretend otherwise. What nothing gives you is the join from the bank line when the narration is
damaged, the join back to the merchant's own orders, and any check that the money named is the
money that arrived.

---

## How it works

The cascade in `reconcile.py` runs the UTR pass over every bank line first and only then runs
the amount and date fallback over the lines that had none, which is not the same as trying each
step per line, and the difference was a live bug until 2 September: a stray credit copying a
real amount could take a settlement by the fallback that a later line then named outright by
its UTR. Both paths check whether a settlement is already claimed now, and a credit naming one
that is taken is `UNKNOWN_CREDIT` rather than a second match.

`check_books` runs before the model is ever called and raises rather than reports, because a
run whose four buckets do not reconcile has lost a payment somewhere and every figure after it
is wrong, so there is no point spending a model call on it.

Two walls hold the rest up.

The answer key enters at one place only. `ground_truth.py` reaches `evaluate.py` and nothing
else, so the matcher cannot score itself and the agent cannot be told the answer, and that is
asserted rather than promised because `test_matcher_never_imports_the_answer_key` reads
`reconcile.py` off disk and checks it for the import.

The model never writes a figure. What reaches it is the residue, already grouped into incidents
by the section below, handed over as groups rather than as rows. It proposes a cause and
cites the records it relies on, then the adjudicator re-reads those records and rejects a claim
they do not support with the reason handed back, so the model can revise and a rejected cause
never reaches the ledger. This is `results/trace.md`, unedited:

```
- proposing for MISSING_SETTLEMENT: card captured 2026-08-19 between 08:00 and 12:00, 3 members
-   turn 1: looked up 1 record(s), proposed SETTLEMENT_FAILED citing 4,
             rejected: cited setl_00039, not in the group
-   turn 2: looked up 2 record(s), proposed SETTLEMENT_FAILED citing 3,
             accepted: 3 payments on setl_00039, which failed
```

On this run 14 first proposals were rejected and 10 of those were accepted on a later turn, so
the model can be wrong and still cannot corrupt the books. Four groups ended in give up rather
than a plausible label, which is a designed outcome and not a failure, because an agent that
explains everything including the unknowable is a confabulation machine with good manners.

The provider is a choice and not an architecture. The published run used `openai/gpt-oss-120b`
through Groq with Anthropic as the fallback, both behind one `Propose` interface, so the loop,
the adjudicator and every test are unchanged by which one answers. No API key, a rate limit, an
unparseable reply or any exception, and the deterministic pipeline still completes with the
report saying explanations are unavailable, which `live-run-evidence/rate-limited-run/` records
actually happening rather than claims.

Three places a model was not used:

1. The matching. A model asked which payments sit inside a credit would be guessing where a
   dictionary read and a subtraction give the answer, and its guess could not be checked
   without doing the arithmetic anyway.
2. The money. Every figure is integer paise computed in `money.py`, so no model output ever
   becomes a number on the page. There are no floats anywhere in the reconciliation path and no
   pandas, which coerces int columns to float64 the moment a value is missing.
3. Judging the model's own answer. The adjudicator is code and not a second model call, since
   an LLM as judge means two systems that can both be confidently wrong about the same records
   with nothing underneath either of them.

The metrics harness was built before the matcher, on purpose, because a matcher built first
gets tuned by eye against whatever it happens to score well on.

---

## Incidents, not a list of rows

The residue is 316 findings, and handing somebody 316 rows is handing them the job back. A
reconciliation that ends in a list has moved the work rather than done it.

`diagnose.py` groups the findings by what they have in common before any model is called, so 160
of them become 34 incidents and 63 stand alone. What comes out is a thing to decide about rather
than a row to interpret, straight out of `results/report.md`:

```
### SETTLEMENT_FAILED  (4 findings)

card captured 2026-08-18 between 08:00 and 12:00. 4 payments on setl_00038, which failed.
```

That is one decision instead of four investigations, and one of the decisions available is that
an incident is noise and can be ignored. The grouping is deterministic and happens first, so the
model is handed groups and never gets to decide what belongs with what.

93 findings are never sorted into anything, and they stay in the ledger as themselves rather than
being forced into a group to make the number look tidier.

---

## What a run produces

```
results/ledger.json    every matched credit and every finding, deterministic and hashed
results/report.md      the run in prose, including what it got wrong
results/trace.md       every turn the model took, including the rejections
results/refusal.md     the confabulating proposer against the adjudicator
dashboard.html         all of the above as one page, no server
```

`report.md` writes its own failure section every run, under the heading What this run may have
got wrong, and a second one further down called Proposals the arithmetic rejected. Neither is
assembled by hand for this file.

The outputs are split on purpose. `ledger.json` is deterministic and hashed, so running the
same seed twice gives a byte identical file, while `report.md` and `trace.md` carry model prose
and will vary. Claiming a byte identical output for a run containing an investigating LLM is a
claim that cannot be kept.

---

## What it did

Seed `20260828`, 3,000 orders, as of 2026-08-28. Everything below is in `results/`.

| | |
|---|---|
| Match rate | 90.0% by count, 93.3% by value, 54 of 60 bank credits |
| Precision | 316 of 316 findings real, so nothing raised would have wasted your time |
| False alarms on healthy money | 0 in flight payments wrongly reported as a problem |
| Agent contribution | 34 groups over 53 turns: 28 accepted, 19 rejected, 4 gave up, 2 provider retries |
| Agent refusal rate | 34 of 34 confabulated proposals refused |

Count and value are both reported because they diverge, and a run can miss few credits while
missing most of the money, or the other way round, so one figure on its own does not say which
happened.

### Throughput

Timed from `load` rather than from records already in memory, because a merchant's run starts
with five CSVs sitting on disk and parsing them is most of the cost.

```
   orders    records   seconds        rec/s
       50        169     0.001       97,166
    5,000     10,542     0.037      144,176
   50,000    104,008     0.373      140,912
```

The 50 record case is slower per record than the other two and that is fixed cost rather than
the matcher, since opening five files costs the same whether there are 169 rows in them or
104,008. Past a few thousand records it flattens out, so the figure that describes the tool is
the 140,912 and not the 97,166.

## The cash position

Every captured payment ends in exactly one of four states and the run fails if they do not add
up to what was captured.

```
Rs 79,11,875.11   captured in the window
Rs 69,23,256.83   received, settled and confirmed by a bank credit
Rs  6,08,596.16   in flight, due but not arrived
Rs  3,80,022.12   at risk, captured and past due with nothing settled
```

A payment captured on Tuesday and due Thursday is healthy, so in flight is a fourth state
rather than a fifth problem and is never reported as an exception. Counting it as missing money
is the easiest way to build a tool that cries wolf.

Every in flight payment carries a due date, two working days after capture, counted in working
days by `reconcile.py` rather than in calendar days, so a Friday capture is due on Tuesday and a
weekend never reads as late. That date is what separates the two states rather than any judgement
about the payment: in flight is money with its date still ahead of it, at risk is money whose
date has gone past. So the position answers when as well as how much, which is the half of a
controller's job that looks forward.

### Money at stake

These are not added up, because a credit that arrived unattributed and a payment owed back to a
customer are not the same money and a total across them would mean nothing.

```
Rs 4,82,723.68   credited to the bank, tied to no settlement or order
Rs 3,80,022.12   captured and past due with nothing settled
Rs 1,49,390.05   of orders that tie to no payment at all
Rs 1,34,431.60   charged to customers twice and owed back
Rs    3,487.21   of fees on payments whose card network the export does not name
Rs    1,098.10   charged above the agreed rate card
Rs      360.47   taken by the bank on the transfers
```

### The two numbers that make this look worse

A match rate on its own proves nothing, so these are reported beside it.

```
Rs 5,177.87   money due that this run did not surface
Rs 9,396.90   reported as reconciled that was not
14            planted problems the run never named at all
```

## On seeds it was never tuned on

Every figure above comes from seed `20260828`, which is the seed the matcher was built against,
so it is the one number that cannot be trusted on its own. Five seeds picked arbitrarily, same
3,000 orders each:

```
      seed   credits    count    value   precision
  20260828     54/60    90.0%    93.3%      100.0%   the tuned one
         1     50/59    84.7%    86.9%       98.1%
         7     51/65    78.5%    88.9%       98.2%
      4242     53/63    84.1%    86.3%       98.1%
  20260901     47/64    73.4%    83.9%       96.2%
     99999     53/63    84.1%    89.7%       99.4%
```

The tuned seed is the best of the six on all three, so the honest range for this matcher is
73.4% to 84.7% by count and 96.2% to 99.4% precision, and the headline 90.0% and 100.0% are the
top of the range rather than the middle of it. What holds up across all six is that precision
stays above 96%, so the matcher goes on being cautious about what it claims even where it
matches less.

These six runs are deterministic only, since no key was set, and the three figures are not
affected by the agent stage. Re-running the tuned seed with the agent disabled reproduced
90.0%, 93.3% and 100.0% exactly, which is what makes the comparison worth reading.

---

## The honest exception list

Eighteen kinds are planted by the generator and recorded in `ground_truth.py`, nine of them
problems and nine ordinary, and each one is measured for detection. The matcher never sees the
answer key. `IN_FLIGHT` is the eighteenth and is deliberately not in this table, since it is a
state and there is nothing to detect.

| class | planted | detected |
|---|---|---|
| BANK_FEE_DEDUCTED | 15 | 15 |
| BATCHED | 54 | 54 |
| CHARGEBACK_LATER | 4 | 4 |
| DUPLICATE_PAYMENT | 36 | 36 |
| FAILED_RETRY | 132 | 132 |
| FEE_MISMATCH | 29 | 29 |
| GATEWAY_OUTAGE | 10 | 0 |
| HELD_BACK | 4 | 0 |
| MANGLED_UTR | 8 | 8 |
| MISSING_SETTLEMENT | 165 | 165 |
| NETWORK_UNKNOWN | 52 | 52 |
| ORDER_ID_MISSING | 250 | 250 |
| PARTIAL_REFUND | 4 | 4 |
| ROUNDING_DRIFT | 50 | 23 |
| T_PLUS_TWO | 1,117 | 1,117 |
| UNKNOWN_CREDIT | 6 | 6 |
| UNLINKED_ORDER | 57 | 57 |

The data is built to be hard on purpose. Earlier versions scored 99.9% by value and 100%
precision, then hard negatives, failed settlements and split settlement cycles went in and the
headline numbers dropped to what is above, so a matcher still scoring 100% against this data
would mean the data got easier and not that the matcher improved.

Two of the hard negatives are worth knowing, since they are what a naive matcher fails on. A
failed retry shares the order id with the capture that worked and carries no money, so counting
payments per order without reading the status reports 132 duplicates that never happened. And
the stray credits copy a real settlement's amount and date, so the amount fallback finds
something that fits and has to refuse it anyway.

---

## Run it

The deterministic pipeline needs nothing beyond the standard library, but `pytest` is not
standard library so `make test` needs it installed.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Neither command below needs an API key.

```bash
make refusal
```

The confabulating proposer against the adjudicator. 34 groups, 102 proposals, 0 accepted.

```bash
make test
```

108 tests, including the wall between the matcher and the answer key.

`make reproduce` regenerates the data from the seed and re-runs end to end, writing `results/`.
`make dashboard` regenerates the committed `dashboard.html` from what is in `results/` and
`data/`. `make bench` prints the throughput and untuned seed tables, and writes nothing at all
since every dataset it builds goes to a temp directory that is thrown away.

`dashboard.html` opens with no server and no build step, so double click it out of a clone. All
6,368 source rows are embedded in it, because `data/` is gitignored and regenerated.

A keyed run of the agent stage needs the model extra and a `GROQ_API_KEY` or
`ANTHROPIC_API_KEY` in the environment. Without one `make reproduce` still completes and the
report says explanations are unavailable.

```bash
.venv/bin/pip install -e ".[model]"
```

---

## Limitations

The exception list is only honest if this section exists.

`HELD_BACK` reads 0 detected and the arithmetic already knows the answer. Razorpay settles what
adds up to the live balance, so a settlement can name a payment in the export and leave its
money out of the transfer. `reconcile.py:185` computes the gap and `:190` narrows it to the one
payment whose net matches it, verified against the planted labels and all four correct to the
paise, and then nothing flags it, so the run reports that money as arrived when it never came.
That is the worst thing this project can do. It is left this way deliberately, because
`money_missed` and `money_wrongly_cleared` are only falsifiable while something real goes
undetected, and fixing it before the deadline would delete the proof that those two figures
work.

The two zeros in the table are not the same zero. `GATEWAY_OUTAGE` reads 0 because the cascade
never emits that kind so there was nothing to find, and `HELD_BACK` reads 0 for the reason
above, so a table where both look identical would hide the difference.

`ROUNDING_DRIFT` reads 23 of 50 because only those sit on a settlement the matcher can price in
full, and on the other 27 it says nothing rather than compare a group it cannot price.

Drift is the normal case and not an edge case, which is why there is a tolerance at all. A
batch level fee and the sum of the per payment fees disagree because each fee is rounded once:

```
2,000 batches of 40 payments at 200bps      78.2% drifted
                                            worst gap 6 paise, mean 1.47
                                            theoretical bound is n/2, so 20
```

Each fee is within half a paisa of exact, so the drift scales with the number of payments and
not with the amount, and exact matching would reject four correct settlements in five. That
measurement is what the tolerance policy rests on and it is what a naive `==` gets wrong.

There is no tolerance sensitivity curve. The policy rests on the measurement above rather than
on a sweep showing what a tighter or looser tolerance would have cost, so the number is
defended by the bound it comes from and not by what happens either side of it.

Four classes are too thin to measure, `PARTIAL_REFUND` 4, `UNKNOWN_CREDIT` 6,
`CHARGEBACK_LATER` 4 and `MANGLED_UTR` 8. One miss on a four item class is twenty five points,
so those per class rates are noise rather than measurement. The cause is structural, since a
run holds thousands of payments and dozens of settlements and everything rolled per settlement
comes out thin.

The bank charge ceiling is a judgement and not a measurement. A credit short by up to Rs 100 is
treated as the bank's transfer fee and more than that is not guessed at, so a real bank
charging more would have its credits reported as unexplained. That is the safe direction to be
wrong in, but the number was chosen rather than derived.

Nothing has run against real data. Every figure here comes from synthetic data whose problems
this project planted, which is what makes detection measurable and is also the ceiling on what
these numbers prove.

The free tier caps the agent stage at a daily request limit, so `make refusal` uses a scripted
proposer and `live-run-evidence/` exists as the record of real keyed runs.

---

## The repo

```
src/unbundle/  money.py           integer paise arithmetic. Pure, no I/O, no dates
               record_types.py    the five record types, frozen dataclasses
               synthetic.py       seeded synthetic data, and the planted problems
               ground_truth.py    LabelKind, and which kinds are problems vs ordinary
               load.py            reads the CSVs back in
               reconcile.py       the cascade
               evaluate.py        the metrics harness
               diagnose.py        the agent on the residue, and incidents
               refusal.py         the agent against planted lies, for the refusal rate
               run.py             the pipeline end to end, writes results/
               bench.py           the throughput and untuned seed tables
               dashboard.py       renders a finished run as one static dashboard.html

results/               the published run: ledger, report, trace, refusal
live-run-evidence/     real keyed runs, including a rate-limited one
dashboard.html         the run as one page, no server needed
DECISIONS.md           what was decided, why, and what was rejected
FAILURE.md             what broke, what it cost, and what caught it
```
