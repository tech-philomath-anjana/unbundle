from dataclasses import dataclass
from typing import Literal, get_args

# LabelKind are strings the synthetic data uses as labels, includes problems as well as ordinary events. Both are recorded because a matcher can be wrong 
# about the normal thing by flagging it or about the problem by missing it and neither can be checked against the truth if the label does not exist 
LabelKind = Literal[
    "BANK_FEE_DEDUCTED",
    "BATCHED",
    "CHARGEBACK_LATER",
    "DUPLICATE_PAYMENT",
    "FAILED_RETRY",
    "FEE_MISMATCH",
    "GATEWAY_OUTAGE",
    # A payment the export assigns to a settlement whose transfer left it out. Kept apart from MISSING_SETTLEMENT 
    # because that one says never settled and this payment names a settlement that did settle and a detail line 
    # contradicting its own record is how three answer key bugs in this project started
    "HELD_BACK",
    "IN_FLIGHT",
    "MANGLED_UTR",
    "MISSING_SETTLEMENT",
    "NETWORK_UNKNOWN",
    "ORDER_ID_MISSING",
    "PARTIAL_REFUND",
    "ROUNDING_DRIFT",
    "T_PLUS_TWO",
    "UNKNOWN_CREDIT",
    "UNLINKED_ORDER",
]

ORDINARY: frozenset[LabelKind] = frozenset(
    {
        "BATCHED",
        # Razorpay deducts a chargeback at onset from whichever settlement is open, so a settlement can be short because of a sale from months back and 
        # that sale is in no file
        "CHARGEBACK_LATER",
        # A failed attempt moves no money and has no fee on it, the customer pays again on a new attempt
        "FAILED_RETRY",
        "IN_FLIGHT",
        # No rate is published for the unknown network so the fee on it cannot be checked, the payment itself still reconciles and only the 
        # charge on it stays unverified
        "NETWORK_UNKNOWN",
        "ORDER_ID_MISSING",
        # A refund raised after the payment settled is deducted from a later credit, so that credit is short and the adjustment against it says why
        "PARTIAL_REFUND",
        "ROUNDING_DRIFT",
        "T_PLUS_TWO",
    }
)

# Used to catch typo, if there is a typo in a kind, it will not get classified and does not match with anything 
assert ORDINARY <= frozenset(get_args(LabelKind))

# If someone adds another kind in the LabelKind, until and unless it is explicitly added to the ORDINARY then it is considered a problem automatically, 
# so that the matcher can be tested against it 
PROBLEMS: frozenset[LabelKind] = frozenset(get_args(LabelKind)) - ORDINARY

@dataclass(frozen=True, slots=True)
class Label:
    kind: LabelKind
    entity_id: str
    detail: str