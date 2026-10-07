"""Immutable monthly inputs and versioned workflow/payment snapshots."""
from dataclasses import dataclass, field
from decimal import Decimal
from app.models.domain import AuditRow, CalculationResult, Plans, Product, Rules, ZERO
from app.models.import_data import ValidationIssue, SheetLayout
from app.utils.normalization import decimal_value, CalculationInputError


@dataclass(frozen=True)
class ShipmentEvent:
    event_id: str
    line_key: str
    source_sheet: str
    source_row: int
    manager: str
    year: int
    month: int
    product_raw: str
    quantity: Decimal
    revenue: Decimal
    lpu: str = ''
    db: str = ''
    legal_entity: str = ''
    contract: str = ''
    fo: str = ''
    region: str = ''

    def __post_init__(self):
        if not self.event_id or not self.line_key or not 1 <= self.month <= 12 or not 1900 <= self.year <= 2100:
            raise CalculationInputError('Некорректный идентификатор или месяц события')
        for name in ('quantity','revenue'):
            value=decimal_value(getattr(self,name))
            if value < ZERO:
                raise CalculationInputError('Отрицательные значения должны быть отдельным возвратом')
            object.__setattr__(self,name,value)

    @property
    def quarter(self):
        return (self.month-1)//3+1


@dataclass(frozen=True)
class ReturnEvent(ShipmentEvent):
    """Positive return magnitude; impact on the actual is negative."""


@dataclass(frozen=True)
class Employee:
    id: int
    name: str
    active: bool = True


@dataclass(frozen=True)
class YearProfile:
    employee_id: int
    year: int
    role: str
    plans: Plans
    calls_plans: tuple[Decimal, ...] = (Decimal('750'),)*4
    calls_facts: tuple[Decimal, ...] = (ZERO,)*4

    def __post_init__(self):
        if self.role not in ('KAM','SUPPORT') or not 1900 <= self.year <= 2100:
            raise CalculationInputError('Укажите год и должность KAM/SUPPORT')
        for name in ('calls_plans','calls_facts'):
            values=tuple(decimal_value(v) for v in getattr(self,name))
            if len(values)!=4 or any(v<0 for v in values) or (name=='calls_plans' and any(v==0 for v in values)):
                raise CalculationInputError('Задайте четыре корректных значения звонков')
            object.__setattr__(self,name,values)


@dataclass(frozen=True)
class RolePolicy:
    role: str = 'KAM'
    rules: Rules = Rules()
    calls_enabled: bool = False
    calls_gate: Decimal = Decimal('0.90')
    calls_max: Decimal = Decimal('30000')
    default_calls_plan: Decimal = Decimal('750')

    def __post_init__(self):
        if self.role not in ('KAM','SUPPORT'):
            raise CalculationInputError('Неизвестная должность')
        for name in ('calls_gate','calls_max','default_calls_plan'):
            object.__setattr__(self,name,decimal_value(getattr(self,name)))
        if not 0 <= self.calls_gate <= 1 or self.calls_max<0 or self.default_calls_plan<=0:
            raise CalculationInputError('Некорректные параметры звонков')

    @classmethod
    def kam(cls):
        return cls()

    @classmethod
    def support(cls):
        return cls('SUPPORT', Rules(rate_block_3='0.05',rate_block_4='0.05'),True)


@dataclass(frozen=True)
class PriceVersion:
    id: int
    source_file: str
    source_hash: str
    imported_at: str
    products: tuple[Product, ...]


@dataclass(frozen=True)
class SalesImport:
    source_file: str
    source_hash: str
    year: int
    shipments: tuple[ShipmentEvent, ...]
    returns: tuple[ReturnEvent, ...]
    issues: tuple[ValidationIssue, ...]
    layouts: tuple[SheetLayout, ...]
    column_requests: tuple[str, ...] = ()
    excluded_tender_rows: int = 0

    @property
    def errors(self):
        return tuple(i for i in self.issues if i.severity=='error')


@dataclass(frozen=True)
class EventAudit:
    event: ShipmentEvent
    audit: AuditRow
    role: str
    rate: Decimal


@dataclass(frozen=True)
class ReturnAllocation:
    return_id: str
    shipment_id: str
    quantity: Decimal
    revenue: Decimal
    eligible: bool
    product: Product | None
    category: str | None
    client_type: str | None
    block: int | None
    rate: Decimal
    original_quarter: int
    original_year: int
    original_role: str
    paid: bool
    clawback: Decimal
    target_year: int
    target_quarter: int


@dataclass(frozen=True)
class PeriodSummary:
    quarter: int
    positive_sales: Decimal
    returns: Decimal
    actual: Decimal
    calls_plan: Decimal
    calls_fact: Decimal
    calls_bonus: Decimal
    calculated_premium: Decimal
    base_payable: Decimal
    clawback: Decimal
    carried_in: Decimal
    carried_out: Decimal
    payable: Decimal
    paid: Decimal
    status: str


@dataclass(frozen=True)
class ReturnReview:
    event: ReturnEvent
    remaining_quantity: Decimal
    remaining_revenue: Decimal
    clawback: Decimal
    status: str


@dataclass(frozen=True)
class EventCalculation:
    result: CalculationResult
    event_audit: tuple[EventAudit, ...]
    returns: tuple[ReturnEvent, ...]
    allocations: tuple[ReturnAllocation, ...]
    quarters: tuple[PeriodSummary, ...]
    unallocated_returns: tuple[str, ...]
    role: str
    year: int
    return_reviews: tuple[ReturnReview, ...] = ()


@dataclass(frozen=True)
class WorkspaceSnapshot:
    id: int
    employee: Employee
    profile: YearProfile
    policy: RolePolicy
    price: PriceVersion
    sales_import_id: int
    calculation: EventCalculation
    calculated_at: str
    status: str = 'calculated'


@dataclass(frozen=True)
class PaymentRecord:
    id: int
    employee_id: int
    year: int
    quarter: int
    amount: Decimal
    paid_at: str
    snapshot: WorkspaceSnapshot
