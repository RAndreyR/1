"""Pure Decimal engine: no Excel, database, GUI or process state dependencies."""
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.models.domain import (
    AuditRow, CalculationResult, CATEGORIES, CLIENT_TYPES, Plans, PremiumBlock,
    Product, QuarterResult, Rules, Shipment, ZERO,
)
from app.services.product_matcher import ProductMatcher
from app.utils.normalization import (
    CalculationInputError, decimal_value, normalize_client_type, normalize_quarter, price_1dp,
)


def audit_shipment(shipment: Shipment, matcher: ProductMatcher) -> AuditRow:
    quarter = normalize_quarter(shipment.quarter)
    client_type = normalize_client_type(shipment.client_type)
    product, match_kind = matcher.match(shipment.product_raw)
    actual = shipment.unit_price
    if actual is None or actual == ZERO:
        actual = shipment.revenue / shipment.quantity if shipment.quantity != ZERO else None
    threshold = None
    if product is not None and client_type is not None:
        threshold = product.price_lpu if client_type == 'ЛПУ' else product.price_distributor
    actual_rounded = price_1dp(actual) if actual is not None else None
    threshold_rounded = price_1dp(threshold) if threshold is not None else None
    delta = actual_rounded - threshold_rounded if actual_rounded is not None and threshold_rounded is not None else None
    reason = None
    if quarter is None:
        reason = 'Некорректный период'
    elif client_type is None:
        reason = 'Неизвестный тип клиента'
    elif product is None:
        reason = 'Не сопоставлен продукт'
    elif actual is None:
        reason = 'Невозможно определить цену'
    elif threshold is None or threshold == ZERO:
        reason = 'Не задана зеленая зона'
    elif delta < ZERO:
        reason = 'Цена ниже зеленой зоны'
    block = None
    if reason is None:
        block = (1 if client_type == 'дистрибьютер' else 2) if product.category in ('СБКС', 'ВМК') else (3 if client_type == 'дистрибьютер' else 4)
    return AuditRow(shipment, quarter, client_type, product, match_kind, actual,
                    actual_rounded, threshold, threshold_rounded, delta,
                    reason is None, reason, block)


def calculate_kpi(
    shipments: Iterable[Shipment], products: Iterable[Product], plans: Plans,
    rules: Rules = Rules(), paid_history: Mapping[int, Decimal] | None = None,
    *, aliases: Mapping[str, str] | None = None,
) -> CalculationResult:
    """Calculate one employee/year.

    Omitted paid_history assumes gated Q1-Q3 amounts were paid. An explicit
    history uses zero for omitted quarters. All inputs and snapshots stay immutable.
    Fractional kopecks are retained; round only when formatting money.
    """
    if not isinstance(plans, Plans) or not isinstance(rules, Rules):
        raise CalculationInputError('Plans and Rules models are required')
    # Isolate from callers changing the global Decimal context.
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        return _calculate(shipments, products, plans, rules, paid_history, aliases)


def _calculate(
    shipments: Iterable[Shipment], products: Iterable[Product], plans: Plans,
    rules: Rules, paid_history: Mapping[int, Decimal] | None,
    aliases: Mapping[str, str] | None,
) -> CalculationResult:
    matcher = ProductMatcher(products, aliases)
    audit = tuple(audit_shipment(s, matcher) for s in shipments)
    if len({a.shipment.source_row for a in audit}) != len(audit):
        raise CalculationInputError('Duplicate source row in a single import')
    green = {(q, t, c): ZERO for q in range(1, 5) for t in CLIENT_TYPES for c in CATEGORIES}
    for row in audit:
        if row.eligible:
            green[row.quarter, row.client_type, row.product.category] += row.shipment.revenue
    quarters = []
    for q, plan in enumerate(plans.values, 1):
        rows = tuple(a for a in audit if a.quarter == q)
        actual = sum((a.shipment.revenue for a in rows), ZERO)
        blocks = []
        for number, rate in enumerate(rules.rates, 1):
            included = tuple(a for a in rows if a.block == number)
            base = sum((a.shipment.revenue for a in included), ZERO)
            blocks.append(PremiumBlock(number, base, rate, base * rate,
                                       tuple(a.shipment.source_row for a in included)))
        premium = sum((b.premium for b in blocks), ZERO)
        passed = actual >= plan * rules.plan_gate
        quarters.append(QuarterResult(q, actual, plan, plan * rules.plan_gate,
                        actual / plan if plan else None, passed, tuple(blocks), premium,
                        max(ZERO, premium) if passed else ZERO,
                        tuple(a.shipment.source_row for a in rows),
                        'quarter_gate' if passed else 'gate_failed'))
    history = {q: quarters[q - 1].payable for q in range(1, 4)} if paid_history is None else {}
    if paid_history is not None:
        for q, value in paid_history.items():
            if type(q) is not int or q not in (1, 2, 3):
                raise CalculationInputError('Paid history accepts only Q1-Q3')
            amount = decimal_value(value)
            if amount < ZERO:
                raise CalculationInputError('Already paid premiums cannot be negative')
            history[q] = amount
        history = {q: history.get(q, ZERO) for q in range(1, 4)}
    annual_actual = sum((q.actual for q in quarters), ZERO)
    annual_premium = sum((q.calculated_premium for q in quarters), ZERO)
    annual_pass = annual_actual >= plans.annual * rules.plan_gate
    paid = sum(history.values(), ZERO)
    catch_up = max(ZERO, annual_premium - paid) if annual_pass else None
    if annual_pass:
        quarters[3] = replace(quarters[3], payable=catch_up, payable_reason='annual_catch_up')
    warnings = tuple(f'Строка {a.shipment.source_row}: {a.exclusion_reason}' for a in audit if not a.eligible)
    return CalculationResult(plans, rules, tuple(quarters), annual_actual,
             annual_actual / plans.annual if plans.annual else None, annual_pass,
             annual_premium, paid + quarters[3].payable, paid, catch_up,
             green, audit, warnings, history)
