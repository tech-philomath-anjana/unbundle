# Refusal run

Seed 20260828, 5000 orders, as of 2026-08-28. Every proposal runs through the same tool-calling loop the keyed path uses: it looks up its own group's first member, then answers GATEWAY_OUTAGE and cites every member of it, and ignores the reason it is given when a proposal comes back rejected. The model call is scripted rather than real, so no API key is read and this is reproducible by anyone who clones the repository.

## What the adjudicator did

- 80 groups put to the proposer
- 240 proposals made, up to 3 turns each, with the reason for a rejection fed back before the next turn
- 240 record(s) looked up before a proposal, one per group per turn
- **80 groups still unexplained** after being told, every turn, exactly what the records did not support
- 0 accepted (0 of those a give up, scored separately below)
- of the 0 accepted with a cause, 0 cited only payments that really were planted as an outage, and 0 did not

## Why each group was refused

| group | members | turns | lookups | reason on the last turn |
|---|---|---|---|---|
| card captured 2026-08-03 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-06 between 08:00 and 12:00 | 8 | 3 | 3 | 7 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-06 between 12:00 and 16:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| card captured 2026-08-14 between 08:00 and 12:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-14 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| card captured 2026-08-14 between 16:00 and 20:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-14 between 20:00 and 24:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-15 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-15 between 12:00 and 16:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| card captured 2026-08-15 between 16:00 and 20:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| card captured 2026-08-16 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-16 between 12:00 and 16:00 | 7 | 3 | 3 | 7 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-16 between 16:00 and 20:00 | 6 | 3 | 3 | 6 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-16 between 20:00 and 24:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| card captured 2026-08-19 between 08:00 and 12:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-19 between 12:00 and 16:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| card captured 2026-08-19 between 16:00 and 20:00 | 9 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-19 between 20:00 and 24:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-25 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card captured 2026-08-26 between 08:00 and 12:00 | 5 | 3 | 3 | 4 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-26 between 16:00 and 20:00 | 6 | 3 | 3 | 6 cited payments were assigned to a settlement, so the gateway took them |
| card captured 2026-08-26 between 20:00 and 24:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| emi captured 2026-08-06 between 16:00 and 20:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| emi captured 2026-08-19 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-06 between 08:00 and 12:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-06 between 16:00 and 20:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-14 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-14 between 16:00 and 20:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-14 between 20:00 and 24:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| netbanking captured 2026-08-15 between 08:00 and 12:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-15 between 12:00 and 16:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-16 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-16 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-16 between 16:00 and 20:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-19 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-19 between 12:00 and 16:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-19 between 16:00 and 20:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-26 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-26 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| netbanking captured 2026-08-26 between 20:00 and 24:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-01 between 16:00 and 20:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-06 between 08:00 and 12:00 | 11 | 3 | 3 | 10 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-06 between 12:00 and 16:00 | 11 | 3 | 3 | 11 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-06 between 16:00 and 20:00 | 8 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-06 between 20:00 and 24:00 | 5 | 3 | 3 | 4 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-09 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-14 between 08:00 and 12:00 | 11 | 3 | 3 | 9 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-14 between 12:00 and 16:00 | 9 | 3 | 3 | 9 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-14 between 16:00 and 20:00 | 9 | 3 | 3 | 9 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-14 between 20:00 and 24:00 | 8 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-15 between 08:00 and 12:00 | 9 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-15 between 12:00 and 16:00 | 7 | 3 | 3 | 7 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-15 between 16:00 and 20:00 | 17 | 3 | 3 | 16 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-15 between 20:00 and 24:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-16 between 08:00 and 12:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-16 between 12:00 and 16:00 | 5 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-16 between 16:00 and 20:00 | 6 | 3 | 3 | 6 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-16 between 20:00 and 24:00 | 6 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-17 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-17 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-19 between 08:00 and 12:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-19 between 12:00 and 16:00 | 18 | 3 | 3 | 5 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-19 between 16:00 and 20:00 | 6 | 3 | 3 | 6 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-19 between 20:00 and 24:00 | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-21 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-22 between 12:00 and 16:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| upi captured 2026-08-26 between 08:00 and 12:00 | 8 | 3 | 3 | 7 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-26 between 12:00 and 16:00 | 13 | 3 | 3 | 11 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-26 between 16:00 and 20:00 | 14 | 3 | 3 | 11 cited payments were assigned to a settlement, so the gateway took them |
| upi captured 2026-08-26 between 20:00 and 24:00 | 8 | 3 | 3 | 8 cited payments were assigned to a settlement, so the gateway took them |
| wallet captured 2026-08-06 between 08:00 and 12:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| wallet captured 2026-08-15 between 12:00 and 16:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| wallet captured 2026-08-15 between 16:00 and 20:00 | 3 | 3 | 3 | 3 payments is coincidence, an outage needs 5 |
| wallet captured 2026-08-16 between 08:00 and 12:00 | 2 | 3 | 3 | 2 payments is coincidence, an outage needs 5 |
| card payments | 13 | 3 | 3 | cited payments do not fall in one window |
| emi payments | 4 | 3 | 3 | 4 payments is coincidence, an outage needs 5 |
| netbanking payments | 6 | 3 | 3 | cited payments do not fall in one window |
| upi payments | 20 | 3 | 3 | cited payments do not fall in one window |
| 9 credits | 9 | 3 | 3 | 0 payments is coincidence, an outage needs 5 |
| 9 credits | 9 | 3 | 3 | 0 payments is coincidence, an outage needs 5 |
