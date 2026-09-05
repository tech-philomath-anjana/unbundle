# Renders the run in results/ as one self contained HTML file. Every figure comes either straight
# out of results/ledger.json or out of the CSVs through the loader the pipeline uses, so there is
# nothing here that can disagree with the run. It does not import ground_truth, so the page shows
# what the run reported rather than what the answer key knows, same rule the matcher works under

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from unbundle.load import load
from unbundle.money import Paise
from unbundle.reconcile import BANK_FEE_CEILING
from unbundle.record_types import Adjustment, BankLine, Order, Payment, Settlement

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "results" / "ledger.json"
TRACE = REPO / "results" / "trace.md"
REPORT = REPO / "results" / "report.md"
DATA = REPO / "data"
OUT = REPO / "dashboard.html"


# The gap between what the settlement named and what it paid, and the payment left out of the
# transfer. This is a second copy of reconcile.py:185-192, kept because the ledger records which
# payments are in a credit and not the arithmetic that gets from them to the figure the bank
# paid, so the recon panel has to work it out again rather than ask the reader to take the total
# on trust. Two members matching the gap is two answers and naming either one puts a payment that
# was paid on the page as held back, so it is left unnamed instead
def settlement_shortfall(
    members: list[Payment], deducted: Paise, settlement_amount: Paise
) -> tuple[Paise, str | None]:
    shortfall = sum(payment.net for payment in members) - deducted - settlement_amount
    if shortfall == 0:
        return shortfall, None
    excluded = [payment for payment in members if payment.net == shortfall]
    return shortfall, excluded[0].payment_id if len(excluded) == 1 else None


# A cell holding the closing script sequence ends the script element wherever it appears, which
# kills every function on the page rather than spoiling the one row, and esc in the page cannot
# help because the browser never gets that far. The angle bracket goes out as its unicode escape,
# which is the same string once JSON has been parsed
def _embed(data: dict) -> str:
    return json.dumps(data).replace("<", "\\u003c")


# Indian grouping, worked out from the integer so no float ever touches a figure on this page
def rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    whole, part = divmod(abs(paise), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}{s}.{part:02d}"


def _records_per_second() -> str | None:
    # Only report.md carries it, run.py computes it and does not put it in the ledger
    found = re.search(r"^- ([\d,]+) records per second$", REPORT.read_text(), re.M)
    return found.group(1) if found else None


def _parse_trace() -> dict:
    text = TRACE.read_text()
    model = re.search(r"^Model: (.+)$", text, re.M)
    sorted_line = re.search(
        r"sorted (\d+) of (\d+) flagged findings into (\d+) candidate incidents "
        r"and (\d+) standing alone",
        text,
    )

    groups: list[dict] = []
    for line in text.splitlines():
        head = re.match(r"^- proposing for (\w+): (.+), (\d+) members$", line)
        if head:
            groups.append(
                {"kind": head.group(1), "group": head.group(2),
                 "members": int(head.group(3)), "turns": []}
            )
            continue
        turn = re.match(r"^-\s+turn (\d+): (.+)$", line)
        if turn and groups:
            body = turn.group(2)
            if "gave up" in body:
                outcome, detail = "gave_up", body.split("gave up, ", 1)[-1]
            elif ", accepted:" in body:
                outcome, detail = "accepted", body.split(", accepted: ", 1)[1]
            elif ", rejected:" in body:
                outcome, detail = "rejected", body.split(", rejected: ", 1)[1]
            else:
                outcome, detail = "other", body
            proposed = re.search(r"proposed (\w+) citing (\d+)", body)
            groups[-1]["turns"].append(
                {
                    "n": int(turn.group(1)),
                    "outcome": outcome,
                    "detail": detail,
                    "cause": proposed.group(1) if proposed else None,
                    "citing": int(proposed.group(2)) if proposed else None,
                }
            )

    turns = [t for g in groups for t in g["turns"]]
    return {
        "model": model.group(1) if model else None,
        "sorted": int(sorted_line.group(1)) if sorted_line else None,
        "flagged": int(sorted_line.group(2)) if sorted_line else None,
        "incidents": int(sorted_line.group(3)) if sorted_line else None,
        "standing_alone": int(sorted_line.group(4)) if sorted_line else None,
        "groups": groups,
        "accepted": sum(1 for t in turns if t["outcome"] == "accepted"),
        "rejected": sum(1 for t in turns if t["outcome"] == "rejected"),
        "gave_up": sum(1 for t in turns if t["outcome"] == "gave_up"),
        "first_rejected": sum(
            1 for g in groups for t in g["turns"] if t["n"] == 1 and t["outcome"] == "rejected"
        ),
        "corrected": sum(
            1
            for g in groups
            if g["turns"]
            and g["turns"][0]["outcome"] == "rejected"
            and any(t["outcome"] == "accepted" for t in g["turns"])
        ),
    }


# One row per matched credit, carrying the arithmetic that gets from the payments the gateway
# named to the figure the bank actually paid
def _credits(
    ledger: dict, orders, payments, settlements, bank_lines, adjustments
) -> tuple[list[dict], int]:
    by_id = {p.payment_id: p for p in payments}
    index_of = {p.payment_id: i for i, p in enumerate(payments)}
    settlement_of = {s.settlement_id: s for s in settlements}
    order_by_id = {o.order_id: o for o in orders if o.order_id}
    order_by_receipt = {o.receipt: o for o in orders if o.receipt}

    assigned: dict[str, list[Payment]] = {}
    for payment in payments:
        if payment.settlement_id:
            assigned.setdefault(payment.settlement_id, []).append(payment)

    against: dict[str, list[Adjustment]] = {}
    for adjustment in adjustments:
        against.setdefault(adjustment.settlement_id, []).append(adjustment)

    rows = []
    spanning = 0
    for entry in ledger["matched"]:
        ids = entry["payment_ids"]
        settlement_ids = {by_id[p].settlement_id for p in ids}
        # A credit covering payments from more than one settlement has no single settlement to
        # tie out against, so this table cannot show it, and it is counted rather than dropped
        # or the row count under the table quietly stops matching credits_matched beside it
        if len(settlement_ids) != 1:
            spanning += 1
            continue
        sid = next(iter(settlement_ids))
        settlement = settlement_of[sid]
        members = assigned[sid]

        gross = sum(p.amount for p in members)
        fee = sum(p.fee for p in members)
        tax = sum(p.tax for p in members)
        deductions = against.get(sid, [])
        deducted = sum(a.amount for a in deductions)
        held, held_back = settlement_shortfall(members, deducted, settlement.amount)

        line = None
        if settlement.utr:
            line = next((b for b in bank_lines if settlement.utr in b.narration), None)
        if line is None:
            line = next((b for b in bank_lines if b.narration == entry["narration"]), None)
        bank_gap = settlement.amount - line.credit if line else None

        joined_id = joined_receipt = missing = 0
        for pid in ids:
            payment = by_id[pid]
            if payment.order_id in order_by_id:
                joined_id += 1
            elif payment.order_receipt and payment.order_receipt in order_by_receipt:
                joined_receipt += 1
            else:
                missing += 1

        if held_back:
            state = "held_back"
        elif line is None:
            state = "unmatched"
        elif settlement.utr and settlement.utr not in line.narration:
            state = "narration_damaged"
        elif bank_gap and 0 < bank_gap <= BANK_FEE_CEILING:
            state = "bank_charge"
        # A negative gap is the bank paying more than the settlement said it would send, and
        # it used to fall through to short, which told the reader money was missing on the one
        # row where there is more of it than expected
        elif bank_gap and bank_gap < 0:
            state = "over"
        elif bank_gap:
            state = "short"
        else:
            state = "exact"

        rows.append(
            {
                "sid": sid,
                "utr": settlement.utr,
                "narration": entry["narration"],
                "date": str(line.txn_date) if line else None,
                "n": len(ids),
                "gross": gross,
                "fee": fee,
                "tax": tax,
                "settlement": settlement.amount,
                "credit": line.credit if line else None,
                "bank_gap": bank_gap,
                "held_back": held_back,
                "held_amount": held if held_back else None,
                "state": state,
                "adjustments": [
                    {"id": a.adjustment_id, "kind": a.kind, "payment_id": a.payment_id,
                     "amount": a.amount, "raised_at": str(a.raised_at.date())}
                    for a in deductions
                ],
                "payment_rows": [index_of[p] for p in ids],
                "trace": {"order_id": joined_id, "receipt": joined_receipt, "missing": missing},
            }
        )

    rows.sort(key=lambda r: (r["date"] or "9999-99-99", r["sid"]))
    return rows, spanning


def build() -> dict:
    ledger = json.loads(LEDGER.read_text())
    loaded = load(DATA)

    orders: tuple[Order, ...] = loaded.orders
    payments: tuple[Payment, ...] = loaded.payments
    settlements: tuple[Settlement, ...] = loaded.settlements
    bank_lines: tuple[BankLine, ...] = loaded.bank_lines
    adjustments: tuple[Adjustment, ...] = loaded.adjustments

    credits, spanning = _credits(ledger, orders, payments, settlements, bank_lines, adjustments)

    order_by_id = {o.order_id for o in orders if o.order_id}
    payment_order_ids = {p.order_id for p in payments if p.order_id}
    payment_receipts = {p.order_receipt for p in payments if p.order_receipt}

    joined_id = sum(1 for o in orders if o.order_id and o.order_id in payment_order_ids)
    joined_receipt = sum(
        1
        for o in orders
        if not (o.order_id and o.order_id in payment_order_ids)
        and o.receipt
        and o.receipt in payment_receipts
    )
    orphans = [
        o
        for o in orders
        if not (o.order_id and o.order_id in payment_order_ids)
        and not (o.receipt and o.receipt in payment_receipts)
    ]

    return {
        "run": {
            "seed": ledger["seed"],
            "as_of": ledger["as_of"],
            "orders": ledger["order_count"],
            "records_per_second": _records_per_second(),
        },
        # Three sources, five files. The gateway is the one that exports more than once,
        # and grouping them is what keeps the page from reading as five separate systems
        "groups": ["Merchant", "Gateway (Razorpay)", "Bank"],
        "sources": [
            {"name": "orders.csv", "group": "Merchant",
             "label": "your order system", "rows": len(orders),
             "value": sum(o.amount for o in orders),
             "columns": ["order_ref", "order_id", "receipt", "placed_at", "amount",
                         "status", "attempts"],
             "note": (f"{joined_id} join a payment on the gateway order id, "
                      f"{joined_receipt} only by receipt, "
                      f"{len(orphans)} tie to no payment at all")},
            {"name": "payments.csv", "group": "Gateway (Razorpay)",
             "label": "the gateway export", "rows": len(payments),
             "value": sum(p.amount for p in payments if p.status == "captured"),
             "columns": ["payment_id", "order_id", "order_receipt", "happened_at", "status",
                         "method", "card_network", "amount", "fee", "tax", "settlement_id"],
             "note": (f"{sum(1 for p in payments if p.status == 'captured')} captured, "
                      f"{sum(1 for p in payments if p.status == 'failed')} failed attempts "
                      f"that carry no money")},
            {"name": "settlements.csv", "group": "Gateway (Razorpay)",
             "label": "the gateway export", "rows": len(settlements),
             "value": sum(s.amount for s in settlements),
             "columns": ["settlement_id", "utr", "settled_at", "amount", "fees", "tax", "status"],
             "note": (f"{sum(1 for s in settlements if s.status == 'processed')} processed, "
                      f"{sum(1 for s in settlements if s.status == 'failed')} failed outright")},
            {"name": "adjustments.csv", "group": "Gateway (Razorpay)",
             "label": "the gateway export", "rows": len(adjustments),
             "value": sum(a.amount for a in adjustments),
             "columns": ["adjustment_id", "kind", "payment_id", "amount", "raised_at",
                         "settlement_id"],
             "note": (f"{sum(1 for a in adjustments if a.kind == 'refund')} refunds, "
                      f"{sum(1 for a in adjustments if a.kind == 'chargeback')} chargebacks. "
                      "A refund can be raised against a payment settled months earlier")},
            {"name": "bank_statement.csv", "group": "Bank",
             "label": "your bank statement", "rows": len(bank_lines),
             "value": sum(b.credit for b in bank_lines),
             "columns": ["txn_date", "narration", "credit", "debit"],
             "note": ("the UTR is buried in the narration, not a column. "
                      f"{ledger['credits_matched']} of {ledger['credits_total']} identified")},
        ],
        "rows": {
            "orders.csv": [[o.order_ref, o.order_id or "", o.receipt or "",
                            str(o.placed_at), o.amount, o.status, o.attempts] for o in orders],
            "payments.csv": [[p.payment_id, p.order_id or "", p.order_receipt or "",
                              str(p.happened_at), p.status, p.method, p.card_network or "",
                              p.amount, p.fee, p.tax, p.settlement_id or ""] for p in payments],
            "settlements.csv": [[s.settlement_id, s.utr or "", str(s.settled_at), s.amount,
                                 s.fees, s.tax, s.status] for s in settlements],
            "bank_statement.csv": [[str(b.txn_date), b.narration, b.credit, b.debit]
                                   for b in bank_lines],
            "adjustments.csv": [[a.adjustment_id, a.kind, a.payment_id, a.amount,
                                 str(a.raised_at), a.settlement_id or ""] for a in adjustments],
        },
        "money_columns": ["amount", "fee", "tax", "fees", "credit", "debit"],
        "cash": {
            "received": ledger["money_received"],
            "in_flight": ledger["money_in_flight"],
            "in_flight_count": len(ledger["in_flight"]),
            "at_risk": ledger["money_at_risk_reported"],
            "unconfirmed": ledger["money_unconfirmed"],
            "captured": ledger["money_captured"],
        },
        "match": {
            "credits_matched": ledger["credits_matched"],
            "credits_total": ledger["credits_total"],
            "value_matched": ledger["credit_value_matched"],
            "value_total": ledger["credit_value_total"],
        },
        "accuracy": {
            "flagged_real": ledger["flagged_real"],
            "flagged_total": ledger["flagged_total"],
            "money_missed": ledger["money_missed"],
            "money_wrongly_cleared": ledger["money_wrongly_cleared"],
            "in_flight_wrongly_flagged": ledger["in_flight_wrongly_flagged"],
            "unnamed_problems": ledger["unnamed_problems"],
        },
        "at_stake": [
            ["credited to the bank and tied to no settlement or order",
             ledger["money_unattributed_credit"]],
            ["captured and past due with nothing settled", ledger["money_at_risk_reported"]],
            ["orders that tie to no payment at all", ledger["money_unlinked"]],
            ["charged to customers twice and owed back", ledger["money_owed_back"]],
            ["fees with no published rate to check against", ledger["money_fee_unverified"]],
            ["charged above the agreed rate card", ledger["money_fee_overcharged"]],
            ["taken by the bank on the transfers", ledger["money_bank_charges"]],
        ],
        "by_class": ledger["by_class"],
        "credits": credits,
        "credits_spanning": spanning,
        "agent": _parse_trace(),
    }


STATE_LABEL = {
    "exact": "Ties out",
    "bank_charge": "Bank charge",
    "narration_damaged": "Narration damaged",
    "held_back": "Held back",
    "short": "Short",
    "over": "Over by more than expected",
    "unmatched": "Unmatched",
}

CSS = """
:root{
  --background:#f5f7fb; --foreground:#0c2651; --card:#fff; --elevated:#fbfcfe;
  --muted:#eef1f7; --muted-foreground:#5a6b85; --faint:#8d9bb5;
  --border:#e4e9f1; --hairline:#edf0f6;
  --navy:#012652; --accent:#0d94fb; --pos:#1a8245; --neg:#d92d20; --amber:#b25e00;
  --sans:"Inter",-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
  --mono:"Roboto Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:var(--sans);background:var(--background);color:var(--foreground);
  -webkit-font-smoothing:antialiased;font-size:14px}
.tnum{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1}
.mono{font-family:var(--mono);font-feature-settings:"tnum" 1}
.layout{display:grid;grid-template-columns:232px 1fr;height:100%}
aside{background:var(--navy);color:rgba(255,255,255,.7);padding:20px 16px;
  display:flex;flex-direction:column;gap:24px}
.brand{display:flex;align-items:center;gap:10px;padding:0 8px}
.brand-mark{width:28px;height:28px;border-radius:7px;background:var(--accent);
  display:flex;align-items:center;justify-content:center}
.brand-name{font-size:16px;font-weight:600;letter-spacing:-.01em;color:#fff}
.side-meta{background:rgba(255,255,255,.08);border-radius:8px;padding:10px 12px}
.side-meta dt{font-size:10px;font-weight:600;text-transform:uppercase;
  letter-spacing:.08em;color:rgba(255,255,255,.45)}
.side-meta dd{font-size:13px;font-weight:500;color:#fff;margin-top:2px}
nav a{display:flex;align-items:center;gap:10px;border-radius:7px;
  background:rgba(255,255,255,.12);padding:8px 12px;font-size:13px;font-weight:500;
  color:#fff;text-decoration:none}
nav a .dot{width:6px;height:6px;border-radius:50%;background:var(--accent)}
.side-foot{margin-top:auto;font-size:11px;line-height:1.5;color:rgba(255,255,255,.4)}
main{overflow-y:auto}
.page{max-width:1200px;margin:0 auto;padding:28px 32px 56px;
  display:flex;flex-direction:column;gap:24px}
h1{font-size:24px;font-weight:600;line-height:1.1;letter-spacing:-.02em;color:var(--navy)}
h2{font-size:15px;font-weight:600;color:var(--navy)}
.sub{font-size:13px;color:var(--muted-foreground);margin-top:4px;
  display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.sep{color:var(--faint)}
.card{border-radius:8px;background:var(--card);border:1px solid var(--border);
  box-shadow:0 1px 2px rgba(12,38,81,.04)}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.stat{padding:20px;display:flex;flex-direction:column;gap:12px}
.stat-name{font-size:13px;font-weight:500;color:var(--muted-foreground)}
.stat-sub{font-size:12px;color:var(--faint)}
.money{display:inline-flex;align-items:baseline;gap:.28em;font-variant-numeric:tabular-nums}
.money .sym{font-size:.55em;font-weight:500;color:var(--faint)}
.money .val{font-weight:600}
.m-xl{font-size:28px;letter-spacing:-.01em}
.m-lg{font-size:22px}
.m-md{font-size:15px}
.m-sm{font-size:13px}
.neg .val{color:var(--neg)} .pos .val{color:var(--pos)}
.dim .val{color:var(--muted-foreground);font-weight:500}
.label{font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;color:var(--faint)}
/* nowrap because Narration damaged is the only state of two words, so it is the only one that
   ever breaks, and a pill split over two lines with its dot stranded reads as a different kind
   of thing from the one word states beside it */
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;
  padding:3px 9px;font-size:12px;font-weight:500;white-space:nowrap}
.pill .d{width:6px;height:6px;border-radius:50%;flex:0 0 auto}
.nw{white-space:nowrap}
.pill-ok{background:rgba(1,38,82,.06);color:var(--navy)}   .pill-ok .d{background:var(--navy)}
.pill-fly{background:rgba(13,148,251,.1);color:var(--accent)} .pill-fly .d{background:var(--accent)}
.pill-att{background:rgba(178,94,0,.12);color:var(--amber)} .pill-att .d{background:var(--amber)}
.strip{display:flex;flex-wrap:wrap;gap:8px 28px;padding:0 4px;font-size:13px;align-items:baseline}
.strip .k{color:var(--faint)}
.chead{display:flex;align-items:flex-end;justify-content:space-between;
  padding:16px 20px 12px;gap:16px}
.hair{height:1px;background:var(--hairline)}
table.rows{width:100%;border-collapse:collapse;font-size:13px}
table.rows th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
  color:var(--faint);background:var(--elevated);padding:8px 12px;text-align:left;
  border-top:1px solid var(--hairline);border-bottom:1px solid var(--hairline)}
table.rows td{padding:9px 12px;border-bottom:1px solid var(--hairline);vertical-align:middle}
table.rows tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums}
.crow{cursor:pointer}
.crow:hover{background:var(--elevated)}
.crow.open{background:var(--elevated)}
.chev{flex:0 0 auto;display:block;color:var(--faint);
  transition:transform .3s cubic-bezier(.16,1,.3,1)}
.crow.open .chev{transform:rotate(90deg)}
.detail td{background:var(--elevated);padding:0 12px 16px}
.detail-inner{animation:expand .24s cubic-bezier(.16,1,.3,1) both}
@keyframes expand{from{opacity:0;transform:translateY(-2px)}to{opacity:1;transform:none}}
.recon{background:var(--card);border:1px solid var(--hairline);border-radius:6px;
  padding:14px 16px;margin-top:8px}
.recon .line{display:flex;align-items:baseline;gap:16px;padding:5px 0}
.recon .line .t{flex:1;color:var(--muted-foreground)}
.recon .line .aside{font-size:11.5px;color:var(--faint)}
.recon .line.total{border-top:1.5px solid var(--foreground);margin-top:6px;padding-top:9px}
.recon .line.total .t{color:var(--foreground);font-weight:600}
.recon .sep-line{height:1px;background:var(--hairline);margin:6px 0}
.trace{margin-top:10px;font-size:12.5px;color:var(--muted-foreground);line-height:1.55}
.trace b{color:var(--accent);font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;display:block;margin-bottom:3px}
.pay-wrap{margin-top:10px;border:1px solid var(--hairline);border-radius:6px;
  background:var(--card);overflow:hidden}
.pay-wrap .cap{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;font-size:12px;color:var(--faint);border-top:1px solid var(--hairline)}
.pay-scroll{max-height:280px;overflow:auto}
.linkish{background:none;border:0;color:var(--accent);font:inherit;font-size:12px;
  font-weight:500;cursor:pointer;padding:0}
.linkish:hover{text-decoration:underline}
.btn{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:8px;
  background:var(--accent);color:#fff;font:inherit;font-size:13px;font-weight:500;
  padding:8px 14px;cursor:pointer;box-shadow:0 1px 2px rgba(1,38,82,.16)}
.btn:hover{background:#0b83e0}
.press{transition:transform .12s cubic-bezier(.16,1,.3,1)}
.press:active{transform:scale(.975)}
.src-btn{background:none;border:0;font:inherit;color:var(--accent);cursor:pointer;
  font-family:var(--mono);font-size:12.5px;padding:0}
.src-btn:hover{text-decoration:underline}
.src-group{display:inline-flex;align-items:baseline;gap:8px;padding:4px 10px;
  border:1px solid var(--border);border-radius:7px;background:var(--card)}
.src-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
  color:var(--faint);white-space:nowrap}
.src-n{font-size:11px;color:var(--faint);margin-left:-4px}
.lede{margin-top:8px;max-width:640px;font-size:13.5px;line-height:1.55;
  color:var(--muted-foreground)}
.side-lede{font-size:12px;line-height:1.55;color:rgba(255,255,255,.55)}
.disclose{display:flex;align-items:center;gap:9px;width:100%;text-align:left;
  background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:9px 13px;font:inherit;font-size:12.5px;color:var(--muted-foreground);
  cursor:pointer;margin-bottom:10px}
.disclose:hover{background:var(--elevated);color:var(--foreground)}
.disclose.open{color:var(--foreground)}
.disclose.open .chev{transform:rotate(90deg)}
.two{display:grid;grid-template-columns:1.4fr 1fr;gap:0}
.two > div{padding:20px}
.two > div:first-child{border-right:1px solid var(--hairline)}
.stake{display:flex;align-items:baseline;gap:16px;padding:6px 0}
.stake .t{flex:1;color:var(--muted-foreground);font-size:13px;line-height:1.5}
.turn{display:flex;gap:12px;padding:6px 0;font-size:12.5px;align-items:baseline}
.turn .n{color:var(--faint);font-family:var(--mono);font-size:11px;white-space:nowrap;
  flex:0 0 auto}
.turn .o{font-weight:600;white-space:nowrap;flex:0 0 auto}
.turn .o.a{color:var(--pos)} .turn .o.r{color:var(--neg)} .turn .o.g{color:var(--amber)}
.turn .o.rt{color:var(--faint)}
/* min-width:0 or the provider's error body, which has no space to break at, refuses to
   shrink and prints straight over the label beside it */
.turn .d{color:var(--muted-foreground);flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.turn .d.raw{font-family:var(--mono);font-size:11px;line-height:1.5;color:var(--faint)}
.grp{border:1px solid var(--hairline);border-radius:6px;padding:12px 14px;
  background:var(--card);margin-bottom:10px}
.grp .h{font-size:12px;color:var(--faint);font-family:var(--mono);margin-bottom:6px}
.scrim{position:fixed;inset:0;z-index:50;background:rgba(1,38,82,.4);
  backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:32px;
  animation:scrim .18s ease-out both}
@keyframes scrim{from{opacity:0}to{opacity:1}}
.modal{width:100%;max-width:1040px;max-height:84vh;background:var(--card);
  border-radius:12px;display:flex;flex-direction:column;overflow:hidden;
  box-shadow:0 24px 64px -16px rgba(1,38,82,.4);
  animation:panel .32s cubic-bezier(.16,1,.3,1) both}
@keyframes panel{from{opacity:0;transform:translateY(10px) scale(.985)}to{opacity:1;transform:none}}
.modal .mhead{display:flex;justify-content:space-between;align-items:center;flex:0 0 auto;
  padding:14px 20px;border-bottom:1px solid var(--hairline)}
/* Wraps rather than scrolls: a tab pushed off the right edge is a file nobody finds, and a
   group wraps whole so a file never ends up under the wrong heading */
.tabs{display:flex;flex-wrap:wrap;align-items:flex-end;gap:0;padding:8px 16px 0;flex:0 0 auto;
  background:var(--elevated);border-bottom:1px solid var(--hairline)}
.tabgrp{display:flex;flex-direction:column;gap:1px;padding:0 14px}
.tabgrp:first-child{padding-left:0}
.tabgrp + .tabgrp{border-left:1px solid var(--border)}
.tabgrp .gname{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
  color:var(--faint);padding:2px 12px 3px;white-space:nowrap}
.tabgrp .files{display:flex;gap:2px}
.tab{background:none;border:0;font:inherit;font-size:13px;cursor:pointer;
  padding:8px 12px;border-radius:7px 7px 0 0;color:var(--muted-foreground);
  display:flex;gap:8px;align-items:baseline;white-space:nowrap}
.tab .c{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
.tab.on{background:var(--card);color:var(--navy);font-weight:500;
  box-shadow:inset 0 -2px 0 var(--accent)}
.mbody{flex:1;min-height:0;overflow:auto}
.mbody table{width:100%;border-collapse:collapse;font-size:12px}
.mbody th{position:sticky;top:0;background:var(--card);z-index:2;
  font-family:var(--mono);font-size:11px;font-weight:500;text-transform:uppercase;
  letter-spacing:.03em;color:var(--faint);padding:8px 12px;text-align:left;
  box-shadow:0 8px 12px -10px rgba(1,38,82,.22)}
.mbody td{padding:6px 12px;border-bottom:1px solid var(--hairline)}
.mbody .empty{color:var(--faint)}
.mfoot{display:flex;justify-content:space-between;padding:10px 20px;flex:0 0 auto;
  border-top:1px solid var(--hairline);font-size:12px;color:var(--faint)}
.x{background:none;border:0;cursor:pointer;color:var(--faint);font-size:18px;
  width:28px;height:28px;border-radius:50%;line-height:1}
.x:hover{background:var(--muted);color:var(--foreground)}
footer{display:flex;justify-content:space-between;padding-top:16px;
  border-top:1px solid var(--hairline);font-size:12px;color:var(--faint)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
@media (prefers-reduced-motion:reduce){*{animation-duration:.01ms !important;
  transition-duration:.01ms !important}}
"""


# The triangle this used to use is a filled arrowhead sitting off the text baseline, so it
# looked like a bullet and nobody would think to press it, and this one is drawn
CHEV = ('<svg class="chev" width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">'
        '<path d="M4 2.5L7 5.5L4 8.5" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _money(paise, cls="m-md", tone=""):
    if paise is None:
        return '<span class="tnum" style="color:var(--faint)">&mdash;</span>'
    return (f'<span class="money {cls} {tone}"><span class="sym">Rs</span>'
            f'<span class="val">{rupees(paise)}</span></span>')


def _recon(credit) -> str:
    out = ['<div class="recon">',
           f'<div class="line"><span class="t">{credit["n"]} payments, gross</span>'
           f'{_money(credit["gross"], "m-sm", "dim")}</div>',
           f'<div class="line"><span class="t">less MDR + GST '
           f'<span class="aside">GST of {rupees(credit["tax"])} sits inside this</span></span>'
           f'{_money(-credit["fee"], "m-sm", "dim")}</div>']
    for adj in credit["adjustments"]:
        out.append(
            f'<div class="line"><span class="t">less {escape(adj["kind"])} on '
            f'<span class="mono">{escape(adj["payment_id"])}</span> '
            f'<span class="aside">raised {adj["raised_at"]}</span></span>'
            f'{_money(-adj["amount"], "m-sm", "dim")}</div>')
    if credit["held_back"]:
        out.append(
            f'<div class="line"><span class="t">less <b>held back</b> on '
            f'<span class="mono">{escape(credit["held_back"])}</span> '
            f'<span class="aside">named by the settlement, not paid</span></span>'
            f'{_money(-credit["held_amount"], "m-sm", "neg")}</div>')
    out.append('<div class="sep-line"></div>')
    out.append(f'<div class="line"><span class="t">settlement '
               f'<span class="mono">{escape(credit["sid"])}</span></span>'
               f'{_money(credit["settlement"], "m-sm", "dim")}</div>')
    if credit["bank_gap"]:
        out.append(f'<div class="line"><span class="t">less bank transfer charge</span>'
                   f'{_money(-credit["bank_gap"], "m-sm", "dim")}</div>')
    label = f'bank credit, {credit["date"]}' if credit["date"] else "no bank credit found"
    out.append(f'<div class="line total"><span class="t">{label}</span>'
               f'{_money(credit["credit"], "m-md")}</div>')
    out.append("</div>")
    return "".join(out)


def render(view: dict) -> str:
    run, match, acc = view["run"], view["match"], view["accuracy"]

    # A run that matched nothing has no percentage, so this says which total was empty rather
    # than dividing by zero or printing 0.0% and putting a figure where there is not one
    for total, what in ((match["credits_total"], "bank credits"),
                        (match["value_total"], "credit value"),
                        (acc["flagged_total"], "findings")):
        if not total:
            raise ValueError(f"nothing to render: the run has no {what}")

    spanning_note = (
        f', {view["credits_spanning"]} covering more than one settlement and not shown'
        if view["credits_spanning"] else "")

    by_count = 100 * match["credits_matched"] / match["credits_total"]
    by_value = 100 * match["value_matched"] / match["value_total"]
    precision = 100 * acc["flagged_real"] / acc["flagged_total"]

    srcs = ""
    for group in view["groups"]:
        members = [(i, s) for i, s in enumerate(view["sources"]) if s["group"] == group]
        files = "".join(
            f'<button class="src-btn" onclick="openFiles({i})">{escape(s["name"])}</button>'
            f'<span class="tnum src-n">{s["rows"]:,}</span>'
            for i, s in members)
        srcs += (f'<span class="src-group"><span class="src-label">{escape(group)}</span>'
                 f'{files}</span>')

    stats = [
        ("Received", view["cash"]["received"],
         f'confirmed by {match["credits_matched"]} bank credits', "pill-ok", "Reconciled"),
        ("In flight", view["cash"]["in_flight"],
         f'{view["cash"]["in_flight_count"]} payments, due but not arrived', "pill-fly",
         "Healthy"),
        ("At risk", view["cash"]["at_risk"],
         "captured, past due, nothing settled", "pill-att", "Needs review"),
    ]
    stat_html = "".join(
        f'<div class="card stat"><div style="display:flex;justify-content:space-between;'
        f'align-items:center"><span class="stat-name">{n}</span>'
        f'<span class="pill {pc}"><span class="d"></span>{pl}</span></div>'
        f'{_money(v, "m-xl")}<div class="stat-sub">{sub}</div></div>'
        for n, v, sub, pc, pl in stats)

    rows = []
    for i, c in enumerate(view["credits"]):
        pill = {"exact": "pill-ok", "bank_charge": "pill-ok",
                "narration_damaged": "pill-fly", "held_back": "pill-att"}.get(c["state"], "pill-att")
        rows.append(
            f'<tr class="crow" onclick="toggleCredit({i},this)">'
            f'<td style="width:24px">{CHEV}</td>'
            f'<td><div class="mono" style="font-weight:500">{escape(c["sid"])}</div>'
            f'<div class="mono" style="font-size:11px;color:var(--faint)">'
            f'{escape(c["narration"])}</div></td>'
            f'<td class="nw" style="color:var(--muted-foreground)">{c["date"] or "&mdash;"}</td>'
            f'<td class="num" style="color:var(--muted-foreground)">{c["n"]}</td>'
            f'<td class="num">{_money(c["gross"], "m-sm", "dim")}</td>'
            f'<td class="num">{_money(c["fee"], "m-sm", "dim")}</td>'
            f'<td class="num">{_money(c["credit"], "m-md")}</td>'
            f'<td class="num"><span class="pill {pill}"><span class="d"></span>'
            f'{STATE_LABEL[c["state"]]}</span></td></tr>'
            f'<tr id="d{i}" style="display:none"><td colspan="8" class="detail"></td></tr>')

    classes = "".join(
        f'<tr><td class="mono" style="font-weight:500">{escape(r["kind"])}</td>'
        f'<td class="num" style="color:var(--muted-foreground)">{r["expected"]}</td>'
        f'<td class="num" style="{"color:var(--amber);font-weight:600" if r["detected"] < r["expected"] else ""}">'
        f'{r["detected"]}</td></tr>'
        for r in view["by_class"])

    stake = "".join(
        f'<div class="stake"><span class="t">{escape(t)}</span>{_money(v, "m-md")}</div>'
        for t, v in view["at_stake"])

    drift = next(r for r in view["by_class"] if r["kind"] == "ROUNDING_DRIFT")
    drift_silent = drift["expected"] - drift["detected"]

    ag = view["agent"]
    # Every group is here rather than a pick of them, so the counts above can be checked by
    # scrolling, and the ones that took more than one turn lead because a rejection the model
    # then answered is what the section is for. sorted is stable, so trace order holds inside
    # each band
    shown = sorted(ag["groups"], key=lambda g: -len(g["turns"]))
    groups_html = ""
    for g in shown:
        # A turn the parser could not read as one of the three outcomes is a provider error the
        # loop retried, so it gets its own label, and the fallback used to call it a give up,
        # which said the model declined to name a cause when it was never asked. rt and not x,
        # because .x is the modal close button at 28px and round, and a label wearing it gets
        # crushed into that circle and spills over the text beside it
        tone = {"accepted": "a", "rejected": "r", "gave_up": "g"}
        label = {"accepted": "accepted", "rejected": "rejected", "gave_up": "gave up"}
        turns = "".join(
            f'<div class="turn"><span class="n">turn {t["n"]}</span>'
            f'<span class="o {tone.get(t["outcome"], "rt")}">'
            f'{label.get(t["outcome"], "retried")}'
            f'</span><span class="d{" raw" if t["outcome"] == "other" else ""}">'
            + (f'proposed <span class="mono">{escape(t["cause"])}</span> citing {t["citing"]} &mdash; ' if t["cause"] else "")
            + f'{escape(t["detail"])}</span></div>'
            for t in g["turns"])
        groups_html += (f'<div class="grp"><div class="h">{escape(g["group"])} &middot; '
                        f'{g["members"]} members</div>{turns}</div>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>unbundle — reconciliation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<div class="layout">
<aside>
  <div class="brand"><span class="brand-name">unbundle</span></div>
  <p class="side-lede">A bank credit is one payment covering many orders, and nothing in it says
    which orders those are. This works that out, and says what it could not place.</p>
  <dl class="side-meta"><dt>Run</dt><dd class="mono">seed {run["seed"]}</dd>
    <dd style="font-size:11px;color:rgba(255,255,255,.45);font-weight:400">
      {run["orders"]:,} orders &middot; as of {run["as_of"]}</dd></dl>
  <nav><a href="#"><span class="dot"></span>Reconciliation</a></nav>
  <p class="side-foot">One run, rendered as a static page. Every figure is read straight out of
    results/ledger.json or recomputed from the CSVs through the pipeline's own loader, so there is
    no second arithmetic here to disagree with the run.</p>
</aside>
<main><div class="page">

<header style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">
  <div><h1>Multi-source reconciliation</h1>
    <p class="lede">The orders, the gateway and the bank all record the same money and each one
      says a different total. The UTR that would tie a bank line to a settlement is a clean field
      on the gateway side and buried in free text on the bank side, so nothing joins them. This
      page does the join, shows which orders sit inside each bank credit, and names what it could
      not place.</p>
    <div class="sub">{srcs}</div></div>
  <button class="btn press" onclick="openFiles(0)">View source files</button>
</header>

<div style="margin:0 4px 12px"><h2>Cash position</h2>
  <p style="font-size:12px;color:var(--faint);margin-top:2px">
    Every captured payment sits in exactly one of these and the run fails if they do not add up to
    what was captured</p></div>

<div class="grid4">{stat_html}
  <div class="card stat"><div><span class="label">Credits matched</span>
    <div class="tnum" style="font-size:24px;font-weight:600;color:var(--navy);margin-top:4px">
      {by_count:.1f}%</div>
    <div class="stat-sub">{match["credits_matched"]} of {match["credits_total"]}</div></div>
    <div><span class="label">By value</span>
    <div class="tnum" style="font-size:24px;font-weight:600;color:var(--navy);margin-top:4px">
      {by_value:.1f}%</div></div></div>
</div>

<div class="strip">
  <span><span class="k">precision</span> <b class="tnum">{precision:.1f}%</b>
    <span class="k">({acc["flagged_real"]} of {acc["flagged_total"]} findings real)</span></span>
  <span><span class="k">money missed</span> {_money(acc["money_missed"], "m-sm")}</span>
  <span><span class="k">wrongly cleared</span> {_money(acc["money_wrongly_cleared"], "m-sm")}</span>
  <span><span class="k">healthy in-flight wrongly flagged</span>
    <b class="tnum">{acc["in_flight_wrongly_flagged"]}</b></span>
</div>

<div class="card">
  <div class="chead"><div><h2>Bank credits</h2>
    <p style="font-size:12px;color:var(--faint);margin-top:2px">
      Each credit unbundled into the payments inside it, and back to your own orders</p></div>
    <span class="tnum" style="font-size:12px;color:var(--faint)">
      {len(view["credits"])} matched credits{spanning_note}</span></div>
  <table class="rows"><thead><tr><th></th><th>Settlement / narration</th><th>Credited</th>
    <th class="num">Payments</th><th class="num">Gross</th><th class="num">Gateway fee</th>
    <th class="num">Bank credit</th><th class="num">State</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table>
</div>

<div class="card"><div class="chead"><div><h2>Money at stake</h2>
  <p style="font-size:12px;color:var(--faint);margin-top:2px">
    Each line is a different kind of exposure. They are deliberately not added up</p></div></div>
  <div class="hair"></div><div style="padding:14px 20px">{stake}</div></div>

<section>
  <div style="margin-bottom:12px"><h2 style="font-size:16px">What the agent did with the residue</h2>
  <p style="margin-top:6px;max-width:760px;font-size:13px;line-height:1.55;color:var(--muted-foreground)">
    Model <span class="mono" style="color:var(--foreground)">{escape(ag["model"] or "")}</span>
    sorted <b class="tnum">{ag["sorted"]}</b> of the <b class="tnum">{ag["flagged"]}</b> flagged
    findings into <b class="tnum">{ag["incidents"]}</b> candidate incidents and left
    <b class="tnum">{ag["standing_alone"]}</b> standing alone. Every cause it proposes is checked
    against the ledger before it counts, so one that cites records which do not add up is thrown
    out rather than printed &mdash; <b style="color:var(--pos)">{ag["accepted"]} accepted</b>,
    <b style="color:var(--neg)">{ag["rejected"]} rejected</b>, and
    <b style="color:var(--amber)">{ag["gave_up"]} gave up</b> rather than name a cause that did
    not fit. <b class="tnum">{ag["first_rejected"]}</b> first proposals were rejected and
    <b class="tnum">{ag["corrected"]}</b> of those were accepted on a later turn.
    The agent proposes; the arithmetic decides.</p></div>
  <button class="disclose press" id="agent-toggle" aria-expanded="false"
    aria-controls="agent-groups" onclick="toggleAgent(this)">{CHEV}
    <span>All {len(shown)} groups, the ones that took more than one turn first</span></button>
  <div id="agent-groups" hidden>{groups_html}</div>
</section>

<div class="card"><div class="chead"><div><h2>Detection by class</h2>
  <p style="font-size:12px;color:var(--faint);margin-top:2px">
    What the generator planted, against what the run found</p></div></div>
  <table class="rows"><thead><tr><th>Class</th><th class="num">Planted</th>
    <th class="num">Detected</th></tr></thead><tbody>{classes}</tbody></table>
  <div class="hair"></div>
  <div style="padding:14px 20px;font-size:12.5px;line-height:1.6;color:var(--muted-foreground)">
    <b>Both zeros are deliberate and they are not the same zero.</b>
    <span class="mono">GATEWAY_OUTAGE</span> reads 0 because the cascade never emits that kind,
    so there was nothing there to find. <span class="mono">HELD_BACK</span> reads 0 for the
    opposite reason: <span class="mono">reconcile.py:190</span> already works out which payment
    the settlement held back and names it to the paise, and then nothing flags it, so the page
    tells you that money arrived when it never came. <span class="mono">ROUNDING_DRIFT</span>
    reads <b class="tnum">{drift["detected"]}</b> of <b class="tnum">{drift["expected"]}</b>
    because only those sit on a settlement the matcher can price in full, and on the other
    <b class="tnum">{drift_silent}</b> it says nothing rather than compare a group it cannot
    price. <b class="tnum">{acc["unnamed_problems"]}</b> planted problems were never named at all.
  </div></div>

<footer><span>{"<b class='tnum'>" + run["records_per_second"] + "</b> records per second"
  if run["records_per_second"] else ""}</span>
  <span>Reproducible from seed <b class="mono">{run["seed"]}</b></span></footer>
</div></main></div>

<div id="modal"></div>
<script>
const D = {_embed({
    "groups": view["groups"],
    "sources": view["sources"],
    "rows": view["rows"],
    "money_columns": view["money_columns"],
    "credits": view["credits"],
})};

// Everything the Python side prints goes through html.escape and these tables did not, so a
// narration carrying an ampersand came out wrong and one carrying a tag came out as markup,
// on the page whose whole subject is what a bank writes into free text
function esc(v){{
  return String(v).replace(/[&<>"']/g, ch => (
    {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}
function rs(p){{
  if(p===null||p===undefined||p==='') return '\\u2014';
  const neg = p<0; p = Math.abs(p);
  let w = Math.floor(p/100), f = String(p%100).padStart(2,'0'), s = String(w);
  if(s.length>3){{ let h=s.slice(0,-3), t=s.slice(-3), g=[];
    while(h.length>2){{ g.unshift(h.slice(-2)); h=h.slice(0,-2); }}
    if(h) g.unshift(h); s=g.concat([t]).join(','); }}
  return (neg?'-':'')+s+'.'+f;
}}
function money(p,cls){{ if(p===null||p===undefined) return '<span style="color:var(--faint)">&mdash;</span>';
  return '<span class="money '+(cls||'m-sm')+'"><span class="sym">Rs</span><span class="val">'+rs(p)+'</span></span>'; }}

const PAY = D.rows['payments.csv'];
function toggleCredit(i, tr){{
  const row = document.getElementById('d'+i), cell = row.firstElementChild;
  if(row.style.display !== 'none'){{ row.style.display='none'; tr.classList.remove('open'); return; }}
  if(!cell.dataset.built){{
    const c = D.credits[i];
    let body = '<div class="detail-inner">' + document.getElementById('recon'+i).innerHTML;
    body += '<div class="pay-wrap"><div class="pay-scroll"><table class="rows"><thead><tr>'
      + '<th>Payment</th><th>Order</th><th>When</th><th>Method</th>'
      + '<th class="num">Gross</th><th class="num">Fee</th></tr></thead><tbody>';
    for(const idx of c.payment_rows){{
      const p = PAY[idx];
      body += '<tr><td class="mono">'+esc(p[0])+'</td><td class="mono">'+esc(p[1]||p[2]||'\\u2014')
        +'</td><td style="color:var(--muted-foreground)">'+esc(p[3].slice(0,16))
        +'</td><td style="color:var(--muted-foreground)">'+esc(p[5])
        +'</td><td class="num">'+money(p[7])+'</td><td class="num">'+money(p[8])+'</td></tr>';
    }}
    body += '</tbody></table></div><div class="cap"><span>all '+c.n
      +' payments inside this credit</span><span class="mono">'+esc(c.sid)+'</span></div></div>';
    body += '<div class="trace"><b>Back to your orders</b>'+c.trace.order_id
      +' matched on the gateway order id, '+c.trace.receipt+' only by receipt, '
      +c.trace.missing+' could not be found in orders.csv</div></div>';
    cell.innerHTML = body; cell.dataset.built = '1';
  }}
  row.style.display=''; tr.classList.add('open');
}}

let tab = 0;
function openFiles(i){{ tab = i; drawModal(); }}
function closeFiles(){{ document.getElementById('modal').innerHTML=''; }}
function toggleAgent(b){{
  const box = document.getElementById('agent-groups');
  const opening = box.hasAttribute('hidden');
  if(opening) box.removeAttribute('hidden'); else box.setAttribute('hidden','');
  b.classList.toggle('open', opening);
  b.setAttribute('aria-expanded', opening ? 'true' : 'false');
}}
function drawModal(){{
  const s = D.sources[tab], rows = D.rows[s.name];
  const isMoney = c => D.money_columns.includes(c);
  let h = '<div class="scrim" onclick="closeFiles()"><div class="modal" onclick="event.stopPropagation()">'
    + '<div class="mhead"><div><h2>Source files</h2><p style="font-size:12px;color:var(--faint)">'
    + esc(s.note) + '</p></div><button class="x" onclick="closeFiles()">&times;</button></div><div class="tabs">';
  D.groups.forEach(g=>{{
    h += '<div class="tabgrp"><div class="gname">'+esc(g)+'</div><div class="files">';
    D.sources.forEach((f,i)=>{{ if(f.group!==g) return;
      h += '<button class="tab press '+(i===tab?'on':'')+'" onclick="openFiles('+i+')">'
        + '<span class="mono">'+esc(f.name)+'</span><span class="c">'+f.rows.toLocaleString('en-IN')+'</span></button>'; }});
    h += '</div></div>';
  }});
  h += '</div><div class="mbody"><table><thead><tr><th class="num">#</th>';
  s.columns.forEach(c=>{{ h += '<th class="'+(isMoney(c)?'num':'')+'">'+esc(c)+'</th>'; }});
  h += '</tr></thead><tbody>';
  rows.forEach((r,ri)=>{{
    h += '<tr><td class="num" style="color:var(--faint)">'+(ri+1)+'</td>';
    r.forEach((cell,ci)=>{{
      const c = s.columns[ci];
      if(isMoney(c)) h += '<td class="num mono">'+rs(cell)+'</td>';
      else h += '<td class="'+(cell===''?'empty':'')+(c.endsWith('_id')||c.endsWith('_ref')||c==='utr'||c==='receipt'?' mono':'')+'">'
        + (cell===''?'\\u2014':esc(cell))+'</td>';
    }});
    h += '</tr>';
  }});
  h += '</tbody></table></div><div class="mfoot"><span>showing all <span class="tnum">'
    + rows.length.toLocaleString('en-IN') + '</span> rows in ' + esc(s.label)
    + '</span><span class="mono">'+esc(s.name)+'</span></div></div></div>';
  document.getElementById('modal').innerHTML = h;
}}
document.addEventListener('keydown', e => {{ if(e.key === 'Escape') closeFiles(); }});
</script>
<div style="display:none">{"".join(f'<div id="recon{i}">{_recon(c)}</div>' for i, c in enumerate(view["credits"]))}</div>
</body></html>"""


def main() -> None:
    view = build()
    OUT.write_text(render(view))
    size = OUT.stat().st_size
    print(f"wrote {OUT.relative_to(REPO)}  {size / 1024:.0f} KB")
    print(f"  {len(view['credits'])} credits, "
          f"{sum(len(r) for r in view['rows'].values()):,} source rows embedded")


if __name__ == "__main__":
    main()

