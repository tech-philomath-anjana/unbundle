# Refusal run

Seed 20260828, 3000 orders, as of 2026-08-28. Every proposal runs through the same tool-calling loop the keyed path uses: it looks up its own group's first member, then answers GATEWAY_OUTAGE and cites every member of it, and ignores the reason it is given when a proposal comes back rejected. The model call is scripted rather than real, so no API key is read and this is reproducible by anyone who clones the repository.

## What the adjudicator did

- 34 groups put to the proposer
- 102 proposals made, up to 3 turns each, with the reason for a rejection fed back before the next turn
- 102 record(s) looked up before a proposal, one per group per turn
- **34 groups still unexplained** after being told, every turn, exactly what the records did not support
- 0 accepted (0 of those a give up, scored separately below)
- of the 0 accepted with a cause, 0 cited only payments that really were planted as an outage, and 0 did not

## Why each group was refused

| group | members | turns | lookups | reason on the last turn |
|---|---|---|---|---|
| card captured 2026-08-17 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-18 between 08:00 and 12:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| card captured 2026-08-18 between 20:00 and 24:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-19 between 08:00 and 12:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| card captured 2026-08-19 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| card captured 2026-08-19 between 16:00 and 20:00 | 6 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-19 between 20:00 and 24:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-24 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| emi captured 2026-08-19 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-19 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-19 between 16:00 and 20:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-24 between 16:00 and 20:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-13 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-14 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-15 between 16:00 and 20:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-18 between 08:00 and 12:00 | 6 | 3 | 3 | 6 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-18 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-18 between 16:00 and 20:00 | 8 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-18 between 20:00 and 24:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-19 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-19 between 12:00 and 16:00 | 13 | 3 | 3 | 3 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-19 between 16:00 and 20:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-19 between 20:00 and 24:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-24 between 08:00 and 12:00 | 7 | 3 | 3 | 7 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-24 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-24 between 16:00 and 20:00 | 9 | 3 | 3 | 9 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-24 between 20:00 and 24:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-26 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card payments | 9 | 3 | 3 | cited payments do not fall in one window |
| emi payments | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking payments | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi payments | 12 | 3 | 3 | cited payments do not fall in one window |
| 15 credits | 15 | 3 | 3 | 0 payments is coincidence, an outage needs 5 |
| 8 credits | 8 | 3 | 3 | 0 payments is coincidence, an outage needs 5 |
