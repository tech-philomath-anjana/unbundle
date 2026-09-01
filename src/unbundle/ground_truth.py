from dataclasses import dataclass
from typing import Literal, get_args

# LabelKind are strings the synthetic data uses as labels, includes problems as well as ordinary events. Both are
# recorded because a matcher can be wrong about the normal thing by flagging it or about the problem by missing it and
# neither can be checked against the truth if the label does not exist 
LabelKind = Literal[
    "BANK_FEE_DEDUCTED",
    "BATCHED",
    "FEE_MISMATCH",
    "GATEWAY_OUTAGE",
    "IN_FLIGHT",
    "MANGLED_UTR",
    "MISSING_SETTLEMENT",
    "ORDER_ID_MISSING",
    "ROUNDING_DRIFT",
    "T_PLUS_TWO",
    "UNKNOWN_CREDIT",
    "UNLINKED_ORDER",
]

ORDINARY: frozenset[LabelKind] = frozenset(
    {"BATCHED", "IN_FLIGHT", "ORDER_ID_MISSING", "ROUNDING_DRIFT", "T_PLUS_TWO"}
)

# Used to catch typo, if there is a typo in a kind, it will not get classified and does not match with anything 
assert ORDINARY <= frozenset(get_args(LabelKind))

# If someone adds another kind in the LabelKind, until and unless it is explicitly added to the ORDINARY then 
# it is considered a problem automatically, so that the matcher can be tested against it 
PROBLEMS: frozenset[LabelKind] = frozenset(get_args(LabelKind)) - ORDINARY

@dataclass(frozen=True, slots=True)
class Label:
    kind: LabelKind
    entity_id: str
    detail: str