# Failures

What broke, what it would have cost, and what caught it. A project that reports honestly on
a merchant's money has no standing to report dishonestly on itself.

Ordered by what each one cost, not by date. Started 1 September 2026 and carried to the fifth.

---

## The bug was in the first guard clause of the most drilled file

**What.** `parse_amount` is the one door every amount in this project enters through, and
`money.py` had thirty tests on it. It carried a live bug through fourteen commits. The
`startswith("-")` guard read the whole string, so it never saw a sign sitting after the dot, and
`int()` was trusted to refuse things `int()` does not refuse.

```
'0.-5'  ->    -5      a negative Paise out of the function the negative invariant rests on
'1.-5'  ->    95      silently wrong
'+5'    ->   500      leading sign accepted
'1_0'   ->  1000      int reads _ as a digit separator, so a mangled cell becomes Rs 10.00
'١٢'    ->  1200      non ASCII digits accepted
```

**Why thirty tests missed it.** They were about boundaries and refusals, the values at the edges
of what the function should accept. None of them asked what the parser does with a string that is
not a number in the shape it expects.

**How it was caught.** By running the five shapes rather than reading the guard. `isdigit()` alone
still passes `'١٢'`, which is why the fix is `isascii() and isdigit()` and why reading the code
would not have settled it.

---

## A retry built from the first error in the trace

**What.** The agent stage was losing groups to rate limits, so the retry read the wait out of the
message with `try again in ([0-9.]+)s` and slept it off. That pattern came from the first 429 in
the trace. Counted across the whole captured run:

```
215 rate limits on tokens per day
  3 rate limits on tokens per minute
```

The daily ceiling states its wait as `4m59.808s`, which that pattern matches none of, so the retry
fell back to a backoff of its own with no relation to what the provider had asked for.

**Why waiting was the wrong answer even when it worked.** Tokens spent for the day do not come
back. The daily waits run four to nine minutes and grow, so six retries per group across 34 groups
is hours spent arriving at the same failure.

**What it cost.** A 5,000-order run lost 69 of its 80 groups. It is kept as
`live-run-evidence/rate-limited-run/` rather than deleted, because it is the measurement.

**The fix.** `_is_daily_limit` splits the two. Per minute waits and retries, per day reports at
once, so somebody who clones this and runs `make reproduce` twice in a day is told the budget is
gone instead of waiting out an hour of it.

---

## A test count you did not watch run is not a test count

**What.** The notes said `6 failed, 31 passed` for most of a day. The suite was not running at
all. `model.py` had been renamed to `record_types.py` and four importers still pointed at
`unbundle.model`, so pytest aborted during collection. Even `test_money.py`, which had nothing
to do with the rename, never executed.

**Why nothing caught it.** The six failures were deliberate, planted the day before by adding
required fields without wiring the construction sites. A red number was expected, so the red
number was not read. Being comfortable with a failing count is how you stop looking at it.

**What it cost.** A day believing the pipeline was six edits from green while no test in the
project had run.

**The rule.** A number quoted from memory is not a measurement. Run it, watch it finish, then
write it down.

---

## A test that passed and proved nothing

**What.** A batch tie-out where both sides came to 741. The batch total had been mis-added, and
the rounding on the other side landed on the same wrong figure.

**How it was caught.** By distrusting a round number that agreed too neatly. The amounts were
fixed, and then 2,000 batches were measured rather than argued about: 78% drift, worst gap
6 paise.

**The rule.** One passing case at a convenient value is a coincidence with a green tick on it.
The fix for a test you do not trust is a measurement across a range, not a second test at the
same value.

---

## A scoring harness with no reason to be trusted

**What.** `evaluate.py` produces every accuracy number in this project, and nothing checked it.

**How it was caught.** By feeding it a deliberately perfect matcher. If an input that gets
everything right does not score 100%, the harness is wrong and the input is fine. It scored
wrong. Two bugs, both in the file every published figure comes out of.

**The rule.** A measuring instrument needs a known input before its readings mean anything.
The perfect matcher took twenty minutes to write and is the reason the numbers can be quoted.

---

## A gate built for one cause was never copied to the two it was modeled on

**What.** The adjudicator gained a rule for `SETTLEMENT_FAILED`: accept a citation only if
every member's own settlement actually failed. `RATE_CARD_MISMATCH` and `SETTLEMENT_NEVER_SENT`
had been checking the arithmetic alone since they were first built, and the same rule was never
added to either, though both were built on the same shape.

**What it would have let through.** Reproduced at 3,000 orders, the published configuration, by
hand-building a citation the model itself never sends: `RATE_CARD_MISMATCH` accepted inside a
`MISSING_SETTLEMENT` group, twice, and `SETTLEMENT_NEVER_SENT` accepted inside a `FEE_MISMATCH`
group, once. Three verdicts the ledger would have called correct on a cause naming the wrong
problem.

**Why the published run was safe anyway.** The model is told to cite every member of a group and
it never cites a subset, so a cross-kind accept needs a citation naming records from a kind the
group was not built from, which only a hand-built proposal produces. Checked rather than assumed:
all 28 accepted verdicts in the published run were re-adjudicated under the new gate, 28 of 28
still accepted, 0 newly refused.

**How it was caught.** An outside review, not a test already in the suite. Two guards, the same
shape as the one already sitting on `SETTLEMENT_FAILED`, plus two tests watched failing against
the unfixed code first.

**The rule.** A safeguard added for one case is not a safeguard until it is added for every case
built the same way, and the two it was modeled on are exactly the two most likely to need it.

---

## The bug that would not have crashed

**What.** The first draft treated Razorpay's `fee` and `tax` as separate deductions. Their
payment entity says `fee` is the "fee, including GST, charged by Razorpay". Subtracting tax
again removed roughly Rs 46.44 per settlement that was never charged.

**Why it is the worst kind.** Nothing raises. Every tie-out fails by a small, consistent,
plausible amount. That is precisely the shape of error a person fixes by widening a tolerance,
which buries the real defect under a number that now looks fine.

**How it was caught.** By reading the entity documentation rather than assuming two fields
named `fee` and `tax` are two charges.

**Why the tolerance had been hiding it.** A rounding tolerance was already in place for a
different reason, and it was wide enough to absorb Rs 46.44 without a single tie-out failing.
The tolerance was decorative until fee verification went back in beside it. With both checking
together, a correct matcher does not drift against this data at all, which is what makes the
78% drift rate reported elsewhere a property of rounding and not a residue of this bug.

---

## A short row loaded as a healthy payment that never settled

**What.** `csv.DictReader` pads a row missing trailing columns with `None`. `settlement_id` is
the last column in `payments.csv`, and `None` there already means this payment was never
settled, so a row truncated by one column loaded as a payment reported at risk that had, in
fact, arrived. Nothing raised.

**Why it is worse than a crash.** No exception, no obviously wrong total, just a payment moved
into the wrong bucket by a boundary condition nobody had reason to suspect. A merchant reading
the report would be told money is missing that already arrived, or the other way round,
depending on which column fell off the row.

**Why nothing had caught it.** No test in the suite imported `unbundle.load` before this. The
CSV round trip is the one property the file exists to guarantee, and only `make reproduce` ever
exercised it, with no assertion attached to the result.

**The fix.** `load.py` now refuses six damaged shapes rather than silently accepting any of
them: a bad cell, a short row, a long row, a renamed column, a negative amount, an unparseable
date. Row numbers count from 2, matching what a spreadsheet shows, checked against a file
damaged at row 43 and not only at row 2.

**How the fix was checked in both directions.** Two tests, both watched failing first: removing
the short-row guard does not raise, and tightening it wrongly raises on a legitimate empty cell.
A guard proven only against the failure it stops is half proven, since the likelier drift is
someone writing it too strictly and rejecting real data.

**The rule.** A boundary a reader has no reason to suspect is exactly the one nothing is
testing.

---

## Three bugs from the same root, all in what a duplicate inherits

**What.** A duplicate capture is built from the first one's fields, and each inherited field
needed its label inherited with it. Two did not. The fee: a second capture overcharged by the
same amount as the first carried no `FEE_MISMATCH` label, so the matcher's correct flag on it
scored as a wasted investigation. The exported network: a duplicate on a payment whose network
reads `unknown` was not labelled `NETWORK_UNKNOWN`, understating that class by one on the
default seed. A third bug shared the mechanism rather than the field: `_utr` numbers
settlements consecutively, so a digit swapped in to damage one settlement's reference could
land on a neighbour's real UTR whenever the two shared a settlement date, which at three cycles
a day is two pairs in three. That one accounted for three of the four wasted investigations in
the run.

**How it was caught.** By reading what a duplicate inherits, field by field, and asking which
label came with it, not by running the suite, which had nothing checking the answer key against
itself.

**The fix.** The fee case does not appear on the default seed at all, expected about once every
two runs, and was watched on a seed that draws it. The network case is the default seed's own
72-against-73. The UTR case is `_swapped_digit`, which steps the last digit until it names no
settlement and raises if all ten are taken, consuming no randomness so the rest of the dataset
is unchanged. Precision moved 98.8% to 99.1%, match by count 81.5% to 83.1%, by value 89.5% to
90.4%.

**The rule.** A duplicate is not a copy of one field, it is a copy of every field, and the label
has to travel with the ones that were wrong the first time too.

---

## A fact verified in a notes file is not a fact verified in the code

**What.** `OrderStatus` allowed `placed`, `paid`, `cancelled`. Razorpay allows `created`,
`attempted`, `paid`. The correct values were verified on 22 August and written into the notes,
and something else was typed into the code eight days later.

**Why nothing caught it for eight days.** Three defences failed at once. `Literal` enforces
nothing at runtime. The generator only ever writes `"paid"`, which is legal in both lists. And
no code reads `Order.status` at all.

**The rule.** A field nothing reads is a field nothing checks, and a document is not a test.

---

## Results that could not be explained

**What.** A `results/` directory was committed on 30 August. The code that produced it was
never committed. When the pipeline was rebuilt on 1 September every planted count differed,
and there is no way to reconstruct why.

**What it cost.** A set of published numbers that had to be thrown away rather than defended,
and the sharpest argument in this project for getting source into the repository early.

**The rule.** Output without the code that made it is not a result. It is a screenshot.

---

## The report claims a run that does not exist

**What.** `results/report.md` says "a full run with explanations is committed in this
directory". There is no such run. The keyed path has never executed, and the same file says,
four lines earlier, that the agent stage was skipped because no API key was set.

**Why it matters more than its size.** This is the one artefact a reader opens first, in a
project whose entire claim is honest reporting, and it contradicts itself on the same screen.
No arithmetic is wrong. The sentence is.

**How it was caught.** By reading the committed output as a stranger would, rather than as the
person who knew what it meant to say.

---

## Detection reads 100% on most classes and I wrote both halves

**What.** Fourteen of the seventeen reported classes read planted equal to detected. The same
author wrote the generator that plants the defects and the matcher that finds them, so
agreement between the two measures one fault model checking itself, not correctness against
data neither of them produced.

**Why the other three do not rescue the argument.** `GATEWAY_OUTAGE` reads 0 of 10 and
`HELD_BACK` reads 0 of 4 because neither is wired to be caught at all, and `ROUNDING_DRIFT`
reads 23 of 50 because the matcher abstains on a settlement it cannot price rather than guess.
None of the three is evidence against the closed loop below. They are evidence the matcher is
honest about where it cannot see, which is a different claim answering a different question.

**Why a fresh seed does not fix it either.** A different seed of the same generator plants the
same kinds of defect in different places. The closed loop is in the fault model, not in the
random numbers.

**What would actually test it.** Data this project did not generate, or a defect class written
by somebody else. Neither exists here.

**Stated rather than solved.** A hundred percent that cannot be wrong is not evidence, and the
honest move is to say so before a reader works it out, on the fourteen classes it still
applies to.

---

## The loop is not the conversation the notes describe

**What.** `MAX_TURNS = 3` and the notes call it a three turn loop where a rejected proposal
goes back to the model. Every call ships `messages=[{"role": "user", ...}]`, a single user
message. `_with_rejections` staples the rejection history into the prompt text instead. The
model never sees its own prior answer, so these are three stateless calls against a growing
string, not three turns of a conversation.

**Why the design is still right.** Keeping the rejection inside the evidence is what holds the
`Propose` signature stable, which is what lets the adversarial proposer run unchanged with no
key. The defect is in the description, not the architecture.

**The rule.** Describe what the code does, not what the design intends. The gap between those
two is where every overclaim in this file started.

---

## A blank column becomes the expensive rate

**What.** The matcher priced EMI payments with `100 if card_type == "debit" else 300`. A blank
`card_type` in an export loads as `None`, which is not `"debit"`, so it silently took the
credit rate. Three times the fee, no error.

**What the merchant would have been told.** `FEE_MISMATCH` against a payment Razorpay priced
correctly. The finding names a pricing dispute when the real defect is a missing column, so
somebody is sent to argue a case that does not exist.

**Why it happened.** Two states were handled where three exist: debit, credit, and not known.
Every other rate in the same function was a dict lookup that raises on an unknown key. One
ternary, one opposite failure mode.

**The fix.** `reconcile.py` now looks the network up in a dict and returns `None` on a blank
column instead of guessing, and the fee check abstains rather than accuse on a payment it
cannot price. The generator side of the same shape is closed too: `synthetic.py` raises on a
blank `card_type` instead of defaulting, since a blank there is a bug in the generator and
never a fact about a real export.

---

## The notes drifted from the run again

**What.** The working notes once said `GATEWAY_OUTAGE 14/14/0` and 160,861 records per second.
The run actually committed in this repository reads `10 | 0` and 146,694 records per second.

**Why it is the same failure as the test count.** Both numbers were true of some run. Neither
was checked against the run that is actually in the repository before being written down as
current.

**The fix that holds.** The run is the source. Any figure in a notes file is a copy, and a copy
is stale the moment the pipeline is executed again.

---

## A pytest.raises block stops at the first raise

**What.** Three assertions inside one `pytest.raises` context. The first one raises, the block
exits, and the remaining two never execute. Three tests on paper, one test in fact.

**The rule.** A test that cannot fail is not passing. It is absent.

---

## The gate and the deadline stopped pointing the same way

**What.** A file goes into the repository when every line in it can be defended out loud. That
rule produced the two best files in the project. It also left seven working modules untracked
with four days to an application deadline, so the public repository holds eight files and none
of them reconciles anything.

**Why the rule was right and the timing was not.** The discipline was aimed at understanding
the code. The deadline is judged on what a stranger can clone and run. Those two goals pointed
the same way in August and stopped some time around the point the pipeline started working.

**What was done about it.** The gate was kept and the scope changed. The code goes in, and the
README says which files have been drilled line by line and which have not, which is more
honest than an empty repository rather than less.

---

## One credit is a tenth of the month, so a headline number swings on one row

**What.** On seed 20260828, `setl_00015` holds 494 payments and Rs 12,97,922.96, against a
total credit value near Rs 1.24 crore. It drew two independent damages at once, a truncated
UTR and a Rs 45.57 bank transfer charge. The UTR step fails because the reference is cut, and
the amount fallback fails because at zero tolerance the credit is short. Nothing in the cascade
survives both, so the credit went unmatched and took ten points off match rate by value on its
own, from 99.9 to 89.5.

**Why one settlement is that large.** `_build_settlements` groups payments by settlement date,
so one date is one settlement, and T+2 working days piles Friday, Saturday and Sunday captures
onto Tuesday. That is faithful rather than wrong. The consequence is that this data has a few
very large credits and match rate by value is hostage to them.

**Why nothing caught it.** Every earlier run left that credit intact, so the metric had never
been asked to survive losing one. A number that has only ever been observed in the good case
has not been tested, it has been watched.

**How it was found, and the near miss inside it.** Precision fell from 100 percent to 99.6 and
was chased rather than waved at. `ROUNDING_DRIFT` and `BATCHED` had each fallen by one at the
same time, and a change made that day had been predicted to cost `ROUNDING_DRIFT` exactly one
detection. The prediction came true and was wrong. `BATCHED` has nothing to do with rates, and
both classes are only emitted after a credit is claimed, so one unmatched credit subtracts one
from each. The change under suspicion had cost nothing at all. A prediction confirmed by the
right number for the wrong reason is the same failure as a test that passes and proves nothing.

**What it cost.** Nothing yet, which is the point. It was found before the untuned-seed run on
2 September rather than after publishing the drop it will produce.

**The rule.** Publish the variance beside the number. On this data a match rate by value is
partly a statement about whether one Tuesday credit got damaged twice, and a reader given the
figure alone will read a lumpy benchmark as a fragile matcher.

---

## A sample that was concealing a false label

**What.** The dashboard's agent section showed three hand-picked cards under a headline reading
28 accepted, 19 rejected, 4 gave up. Nothing on the page said it was a sample, so a reader
counting down the page found 2 where they had been told 28. The slice took the first two groups
that needed more than one turn, which is exactly the flattering pair, the cases where the model
corrected itself.

**What the sample was hiding.** Rendering all 34 exposed a false label. The turn renderer was an
if/else chain whose last branch printed **gave up** for anything it did not recognise, and two
turns in the trace are provider-side 400s that the loop retried. The page was saying the model
declined to name a cause on turns where it had never been asked for one. `_parse_trace` had always
classified those correctly, so only the renderer was wrong.

**Why it matters more than its size.** A subset chosen to save space had become a claim, and the
claim was wrong in the direction that flatters. The label became visible only because the sample
was removed, so the page now renders all 34 and hides them behind a toggle instead.

---

## Escaping that never ran

**What.** `dashboard.html` embeds the whole run as JSON inside a `<script>` tag. A cell holding the
closing script sequence ends the element early and kills every function on the page. The escaping
written to prevent exactly that runs at render time, which is too late, because the browser never
gets that far.

**How it was caught.** By rendering a page with a real payload sitting in a narration and watching
the script fail to parse. Reading the code would have shown escaping that looked correct, because
it was correct, and in the wrong place.

**The rule.** A defence that runs after the parser is not a defence. Put the payload in and look.

---

## Still open

The defects still in the shipped run are listed in the README, under Limitations, rather than
repeated here. A defect recorded in two files goes out of step with itself, and this file has
already caught that happening twice with numbers.

The one worth naming in both places is `HELD_BACK`. The arithmetic already finds the money,
`reconcile.py` computes the gap and narrows it to the single payment whose net matches, verified
against the planted labels and correct to the paise on all four, and then nothing reports it. So
the run says that money arrived when it never came, which is the worst thing this project can do.
It is left that way deliberately, because `money_missed` and `money_wrongly_cleared` only mean
anything while something real is going undetected, and fixing it before the deadline would delete
the proof that those two figures work.

The rest, with the reasoning for leaving each one, are in `LATER.md`.

---

## What did not break

The agent stage degrades rather than fails. No key, an unparseable reply, or any exception,
and the deterministic pipeline still completes and the report says explanations are
unavailable. That was designed on the first day and has never been the thing that went wrong.
