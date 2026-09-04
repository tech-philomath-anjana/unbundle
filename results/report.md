# Reconciliation report

Run of 3000 orders on seed 20260828, as of 2026-08-28.

## Cash position

Of the Rs 79,11,875.11 captured in this window, every payment ends in exactly one of these four and the run fails if they do not add up.

- Rs 69,23,256.83 received, settled and confirmed by a bank credit
- Rs 6,08,596.16 in flight, due but not arrived yet
- Rs 0.00 settled by Razorpay with no bank credit matched to it, so it is neither confirmed nor known to be missing
- **Rs 3,80,022.12** at risk, captured and past due with nothing settled

## Money at stake

Each line is a different kind of exposure and they are not added up, because a credit that arrived unattributed and a payment owed back to a customer are not the same money.

- Rs 4,82,723.68 credited to the bank that this run cannot tie to any settlement or order
- Rs 3,80,022.12 captured and past due with nothing settled
- Rs 1,49,390.05 of orders that tie to no payment at all
- Rs 1,34,431.60 charged to customers twice and owed back
- Rs 3,487.21 of fees with no published rate to check against
- Rs 1,098.10 charged above the agreed rate card
- Rs 360.47 taken by the bank on the transfers

## What this run may have got wrong

- Rs 5,177.87 of money due that this run did not surface. Reads the missing settlement class alone, so the two lines below cover the seven other kinds
- Rs 9,396.90 reported as reconciled that was not
- 14 planted problems the run never named, across all eight kinds. The detection table below says which
- Rs 5,177.87 sitting on records the run noticed under no kind at all, counted once per record rather than once per label
- Rs 3,487.21 of fees charged on payments whose card network the export does not name, so no published rate exists to check them against

## What landed

- 54 of 60 bank credits explained (90.0% by count, 93.3% by value)
- 316 findings, of which 0 would have wasted your time
- 0 healthy in flight payments wrongly reported as a problem
- Rs 1,49,390.05 of orders that tie to no payment at all
- 146,694 records per second
- 125 of those findings were given a cause the agent stage verified against the records, covering Rs 22,48,177.19 of turnover, of which Rs 2,03,584.14 is money whose disposition the verdict decides, wait for a gateway to recover or escalate it

## What to look at

### SETTLEMENT_NEVER_SENT  (2 findings)

card captured 2026-08-17 between 12:00 and 16:00. 2 payments captured and never settled.

### SETTLEMENT_FAILED  (4 findings)

card captured 2026-08-18 between 08:00 and 12:00. 4 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (2 findings)

card captured 2026-08-18 between 20:00 and 24:00. 2 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (3 findings)

card captured 2026-08-19 between 08:00 and 12:00. 3 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (3 findings)

card captured 2026-08-19 between 12:00 and 16:00. 3 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (3 findings)

card captured 2026-08-24 between 12:00 and 16:00. 3 payments on setl_00048, which failed.

### SETTLEMENT_FAILED  (2 findings)

emi captured 2026-08-19 between 12:00 and 16:00. 2 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (2 findings)

netbanking captured 2026-08-19 between 12:00 and 16:00. 2 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (3 findings)

netbanking captured 2026-08-24 between 16:00 and 20:00. 3 payments on setl_00048, which failed.

### SETTLEMENT_NEVER_SENT  (2 findings)

upi captured 2026-08-13 between 12:00 and 16:00. 2 payments captured and never settled.

### SETTLEMENT_NEVER_SENT  (2 findings)

upi captured 2026-08-14 between 12:00 and 16:00. 2 payments captured and never settled.

### SETTLEMENT_NEVER_SENT  (2 findings)

upi captured 2026-08-15 between 16:00 and 20:00. 2 payments captured and never settled.

### SETTLEMENT_FAILED  (6 findings)

upi captured 2026-08-18 between 08:00 and 12:00. 6 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (2 findings)

upi captured 2026-08-18 between 12:00 and 16:00. 2 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (8 findings)

upi captured 2026-08-18 between 16:00 and 20:00. 8 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (5 findings)

upi captured 2026-08-18 between 20:00 and 24:00. 5 payments on setl_00038, which failed.

### SETTLEMENT_FAILED  (2 findings)

upi captured 2026-08-19 between 08:00 and 12:00. 2 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (4 findings)

upi captured 2026-08-19 between 16:00 and 20:00. 4 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (2 findings)

upi captured 2026-08-19 between 20:00 and 24:00. 2 payments on setl_00039, which failed.

### SETTLEMENT_FAILED  (7 findings)

upi captured 2026-08-24 between 08:00 and 12:00. 7 payments on setl_00048, which failed.

### SETTLEMENT_FAILED  (9 findings)

upi captured 2026-08-24 between 16:00 and 20:00. 9 payments on setl_00048, which failed.

### SETTLEMENT_FAILED  (5 findings)

upi captured 2026-08-24 between 20:00 and 24:00. 5 payments on setl_00048, which failed.

### SETTLEMENT_NEVER_SENT  (2 findings)

upi captured 2026-08-26 between 12:00 and 16:00. 2 payments captured and never settled.

### RATE_CARD_MISMATCH  (9 findings)

card payments. 9 payments charged off the agreed rate.

### RATE_CARD_MISMATCH  (3 findings)

emi payments. 3 payments charged off the agreed rate.

### RATE_CARD_MISMATCH  (4 findings)

netbanking payments. 4 payments charged off the agreed rate.

### RATE_CARD_MISMATCH  (12 findings)

upi payments. 12 payments charged off the agreed rate.

### BANK_TRANSFER_CHARGE  (15 findings)

15 credits. 15 credits short by a transfer charge.

### No cause determined  (4 groups)

Grouped, investigated, and left undiagnosed rather than guessed at.

- card captured 2026-08-19 between 20:00 and 24:00. Payments show mixed settlement issues: one never sent, one failed, no single cause applies.
- netbanking captured 2026-08-19 between 16:00 and 20:00. No single cause among the allowed set applies to all three payments: two have a failed settlement and one has no settlement, which disqualifies both SETTLEMENT_FAILED and SETTLEMENT_NEVER_SENT.
- upi captured 2026-08-19 between 12:00 and 16:00. No single cause among the allowed list fits all payments with available evidence.
- 8 credits. All settlements are processed with zero shortfall, none match any of the defined unexplained causes.

### 63 individual findings, no shared pattern

Rs 98,767.18 across findings that grouped with nothing else.

## Proposals the arithmetic rejected

The model proposed 30 causes. 2 were rejected because the cited records did not support them.

- card captured 2026-08-19 between 16:00 and 20:00: 1 cited payments were never assigned to a settlement
- upi captured 2026-08-24 between 12:00 and 16:00: cited setl_00048, not in the group

## Detection by class

| class | expected | detected |
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
| T_PLUS_TWO | 1117 | 1117 |
| UNKNOWN_CREDIT | 6 | 6 |
| UNLINKED_ORDER | 57 | 57 |

GATEWAY_OUTAGE reads 0 detected above because the cascade never emits that kind. The agent stage separately verified 0 of the 10 planted, checked against the answer key and not just accepted on the model's say.
