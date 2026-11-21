"""Deterministic GBP cash-flow estimates for human review."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

ZERO = Decimal("0")
PENNY = Decimal("0.01")
MAX_MONEY = Decimal("1000000000")
SCENARIOS = ("conservative", "expected", "optimistic")
PATHS = ("whole", "refurbish", "parts")


def money(value):
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


def number(value, name, limit=MAX_MONEY, places=2):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not result.is_finite() or not ZERO <= result <= limit:
        raise ValueError(f"{name} must be between 0 and {limit}.")
    if result != result.quantize(Decimal(1).scaleb(-places)):
        raise ValueError(f"{name} allows up to {places} decimal places.")
    return result


def units(value, name, minimum=0):
    result = int(number(value, name, limit=1_000_000, places=0))
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return result


def percent(value, name):
    return number(value, name, limit=100) / 100


@dataclass(frozen=True)
class Fees:
    premium_pct: Decimal = ZERO
    fixed_fee: Decimal = ZERO
    hammer_vat_pct: Decimal = ZERO
    fee_vat_pct: Decimal = ZERO
    inbound_cost: Decimal = ZERO
    inbound_vat_pct: Decimal = ZERO
    first_bid: Decimal = Decimal("1")
    increment: Decimal = Decimal("1")

    def __post_init__(self):
        for name, value in vars(self).items():
            parser = percent if name.endswith("_pct") else number
            parser(value, name.replace("_", " "))
        if self.first_bid <= ZERO or self.increment <= ZERO:
            raise ValueError("First valid bid and bid increment must be positive.")

    def invoice(self, hammer):
        # The fixed fee and percentage premium share the same VAT rate.
        fee = money(hammer * self.premium_pct / 100 + self.fixed_fee)
        return {
            "hammer": hammer,
            "buyer_fee": fee,
            "hammer_vat": money(hammer * self.hammer_vat_pct / 100),
            "fee_vat": money(fee * self.fee_vat_pct / 100),
            "inbound": self.inbound_cost,
            "inbound_vat": money(self.inbound_cost * self.inbound_vat_pct / 100),
        }


@dataclass(frozen=True)
class Recovery:
    quantity: int = 0
    price: Decimal = ZERO
    unsold_pct: Decimal = ZERO
    unsold_value: Decimal = ZERO
    processing: Decimal = ZERO
    labour: Decimal = ZERO
    selling_fee_pct: Decimal = ZERO
    packing_postage: Decimal = ZERO
    returns_pct: Decimal = ZERO

    def __post_init__(self):
        units(self.quantity, "Recovery quantity")
        for name, value in vars(self).items():
            if name == "quantity":
                continue
            parser = percent if name.endswith("_pct") else number
            parser(value, name.replace("_", " "))
        if self.unsold_value > self.price:
            raise ValueError("Unsold clearance value cannot exceed regular resale price.")

    def totals(self):
        unsold = self.quantity * self.unsold_pct / 100
        regular = self.quantity - unsold
        gross = regular * self.price + unsold * self.unsold_value
        # Clearance quantities use the same fee/postage allowance only if cleared for money.
        shipments = regular + (unsold if self.unsold_value else ZERO)
        return {
            "gross": gross,
            "processing": self.quantity * self.processing,
            "labour": self.quantity * self.labour,
            "selling_fee": gross * self.selling_fee_pct / 100,
            "packing_postage": shipments * self.packing_postage,
            "returns": gross * self.returns_pct / 100,
        }


@dataclass(frozen=True)
class Scenario:
    whole: Recovery
    refurbish: Recovery
    parts: Recovery
    failed: int
    failed_unit_cost: Decimal = ZERO
    overhead: Decimal = ZERO
    contingency: Decimal = ZERO
    margin_pct: Decimal = ZERO

    def __post_init__(self):
        units(self.failed, "Failed/unrecoverable quantity")
        for name in ("failed_unit_cost", "overhead", "contingency"):
            number(getattr(self, name), name.replace("_", " "))
        percent(self.margin_pct, "Required margin")


def maximum_bid(fees, budget):
    """Highest affordable point on first_bid + n * increment."""
    if sum(fees.invoice(fees.first_bid).values()) > budget:
        return None
    # All charges are nonnegative, so the landed budget bounds the hammer price.
    low = 0
    high = int(((budget - fees.first_bid) / fees.increment).to_integral_value(
        rounding=ROUND_FLOOR
    ))
    while low < high:
        mid = (low + high + 1) // 2
        hammer = fees.first_bid + mid * fees.increment
        if sum(fees.invoice(hammer).values()) <= budget:
            low = mid
        else:
            high = mid - 1
    return fees.first_bid + low * fees.increment


def evaluate(quantity, fees, scenario, hammer=None):
    units(quantity, "Lot quantity", minimum=1)
    allocated = sum(getattr(scenario, path).quantity for path in PATHS) + scenario.failed
    if allocated != quantity:
        raise ValueError(f"Allocate exactly {quantity} units across the four outcomes (allocated {allocated}).")
    totals = [getattr(scenario, path).totals() for path in PATHS]
    gross = money(sum(row["gross"] for row in totals))
    costs = {
        key: money(sum(row[key] for row in totals))
        for key in totals[0] if key != "gross"
    }
    costs.update(
        failed=money(scenario.failed * scenario.failed_unit_cost),
        overhead=scenario.overhead,
        contingency=scenario.contingency,
    )
    operations = sum(costs.values())
    required_profit = money(gross * scenario.margin_pct / 100)
    ceiling = None if gross <= ZERO else maximum_bid(fees, gross - operations - required_profit)
    result = {
        "gross": gross,
        "operating_costs": operations,
        "costs": costs,
        "required_profit": required_profit,
        "maximum_bid": ceiling,
        "landed": None,
        "invoice": None,
        "profit": None,
        "roi_pct": None,
        "status": "No viable bid" if ceiling is None else "Bid price unknown",
    }
    if hammer is not None:
        number(hammer, "Hammer or asking price")
        invoice = fees.invoice(hammer)
        landed = sum(invoice.values())
        outlay = landed + operations
        profit = gross - outlay
        if profit <= ZERO:
            status = "Unprofitable" if profit < ZERO else "Break-even"
        elif profit < required_profit:
            status = "Below required margin"
        else:
            status = "Meets required margin"
        result.update(
            invoice=invoice, landed=landed, profit=profit, status=status,
            roi_pct=money(100 * profit / outlay) if outlay else None,
        )
    return result
