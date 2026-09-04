# Reconciliation report

Run of 200 orders on seed 20260828, as of 2026-08-28.

## Cash position

Of the Rs 4,58,630.22 captured in this window, every payment ends in exactly one of these four and the run fails if they do not add up.

- Rs 4,27,154.08 received, settled and confirmed by a bank credit
- Rs 13,754.26 in flight, due but not arrived yet
- Rs 0.00 settled by Razorpay with no bank credit matched to it, so it is neither confirmed nor known to be missing
- **Rs 17,721.88** at risk, captured and past due with nothing settled

## Money at stake

Each line is a different kind of exposure and they are not added up, because a credit that arrived unattributed and a payment owed back to a customer are not the same money.

- Rs 27,953.72 credited to the bank that this run cannot tie to any settlement or order
- Rs 17,721.88 captured and past due with nothing settled
- Rs 2,095.25 of orders that tie to no payment at all
- Rs 23,719.62 charged to customers twice and owed back
- Rs 187.85 of fees with no published rate to check against
- Rs 7.97 charged above the agreed rate card
- Rs 478.74 taken by the bank on the transfers

## What this run may have got wrong

- Rs 1,404.89 of money due that this run did not surface. Reads the missing settlement class alone, so the two lines below cover the seven other kinds
- Rs 1,727.47 reported as reconciled that was not
- 3 planted problems the run never named, across all eight kinds. The detection table below says which
- Rs 1,404.89 sitting on records the run noticed under no kind at all, counted once per record rather than once per label
- Rs 187.85 of fees charged on payments whose card network the export does not name, so no published rate exists to check them against

## What landed

- 55 of 63 bank credits explained (87.3% by count, 93.4% by value)
- 52 findings, of which 0 would have wasted your time
- 0 healthy in flight payments wrongly reported as a problem
- Rs 2,095.25 of orders that tie to no payment at all
- 119,646 records per second
- 20 of those findings were given a cause the agent stage verified against the records, covering Rs 1,13,027.09 of turnover, of which Rs 0.00 is money whose disposition the verdict decides, wait for a gateway to recover or escalate it

## What to look at

### BANK_TRANSFER_CHARGE  (20 findings)

20 credits. 20 credits short by a transfer charge.

### No cause determined  (1 groups)

Grouped, investigated, and left undiagnosed rather than guessed at.

- 4 credits. No cause fits: all settlements are processed with full claimed credit and zero shortfall, so none of the listed causes apply.

### 21 individual findings, no shared pattern

Rs 17,166.24 across findings that grouped with nothing else.

## Proposals the arithmetic rejected

The model proposed 2 causes. 1 were rejected because the cited records did not support them.

- upi captured 2026-07-31 between 20:00 and 24:00: cited setl_00001, not in the group

## Detection by class

| class | expected | detected |
|---|---|---|
| BANK_FEE_DEDUCTED | 20 | 20 |
| BATCHED | 44 | 44 |
| CHARGEBACK_LATER | 6 | 6 |
| DUPLICATE_PAYMENT | 3 | 3 |
| FAILED_RETRY | 11 | 11 |
| FEE_MISMATCH | 1 | 1 |
| GATEWAY_OUTAGE | 0 | 0 |
| HELD_BACK | 3 | 0 |
| MANGLED_UTR | 4 | 4 |
| MISSING_SETTLEMENT | 14 | 14 |
| NETWORK_UNKNOWN | 4 | 4 |
| ORDER_ID_MISSING | 23 | 23 |
| PARTIAL_REFUND | 2 | 2 |
| ROUNDING_DRIFT | 29 | 26 |
| T_PLUS_TWO | 91 | 82 |
| UNKNOWN_CREDIT | 8 | 8 |
| UNLINKED_ORDER | 2 | 2 |

GATEWAY_OUTAGE reads 0 detected above because the cascade never emits that kind. The agent stage separately verified 0 of the 0 planted, checked against the answer key and not just accepted on the model's say.
