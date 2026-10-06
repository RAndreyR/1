"""Immutable inputs and auditable results for one employee/year calculation."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping
from types import MappingProxyType

from app.utils.normalization import CalculationInputError, decimal_value, normalize_text

ZERO = Decimal('0')
CATEGORIES = ('СБКС', 'ВМК', 'Latema', 'Novionta', 'ЭП')
CLIENT_TYPES = ('дистрибьютер', 'ЛПУ')


def _decimals(instance: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(instance, name)
        if value is not None:
            object.__setattr__(instance, name, decimal_value(value))


@dataclass(frozen=True)
class Shipment:
    source_row: int
    quarter: str | int | None
    client_type: str | None
    product_raw: str | None
    quantity: Decimal
    revenue: Decimal
    unit_price: Decimal | None = None
    client: str = ''
    legal_entity: str = ''
    bitrix_task: str = ''
    comment: str = ''

    def __post_init__(self) -> None:
        if type(self.source_row) is not int or self.source_row < 1:
            raise CalculationInputError('source_row must be a positive integer')
        _decimals(self, ('quantity', 'revenue', 'unit_price'))
        if self.quantity is None or self.revenue is None:
            raise CalculationInputError('Quantity and revenue are required')


@dataclass(frozen=True)
class Product:
    id: str
    canonical_name: str
    category: str
    price_lpu: Decimal | None
    price_distributor: Decimal | None

    def __post_init__(self) -> None:
        if not self.id or not normalize_text(self.canonical_name):
            raise CalculationInputError('Product id and name are required')
        category = {normalize_text(c): c for c in CATEGORIES}.get(normalize_text(self.category))
        if category is None:
            raise CalculationInputError('Unknown price-list category')
        object.__setattr__(self, 'category', category)
        _decimals(self, ('price_lpu', 'price_distributor'))
        if any(p is not None and p < ZERO for p in (self.price_lpu, self.price_distributor)):
            raise CalculationInputError('Thresholds cannot be negative')


@dataclass(frozen=True)
class Plans:
    q1: Decimal
    q2: Decimal
    q3: Decimal
    q4: Decimal

    def __post_init__(self) -> None:
        _decimals(self, ('q1', 'q2', 'q3', 'q4'))
        if any(p is None or p < ZERO for p in self.values):
            raise CalculationInputError('All four non-negative plans are required')

    @property
    def values(self) -> tuple[Decimal, ...]:
        return self.q1, self.q2, self.q3, self.q4

    @property
    def annual(self) -> Decimal:
        return sum(self.values, ZERO)


@dataclass(frozen=True)
class Rules:
    # Fractions, not percentage points: 0.90 = 90%.
    plan_gate: Decimal = Decimal('0.90')
    rate_block_1: Decimal = Decimal('0.05')
    rate_block_2: Decimal = Decimal('0.05')
    rate_block_3: Decimal = Decimal('0.015')
    rate_block_4: Decimal = Decimal('0.02')

    def __post_init__(self) -> None:
        _decimals(self, ('plan_gate', 'rate_block_1', 'rate_block_2', 'rate_block_3', 'rate_block_4'))
        if any(v is None or not ZERO <= v <= Decimal('1') for v in (self.plan_gate, *self.rates)):
            raise CalculationInputError('Gate and rates must be fractions between 0 and 1')

    @property
    def rates(self) -> tuple[Decimal, ...]:
        return self.rate_block_1, self.rate_block_2, self.rate_block_3, self.rate_block_4


@dataclass(frozen=True)
class AuditRow:
    shipment: Shipment
    quarter: int | None
    client_type: str | None
    product: Product | None
    match_kind: str | None
    actual_price_raw: Decimal | None
    actual_price_rounded: Decimal | None
    threshold_raw: Decimal | None
    threshold_rounded: Decimal | None
    price_delta: Decimal | None
    eligible: bool
    exclusion_reason: str | None
    block: int | None


@dataclass(frozen=True)
class PremiumBlock:
    number: int
    base: Decimal
    rate: Decimal
    premium: Decimal
    source_rows: tuple[int, ...]


@dataclass(frozen=True)
class QuarterResult:
    quarter: int
    actual: Decimal
    plan: Decimal
    gate_threshold: Decimal
    achievement: Decimal | None  # Ratio; undefined for a zero plan.
    quarter_pass: bool
    blocks: tuple[PremiumBlock, ...]
    calculated_premium: Decimal
    payable: Decimal
    actual_source_rows: tuple[int, ...]
    payable_reason: str


@dataclass(frozen=True)
class CalculationResult:
    plans: Plans
    rules: Rules
    quarters: tuple[QuarterResult, ...]
    annual_actual: Decimal
    annual_achievement: Decimal | None
    annual_pass: bool
    annual_calculated_premium: Decimal
    annual_payable: Decimal
    paid_q1_q3: Decimal
    annual_catch_up: Decimal | None
    green_totals: Mapping[tuple[int, str, str], Decimal]
    audit: tuple[AuditRow, ...]
    warnings: tuple[str, ...]
    paid_history: Mapping[int, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'green_totals', MappingProxyType(dict(self.green_totals)))
        object.__setattr__(self, 'paid_history', MappingProxyType(dict(self.paid_history)))

    @property
    def included_rows(self) -> tuple[AuditRow, ...]:
        return tuple(row for row in self.audit if row.eligible)

    @property
    def excluded_rows(self) -> tuple[AuditRow, ...]:
        return tuple(row for row in self.audit if not row.eligible)
