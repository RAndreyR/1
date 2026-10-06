from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.models.domain import Plans, Rules
from app.services.calculation_engine import calculate_kpi


@pytest.mark.parametrize('quarter', [1, 2, 3])
@pytest.mark.parametrize('revenue,passed', [('899.99', False), ('900', True), ('900.01', True)])
def test_gate_boundary(shipment, product, quarter, revenue, passed):
    result = calculate_kpi([shipment(quarter=quarter, revenue=Decimal(revenue))], [product], Plans('1000','1000','1000','1000'))
    q = result.quarters[quarter-1]
    assert q.gate_threshold == Decimal('900')
    assert q.quarter_pass == passed
    assert q.calculated_premium == Decimal(revenue) * Decimal('0.05')
    assert q.payable == (q.calculated_premium if passed else 0)
    assert q.achievement == Decimal(revenue) / Decimal('1000')


def test_custom_rules_and_snapshots(shipment, product):
    plans = Plans('1000', '0', '0', '0')
    rules = Rules(plan_gate='0.80', rate_block_1='0.07')
    result = calculate_kpi([shipment(revenue=Decimal('800'))], [product], plans, rules)
    assert result.quarters[0].payable == Decimal('56')
    assert result.plans == plans and result.rules == rules
    with pytest.raises(FrozenInstanceError):
        result.plans.q1 = Decimal('0')
    with pytest.raises(TypeError):
        result.green_totals[1, 'ЛПУ', 'СБКС'] = Decimal('0')


def test_zero_plans_and_empty_shipments(plans):
    result = calculate_kpi([], [], plans)
    assert result.annual_actual == 0 and result.annual_payable == 0
    assert result.annual_achievement is None
    assert all(q.quarter_pass and q.achievement is None for q in result.quarters)


def test_unknown_product_helps_gate_but_not_premium(shipment, product):
    rows = [shipment(revenue=Decimal('100')), shipment(source_row=4, product_raw='Unknown', revenue=Decimal('800'))]
    result = calculate_kpi(rows, [product], Plans('1000','1000','1000','1000'))
    assert result.quarters[0].actual == 900
    assert result.quarters[0].payable == 5
    assert result.quarters[0].actual_source_rows == (3, 4)
