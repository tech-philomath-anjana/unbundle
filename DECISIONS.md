# Decisions

What was decided, why, and what was rejected. Written as the decision is made, so a
reader can tell a choice from an accident.

---

## Money is integer paise, and nothing else is allowed to represent an amount

**What.** Every amount in the project is an `int` of paise. Every amount enters through one
function, `parse_amount`, and every rate calculation goes through one function that ends in
`// 10000`.

**Why not float.** Measured: round-tripping rupee strings through float and back is wrong on
1,142 of 19,900 amounts, about 5.7%. That is not an edge case, it is one row in eighteen.

**Rejected: `decimal.Decimal`.** It is exact, so it removes the drift. It does not remove the
problem. `Decimal("1000.005")` is a valid `Decimal` and an invalid amount of money, so correct
rounding becomes a discipline that has to be remembered at every call site, and a wrong value
that is exact looks deliberate. Integer paise makes half a paisa unrepresentable rather than
discouraged.

**Rejected: a Money class.** It would wrap the same integer and add a layer to read through.
The single entry point does the work a class would have done.

**One rounding function, half up.** `(amount * rate + 5000) // 10000`. It refuses negatives,
because on -100025 at 2% it returns -2000 where money wants -2001. Floor division rounds toward
negative infinity and money rounds half away from zero, so the two agree above zero and diverge
below it in silence.

**What makes that refusal safe rather than restrictive.** Direction is carried by the field or
the label, never by the sign. A refund is a positive amount with `kind="refund"`. A bank line
has separate non-negative `credit` and `debit`. No legitimate value in this system is ever
negative.

---

## GST is a second rounded step, never folded into the rate

**What.** The fee is `apply_rate(amount, mdr)`, and then GST is `apply_rate(fee, 1800)` on the
result. A combined rate is never used.

**Why.** Measured 31 August, on amounts between Rs 500 and Rs 5,000: a folded rate disagrees
with the two step calculation on 132,750 of 450,000 amounts, 29.5%, always by one paise.
Identical behaviour for 100 basis points against 118 and for 300 against 354.

**Why one test would not have found it.** On a round Rs 10,000 both routes give Rs 118.00
exactly. A single check at a convenient number proves nothing, which is the same failure
recorded in `FAILURE.md` under the tie-out that came to 741 on both sides.

**What it would have cost.** `synthetic.py` and `reconcile.py` both two step and agree, so there
is no live defect. But the tolerance is zero, so a fold on one side would emit roughly a third of
all payments as `FEE_MISMATCH` and not one of them would be real.

---

## Cleaning refuses, it does not repair

**What.** `parse_amount` rejects three decimal places, negatives, and multiple decimal points
at the boundary. It does not round them and it does not strip them.

**Why.** Rounding `1000.005` up invents half a paisa nobody wrote down. Rounding it down
deletes money. A string parser has no basis for choosing between those, and whichever it picks
it will pick silently on every row for the rest of the run.

**Rejected: repair with a warning.** A warning in a log is a decision made by whoever reads the
log later, which in practice is nobody. A rejected row is a decision made now, by a person, on
one row.

**Where the exception is.** Comma grouping lives in the report layer and not in
`format_amount`, because a comma inside a canonical amount string breaks CSV output.

---

## The matcher keeps its own rate card and its own settlement calendar

**What.** `reconcile.py` holds what the merchant believes was agreed, separately from what
Razorpay actually charged, and computes the fee it expects rather than reading the fee it was
given.

**Why.** This is the only reason `FEE_MISMATCH` is detectable at all. A reconciler that reads
the gateway's fee and subtracts it can confirm arithmetic and can never find a payment priced
off the wrong rate. Holding an independent expectation is what turns the tie-out from a
checksum into a check.

**Rejected: trusting `fee` as the truth and only verifying the sum.** That reduces the whole
exercise to confirming that Razorpay can subtract, which was never in doubt.

---

## In flight is a state, not an exception

**What.** A payment captured less than two working days ago is due, not missing. It is reported
in the cash position and never in the exception list.

**Why.** Counting healthy in-flight payments as problems is the cheapest way to make an
exception list look thorough, and it is the reason a merchant stops reading one. In the current
run that is Rs 8,29,496.19 that would have been reported as trouble and is not.

**The general form of the same rule.** `flagged` is what a person has to act on. `observed` is
what the run noticed and nobody needs to do anything about. Keeping them apart is what stops a
batched credit padding the list.

**Rejected: a single "unmatched" bucket.** It is one number, and it hides the only distinction
in the report that a merchant would care about.

---

## Joining the merchant's orders to the gateway

Decided 31 August 2026, built 1 September.

**What.** The merchant's order file joins to the gateway on a cascade, not a single key.
Try Razorpay's `order_id` first. If the merchant never kept it, try the `receipt` they
sent at order creation, which Razorpay returns as `order_receipt`. If neither survived,
the order is unlinked and is reported under `order_ref`, the merchant's own number and
the one identifier it always has.

**Why two keys and not one.** They fail for different reasons. `order_id` is missing
when the integration did not save what checkout returned. `receipt` is missing when the
merchant never set one. A single key would make the whole join depend on one habit.

**Verified against Razorpay's documentation on 31 August 2026.**

```
order_id   mandatory at Standard Checkout, and a payment made without one cannot be
           captured
           razorpay.com/docs/payments/payment-gateway/web-integration/standard/
           integration-steps/#123-checkout-options
receipt    optional at order creation, maximum 40 characters, has to be unique
           razorpay.com/docs/api/orders/create/
both       returned per transaction by the settlement recon API as order_id and
           order_receipt
           razorpay.com/docs/api/settlements/fetch-recon/
```

**Not verified.** The columns of the *downloadable* settlement report. Only the API
response was read, and the dashboard docs do not enumerate the file's columns.

**Rejected: a third route on `payment_id`.** It arrives in the same checkout response as
`order_id` and is saved by the same code, so it goes missing whenever `order_id` goes
missing. A fallback that fails exactly when the primary fails buys nothing.

**Rejected: a fourth route through `notes`.** Razorpay's `notes` is up to fifteen
arbitrary key value pairs with no schema, so there is no way to know which key holds an
order number or whether one does. Searching it is a proposal for a person to confirm,
not a join, and a wrong link is the one outcome this project refuses.

---

## An unlinked order is named, not dropped

**What.** `Order.order_ref` is required and never null, where `order_id` and `receipt`
are both nullable.

**Why.** An order that joins to nothing still has to appear in the report, and a report
cannot say "one order is unlinked" without saying which. Had the model carried only the
two gateway keys, an order missing both would have no identifier at all.

**Rejected: making `order_id` the primary key.** Razorpay always has it, which is what
makes it tempting. But the merchant only has it if their integration saved it, and the
model has to hold what the merchant's file actually contains.

---

## The proposer is one injectable function

**What.** `explain()` takes a `propose` callable. The Anthropic client is built inside
`_anthropic_propose()` and returns `None` when no key is set.

**Why.** Three things fall out of it. The run degrades to deterministic-only rather than
failing when no key is present. The adversarial check substitutes a deliberately greedy
proposer and needs no key at all, so anyone who clones the repository can reproduce it.
And the model is swappable without touching the adjudicator, which is what makes the
claim that arithmetic decides rather than the model something a reader can check.

**Rejected: calling the client directly inside `explain()`.** It would have made the
adversarial run impossible to write without network access or mocking, and the no-key
path would have been an exception handler rather than a designed state.

---

## The agent revises, it does not get one shot

Decided 1 September 2026.

**What.** A rejected proposal goes back to the proposer with the adjudicator's reason
attached, up to three turns, then the group is recorded as undiagnosed.

**Why.** A single call is a classifier, not an agent. With the reason fed back the model
acts on what its environment told it, and can call `give_up` once it learns the records do
not support anything, which is where a refusal rate comes from at all.

**Why the reason travels inside the evidence and not as a new argument.** The `Propose`
signature stays as it was, so a proposer written before the loop existed still runs. The
greedy stub used by the adversarial check is exactly such a proposer.

**Rejected: unbounded retries.** Three turns is enough to show whether feedback helps.
Without a cap a proposer that ignores feedback, which is precisely the adversarial case,
would never terminate.

**What this is not, stated plainly.** The three calls are not three turns of a conversation.
The rejection is stitched into the prompt text, so the model does not see its own prior
answer. The behaviour is right and the description in the notes was not. Recorded in
`FAILURE.md`.

---

## Refusal is adjudicated, not requested

**What.** The model is never asked whether it is confident. It names a cause and cites record
ids, and `_adjudicate` re-derives that cause from the records. A cause the citations do not
support is rejected whether or not the model was willing to admit doubt.

**Why.** Asking a model to report its own uncertainty makes the refusal rate a property of the
model's manner rather than of the evidence. A model that hedges looks careful and a model that
does not looks reckless, and neither tells you whether the answer was true. Checking the
citation makes refusal a property of the data.

**What it buys, measured.** A deliberately greedy proposer that answers `GATEWAY_OUTAGE` to
every group and ignores every correction gets 34 of 34 groups refused across 102 proposals,
with nothing accepted at all. That number exists because refusal does not depend on the
proposer's cooperation.

**Rejected: a confidence score in the reply.** It is a number the model chooses, about itself,
with nothing behind it. The adjudicator's reason is a sentence about the records.

---

## The cause set is closed, and an outage needs five payments

**What.** Five causes: `GATEWAY_OUTAGE`, `BANK_TRANSFER_CHARGE`, `RATE_CARD_MISMATCH`,
`SETTLEMENT_NEVER_SENT` and `SETTLEMENT_FAILED`. A cause outside the set is rejected rather
than recorded. `OUTAGE_MINIMUM = 5` inside a four hour window on one payment method.

**Why a closed set.** Choosing one of five is something a small model does reliably. Composing
an honest paragraph about its own uncertainty is not. A model that fits none of them calls
`give_up` with its own reason, so an answer outside the set has somewhere to go that is not
invention.

**Why five payments and not two.** With a hundred scattered misses across a month, two landing
in the same window on the same method is coincidence. Calling that an outage is exactly the
confabulation the adjudicator exists to stop, so the threshold has to sit above the noise the
data actually produces.

**Rejected: free text causes.** Unverifiable by construction. There is no check to write for a
sentence, so every answer would have to be believed.

---

## What was not built, and why

**Forecasting.** Agent Studio ships a Cashflow Forecaster, a 3 to 7 day cash prediction.
Building one invites a comparison with a shipped product on their own ground, and the cash
position this project already reports, received against in flight against at risk, is the part
a settlement reconciler is uniquely placed to know.

**MDR on zero-MDR UPI.** Razorpay charges a platform fee on UPI by published policy, so a
detector for it would manufacture false positives against correct behaviour. Dropped rather
than left half built.

**`DUPLICATE_PAYMENT`, `PARTIAL_REFUND`, `CHARGEBACK_LATER`.** Understood well enough to build
and deliberately not built. The reasoning for each, and what each would cost, is in `LATER.md`.

**The hard negative that matters if duplicates are built later.** The case that tests detection
is the failed retry, not the repeat purchase. A repeat purchase gets a different `order_id` and
never collides. A failed attempt shares the `order_id`, which is what makes it the case worth
generating.

---

## An unknown card network is not priced, and the fee check abstains

**What.** Both rate cards are rekeyed to Razorpay's seven documented `card_network` strings, and
`unknown` is deliberately absent from both. The generator prices a card payment at its real
network's rate and then rolls whether the exported column reads `unknown`. `reconcile.py` has no
agreed rate for `unknown`, so `expected_fee` returns nothing and the fee check abstains for that
payment. It is recorded as `NETWORK_UNKNOWN`, which is ORDINARY, observed and never flagged. An
assert beside each rate card holds the six priceable keys plus `unknown` equal to the declared
type.

**Why the vocabulary was wrong.** `record_types.py` declares `Visa`, `MasterCard`, `RuPay`,
`American Express`, `Diners Club`, `Maestro`, `unknown`. Both rate cards were keyed on `visa`,
`mastercard`, `rupay`, `amex`, `diners`, `maestro`. Not one of the seven declared values was a
key in either dict, so every card payment from a real export would raise `KeyError`. It worked
only because `NETWORK_WEIGHTS` emitted the same lowercase short forms the dicts expected, so the
generator and the matcher agreed with each other and neither agreed with the type. Watched:
`rate_for("card", "Visa", "credit")` raises `KeyError: 'Visa'`.

**Why the export is masked rather than the network being unpriceable.** Razorpay's recon report
documents `fee` as the fees charged to process the transaction, with no stated dependency on
`card_network`, so a row can read `unknown` and still carry a real charged fee. The page never
says why the value appears and publishes no rate for it. Pricing at the true rate and damaging
only the exported column is the one version that does not invent a rate Razorpay has never
published. That the payment really was priced at some identifiable network's rate is an
assumption, marked as one, the same way the 3 percent credit EMI rate is. It is also the shape
`MANGLED_UTR` already uses, where the underlying fact is sound and the export is damaged.

**Why the check abstains rather than raising.** A reconciliation tool that dies on a documented
value cannot be run against a real file, and one unpriceable row is not a reason to fail every
other row in the batch alongside it.

**Why it abstains rather than falling back to a rate.** Any fallback rate is invented, and an
invented rate produces an expected fee, which produces a `FEE_MISMATCH` against a payment
Razorpay priced correctly. The merchant is then sent to argue a pricing dispute that does not
exist, the same shape as a blank `card_type` once silently taking the credit rate, recorded in
`FAILURE.md`.

**Why the generator still records drift the matcher cannot confirm.** A batch holding a masked
payment can drift truly while the matcher has no rate to check it with, so `ROUNDING_DRIFT`
detection falls below 100 percent. The generator records the drift anyway, because an answer key
edited to skip what the matcher cannot see is the matcher's blind spot copied into the truth,
which is the reason the truth is recorded as the data is made rather than worked out afterwards.
The lost detection is the real cost of the masked column and belongs in the numbers.

**Why not the agent.** The rule is that the model only works cases where its answer can be
arithmetically refuted, and the thing that would refute a guess at the agreed rate is the rate
that is missing. The half that is recoverable needs no model: `amount` and `fee` give the charged
rate back exactly, since `fee` is the MDR plus GST on it. What is gone is what was agreed, and
nothing recovers that.

**Rejected: normalising at the boundary.** A function folding `American Express` down to `amex`.
`record_types.py` copies Razorpay's strings so a real export loads without a normalising step,
and a normaliser is a second place the vocabulary is written down and so a second place it can
drift from the first.

**Rejected: changing the type to match the dicts.** The type mirrors the source, and the source
is what a real export actually contains.

**Rejected: one shared rate card.** The two dicts are separate on purpose. The matcher holds what
the merchant believes was agreed and the generator holds what was charged, and merging them makes
`FEE_MISMATCH` undetectable by construction.

**Rejected: a rate for `unknown`.** Covered above, and it is the change a reader will reach for
first, which is why the assert names the absence rather than leaving it to be noticed.

---

## The answer key is recorded as the data is made, not worked out afterward

**What.** `ground_truth.py` writes a label the instant `synthetic.py` plants the defect that
earns it. Nothing re-reads the finished dataset afterward to work out what should have been
flagged.

**Why.** A truth worked out afterward is a second matcher, built by the same hand as the first,
looking at the same output the first one produced. It would share the first one's blind spots
exactly where sharing them matters most, and a matcher graded against its own reasoning is not
being checked.

**Rejected: deriving the answer key from the finished dataset.** It looks like the more careful
approach, since it works off the data instead of trusting a note taken mid-generation, and it is
the opposite. A label attached at the moment a defect is planted records a fact about intent, and
a label worked out afterward records only what a second pass of the same logic concludes, which
is not independent of the first pass at all.

---

## The provider is Groq's free tier, not a paid one

**What.** The published run answers through `openai/gpt-oss-120b` on Groq's free tier.
Anthropic sits behind the same `Propose` interface as a fallback, reachable with a key, but the
run this project publishes needs none.

**Why free over paid.** Groq's stated policy is that it does not retain customer data for
inference requests, with retention limited to batch and fine-tuning jobs and abuse logs capped
at 30 days with an opt-out. That is a stronger privacy position than a gateway processing a
merchant's transaction data has any obligation to accept casually, and it costs nothing to get.

**Rejected: Gemini's free tier.** It offers a response schema, which would have removed a JSON
parsing step from the proposer. Its own pricing page states that free tier content is used to
improve Google's products. Removing one parsing step is not worth handing a merchant's
transaction data to that policy.

**Rejected: paying for Anthropic.** A keyed run costs about three paise. The obstacle was never
the money, it was a five dollar minimum top-up standing between a working key and a run that
costs a fraction of a rupee. Free removes a step a reader would otherwise have to take on trust.

**Rejected: NVIDIA's hosted endpoints.** Free and reachable, but the model available there
offers no structured output and its trial terms log inputs, which is the Gemini objection again
from a different vendor.

**What this buys a reader.** `make refusal` and the deterministic pipeline need no key at all.
A keyed run of the agent stage is the only thing gated on Groq specifically, and pointing that
one call at Anthropic instead is a configuration change, not a rewrite, which is what makes the
claim that the provider is a choice rather than an architecture something a reader can check
rather than take on faith.

---

## The published run is 3,000 orders, not 5,000

**What.** `run.py` and `refusal.py` both default to 3,000 orders. The seed and every published
figure are from that size, not the 5,000 orders used earlier in the project's own exploration.

**Why.** Read off Groq's console for `openai/gpt-oss-120b` on the free tier: 30 requests a
minute, 1,000 a day, 8,000 tokens a minute, 200,000 tokens a day. At roughly 1,650 tokens a call:

```
5,000 orders   80 groups   264,000-396,000 tokens   does not fit in a day, on any account
3,000 orders   34 groups   112,000-168,000 tokens   fits, 13 to 19 minutes
2,000 orders   27 groups    89,000-133,000 tokens   fits, but weakens HELD_BACK further
```

**Rejected: 5,000 orders.** Not a close call, and not only arithmetic. A keyed run at this size
was actually tried and lost 69 of its 80 groups to exactly this ceiling, kept as
`live-run-evidence/rate-limited-run/` rather than deleted, because a run that failed is stronger
evidence for a limit than the math predicting it would. 80 groups at up to three turns each can
reach 396,000 tokens against a 200,000 token daily cap, so this is a fact about the tier, not a
bug in the loop, and not a close call to have run again.

**Rejected: 2,000 orders.** It fits inside the daily budget with more headroom than 3,000 does,
and it was rejected anyway. `HELD_BACK` is already one of the thinnest classes in the exception
list at 4 planted, and scaling down to 2,000 orders draws only 2 of it. A class too thin to
measure at 4 is not made more measurable by halving it again, and `HELD_BACK` is the class the
two safety figures, `money_missed` and `money_wrongly_cleared`, depend on existing at all.

**What this makes provable.** A keyed run at 3,000 orders takes 13 to 19 minutes on a free key,
which is the reproducibility claim in the README: a reader with no paid account can clone this
and watch the same agent stage run to completion rather than take the published trace on trust.
