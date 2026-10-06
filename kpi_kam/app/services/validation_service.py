"""Validate business data while retaining excluded rows and their actual revenue."""
from collections.abc import Mapping, Iterable
from decimal import localcontext, ROUND_HALF_UP

from app.models.import_data import ExcelImport, ValidationIssue, ValidationReport
from app.services.calculation_engine import audit_shipment
from app.services.product_matcher import ProductMatcher
from app.utils.normalization import CalculationInputError, normalize_text


def validate_import(
    imported: ExcelImport, *, aliases: Mapping[str, str] | None = None,
    product_assignments: Mapping[int, str] | None = None,
    left_unmatched_rows: Iterable[int] = (),
) -> ValidationReport:
    issues = list(imported.issues)
    matcher = ProductMatcher(imported.products, aliases)
    assignments = dict(product_assignments or {})
    left = set(left_unmatched_rows)
    row_ids = {s.source_row for s in imported.shipments}
    if any(type(row) is not int or row not in row_ids for row in (*assignments, *left)):
        raise CalculationInputError('Mapping decision must reference an imported source row')
    if any(product_id not in matcher.by_id for product_id in assignments.values()):
        raise CalculationInputError('Assignment must reference a current price-list product')
    if assignments.keys() & left:
        raise CalculationInputError('A row cannot be both mapped and explicitly unmatched')
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        audit = tuple(audit_shipment(s, matcher, assignments.get(s.source_row)) for s in imported.shipments)
    sheet = next((layout.name for layout in imported.layouts if 'product_raw' in layout.columns), 'отгрузки')
    unmatched, unknown_types, invalid_quarters, missing_prices, missing_thresholds = [], [], [], [], []
    raw_products = {}
    for row in audit:
        source = row.shipment.source_row
        if source in left and row.product is not None:
            raise CalculationInputError('An existing product match cannot be left unmatched')
        checks = []
        if row.quarter is None:
            invalid_quarters.append(source)
            checks.append(('invalid_quarter', 'Некорректный период', 'quarter'))
        if row.client_type is None:
            unknown_types.append(source)
            checks.append(('unknown_client_type', 'Неизвестный тип клиента', 'client_type'))
        if row.product is None:
            unmatched.append(source)
            raw_products.setdefault(normalize_text(row.shipment.product_raw), row.shipment.product_raw)
            checks.append(('unmatched_product', 'Не сопоставлен продукт', 'product_raw'))
        if row.actual_price_raw is None:
            missing_prices.append(source)
            checks.append(('missing_price', 'Невозможно определить цену', 'unit_price'))
        if row.product is not None and row.client_type is not None and not row.threshold_raw:
            missing_thresholds.append(source)
            checks.append(('missing_threshold', 'Не задана зеленая зона', 'unit_price'))
        for code, message, field in checks:
            issues.append(ValidationIssue(code, message, 'warning', sheet, source, field))
    return ValidationReport(tuple(issues), len(imported.shipments), len(imported.products),
        len(audit)-len(unmatched), tuple(unmatched), tuple(raw_products.values()),
        tuple(invalid_quarters), tuple(unknown_types), tuple(missing_prices),
        tuple(missing_thresholds), tuple(row for row in unmatched if row not in left))
