from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from unbundle.money import Paise

# Razorpay's own strings from https://razorpay.com/docs/api/settlements/fetch-recon/ for Method, CardNetwork and CardType,
# copied as they come so a real export loads without a normalising step
Method = Literal["card", "netbanking", "wallet", "emi", "upi"]
# MDRs for different card networks are different, so the network is important to know for a payment and 
# the card type is important for the emi 
CardNetwork = Literal["Visa", "MasterCard", "RuPay", "American Express", "Diners Club", "Maestro", "unknown"]
CardType = Literal["debit", "credit"]
# Referred https://razorpay.com/docs/api/orders/entity/ 
OrderStatus = Literal["created", "attempted", "paid"]
# Referred https://razorpay.com/docs/api/payments/entity/ 
PaymentStatus = Literal["created", "authorized", "captured", "refunded", "failed"]
# Referred https://razorpay.com/docs/api/settlements/entity/ 
SettlementStatus = Literal["created", "processed", "failed"]
# Refund and chargeback are categories
AdjustmentKind = Literal["refund", "chargeback"]

@dataclass(frozen=True, slots=True)
class Order:
    # order_ref is the merchant side of record, so an order that matches nothing on the gateway
    # can still be named in the report
    order_ref: str
    # From https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/#123-checkout-options,
    # order id is mandatory at Standard Checkout so the gateway always has one, null here
    # because the merchant only has it if the integration saved it
    order_id: str | None
    # From https://razorpay.com/docs/api/orders/create/, receipt is optional at order creation
    # so it can be missing, but unique when set so it works as a join when order_id was not saved
    receipt: str | None
    placed_at: datetime
    amount: Paise
    status: OrderStatus
    # From https://razorpay.com/docs/api/orders/entity/, Razorpay counts a failed payment and a successful 
    # payment as attempts, the number of attempts does not mean duplicate payments so the status will show 
    # which one made it
    attempts: int

@dataclass(frozen=True, slots=True)
class Payment:
    payment_id: str
    order_id: str
    order_receipt: str | None
    # Time is recorded for any payment whatever the status but T+2 condition is only for when the payment 
    # status is captured 
    happened_at: datetime
    status: PaymentStatus
    method: Method
    card_network: CardNetwork | None
    card_type: CardType | None
    amount: Paise
    # Fee is inclusive of GST and tax is nothing but the GST 
    fee: Paise
    tax: Paise
    settlement_id: str | None

    @property
    def net(self) -> Paise:
        return self.amount - self.fee

@dataclass(frozen=True, slots=True)
class Settlement:
    settlement_id: str
    utr: str
    settled_at: datetime
    # Amount in here is the one that Razorpay has to send to the merchant after deducting the fees and tax, so  
    # subtracting them again from the amount would deduct them twice 
    amount: Paise
    fees: Paise
    tax: Paise
    status: SettlementStatus

@dataclass(frozen=True, slots=True)
class BankLine:
    # Bank statement exports are inconsistent, some banks give a date and a time while some just gives a date,
    # datetime here would store the time as 00:00:00 for every row that has no time
    txn_date: date
    narration: str
    credit: Paise
    debit: Paise

@dataclass(frozen=True, slots=True)
class Adjustment:
    adjustment_id: str
    kind: AdjustmentKind
    payment_id: str
    amount: Paise
    raised_at: datetime
    settlement_id: str | None