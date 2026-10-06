"""Drill-down selection and footer totals over the engine's audit snapshot."""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from app.models.domain import AuditRow, CalculationResult
from app.utils.normalization import normalize_text


@dataclass(frozen=True)
class AuditFilter:
    source_rows: frozenset[int] | None = None
    quarter: int | None = None  # 0 selects invalid periods.
    client_type: str | None = None
    category: str | None = None
    eligible: bool | None = None
    product: str = ''
    client: str = ''


@dataclass(frozen=True)
class AuditSelection:
    rows: tuple[AuditRow, ...]
    included_revenue: Decimal
    excluded_revenue: Decimal


def select_audit(result: CalculationResult, filters: AuditFilter = AuditFilter()) -> AuditSelection:
    def included(row: AuditRow) -> bool:
        s = row.shipment
        return ((filters.source_rows is None or s.source_row in filters.source_rows)
            and (filters.quarter is None or (row.quarter or 0) == filters.quarter)
            and (filters.client_type is None or (row.client_type or '') == filters.client_type)
            and (filters.category is None or (row.product.category if row.product else '') == filters.category)
            and (filters.eligible is None or row.eligible == filters.eligible)
            and normalize_text(filters.product) in normalize_text((s.product_raw or '') + ' ' + (row.product.canonical_name if row.product else ''))
            and normalize_text(filters.client) in normalize_text(s.client))
    rows = tuple(row for row in result.audit if included(row))
    with localcontext() as context:
        context.prec = 50
        return AuditSelection(rows, sum((r.shipment.revenue for r in rows if r.eligible), Decimal('0')),
                              sum((r.shipment.revenue for r in rows if not r.eligible), Decimal('0')))
