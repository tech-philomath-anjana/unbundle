# Reconciliation report

Run of 5000 orders on seed 20260828, as of 2026-08-28.

## Cash position

Of the Rs 1,34,64,478.71 captured in this window, every payment ends in exactly one of these four and the run fails if they do not add up.

- Rs 1,10,29,194.71 received, settled and confirmed by a bank credit
- Rs 9,53,548.60 in flight, due but not arrived yet
- Rs 2,58,941.74 settled by Razorpay with no bank credit matched to it, so it is neither confirmed nor known to be missing
- **Rs 12,22,793.66** at risk, captured and past due with nothing settled

## Money at stake

Each line is a different kind of exposure and they are not added up, because a credit that arrived unattributed and a payment owed back to a customer are not the same money.

- Rs 12,85,209.38 credited to the bank that this run cannot tie to any settlement or order
- Rs 12,22,793.66 captured and past due with nothing settled
- Rs 2,95,621.33 of orders that tie to no payment at all
- Rs 1,88,386.84 charged to customers twice and owed back
- Rs 4,707.96 of fees with no published rate to check against
- Rs 1,817.11 charged above the agreed rate card
- Rs 239.45 taken by the bank on the transfers

## What this run may have got wrong

- Rs 925.63 of money due that this run did not surface. Reads the missing settlement class alone, so the two lines below cover the seven other kinds
- Rs 1,858.70 reported as reconciled that was not
- 20 planted problems the run never named, across all eight kinds. The detection table below says which
- Rs 2,52,504.80 sitting on records the run noticed under no kind at all, counted once per record rather than once per label
- Rs 4,707.96 of fees charged on payments whose card network the export does not name, so no published rate exists to check them against

## What landed

- 51 of 64 bank credits explained (79.7% by count, 89.2% by value)
- 688 findings, of which 2 would have wasted your time
- 0 healthy in flight payments wrongly reported as a problem
- Rs 2,95,621.33 of orders that tie to no payment at all
- 144,486 records per second
- 26 of those findings were given a cause the agent stage verified against the records, covering Rs 34,837.64 of turnover, of which Rs 34,837.64 is money whose disposition the verdict decides, wait for a gateway to recover or escalate it

## What to look at

### SETTLEMENT_NEVER_SENT  (2 findings)

card captured 2026-08-03 between 12:00 and 16:00. 2 payments captured and never settled.

### SETTLEMENT_FAILED  (4 findings)

card captured 2026-08-06 between 12:00 and 16:00. 4 payments on setl_00012, which failed.

### SETTLEMENT_FAILED  (5 findings)

card captured 2026-08-14 between 08:00 and 12:00. 5 payments on setl_00030, which failed.

### SETTLEMENT_FAILED  (3 findings)

card captured 2026-08-14 between 12:00 and 16:00. 3 payments on setl_00030, which failed.

### SETTLEMENT_FAILED  (5 findings)

card captured 2026-08-14 between 16:00 and 20:00. 5 payments on setl_00030, which failed.

### SETTLEMENT_FAILED  (2 findings)

card captured 2026-08-14 between 20:00 and 24:00. 2 payments on setl_00030, which failed.

### SETTLEMENT_FAILED  (2 findings)

card captured 2026-08-15 between 08:00 and 12:00. 2 payments on setl_00030, which failed.

### SETTLEMENT_FAILED  (3 findings)

card captured 2026-08-15 between 16:00 and 20:00. 3 payments on setl_00030, which failed.

### No cause determined  (1 groups)

Grouped, investigated, and left undiagnosed rather than guessed at.

- card captured 2026-08-15 between 12:00 and 16:00. The group contains mixed settlement issues: three payments are linked to a settlement that failed (SETTLEMENT_FAILED) while one payment has no settlement record (SETTLEMENT_NEVER_SENT). No single listed cause accounts for all members simultaneously.

### 92 individual findings, no shared pattern

Rs 2,47,449.99 across findings that grouped with nothing else.

## Proposals the arithmetic rejected

The model proposed 10 causes. 2 were rejected because the cited records did not support them.

- card captured 2026-08-06 between 08:00 and 12:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 6636, Requested 1651. Please try again in 2.1525s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- card captured 2026-08-16 between 08:00 and 12:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199636, Requested 1058. Please try again in 4m59.808s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}

## 69 groups the model never answered

Not a refusal and not a rejection, the model produced nothing the loop could read on any of its 3 attempts. The last reason is shown.

- card captured 2026-08-16 between 12:00 and 16:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199634, Requested 1569. Please try again in 8m39.696s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- card captured 2026-08-16 between 16:00 and 20:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199632, Requested 1237. Please try again in 6m15.408s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- card captured 2026-08-16 between 20:00 and 24:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199630, Requested 1047. Please try again in 4m52.464s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- card captured 2026-08-19 between 08:00 and 12:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199629, Requested 1499. Please try again in 8m7.296s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- card captured 2026-08-19 between 12:00 and 16:00: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_<redacted>` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199625, Requested 1465. Please try again in 7m50.88s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}

## Detection by class

| class | expected | detected |
|---|---|---|
| BANK_FEE_DEDUCTED | 11 | 9 |
| BATCHED | 53 | 51 |
| CHARGEBACK_LATER | 8 | 8 |
| DUPLICATE_PAYMENT | 56 | 56 |
| FAILED_RETRY | 207 | 207 |
| FEE_MISMATCH | 44 | 44 |
| GATEWAY_OUTAGE | 13 | 0 |
| HELD_BACK | 3 | 0 |
| MANGLED_UTR | 11 | 9 |
| MISSING_SETTLEMENT | 452 | 452 |
| NETWORK_UNKNOWN | 73 | 73 |
| ORDER_ID_MISSING | 406 | 406 |
| PARTIAL_REFUND | 6 | 6 |
| ROUNDING_DRIFT | 49 | 16 |
| T_PLUS_TWO | 1883 | 1699 |
| UNKNOWN_CREDIT | 11 | 11 |
| UNLINKED_ORDER | 105 | 105 |

GATEWAY_OUTAGE reads 0 detected above because the cascade never emits that kind. The agent stage separately verified 0 of the 13 planted, checked against the answer key and not just accepted on the model's say.
