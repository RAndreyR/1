from decimal import Decimal
import pytest
from app.models.domain import Plans
from app.services.calculation_engine import calculate_kpi
from app.utils.normalization import CalculationInputError


def test_annual_exact_boundary_recovers_failed_quarters(shipment, product):
    rows = [shipment(source_row=q+2, quarter=q, revenue=Decimal(v)) for q,v in enumerate(['800','800','1000','1000'],1)]
    result = calculate_kpi(rows, [product], Plans('1000','1000','1000','1000'))
    assert result.annual_actual == 3600 and result.annual_pass
    assert [q.payable for q in result.quarters] == [0, 0, 50, 130]
    assert result.annual_calculated_premium == 180
    assert result.paid_q1_q3 == 50 and result.annual_catch_up == 130
    assert result.annual_payable == 180
    assert result.quarters[3].payable_reason == 'annual_catch_up'


def test_explicit_actual_paid_history_and_clamp(shipment, product, plans):
    history = {1: Decimal('60')}
    result = calculate_kpi([shipment()], [product], plans, paid_history=history)
    history[1] = Decimal('0')
    assert result.annual_calculated_premium == 50
    assert result.paid_q1_q3 == 60 and result.quarters[3].payable == 0
    assert result.paid_history[1] == 60
    assert result.annual_payable == 60


def test_explicit_empty_history_means_no_previous_payments(shipment, product, plans):
    result = calculate_kpi([shipment()], [product], plans, paid_history={})
    assert result.paid_q1_q3 == 0
    assert result.quarters[3].payable == 50


@pytest.mark.parametrize('q4,passed,payable', [('900',True,'45'),('899.99',False,'0')])
def test_annual_fails_then_q4_ordinary_gate(shipment, product, q4, passed, payable):
    result = calculate_kpi([shipment(quarter=4, revenue=Decimal(q4))], [product], Plans('1000','1000','1000','1000'))
    assert not result.annual_pass and result.annual_catch_up is None
    assert result.quarters[3].quarter_pass == passed
    assert result.quarters[3].payable == Decimal(payable)


def test_annual_pass_despite_q4_gate_failure(shipment, product):
    rows = [shipment(source_row=q+2, quarter=q, revenue=Decimal(v)) for q,v in enumerate(['1200','1200','1200','0'],1)]
    result = calculate_kpi(rows, [product], Plans('1000','1000','1000','1000'), paid_history={1:Decimal('40'),2:Decimal('40'),3:Decimal('40')})
    assert not result.quarters[3].quarter_pass and result.annual_pass
    assert result.quarters[3].payable == 60


@pytest.mark.parametrize('history', [{4: Decimal('1')}, {1: Decimal('-1')}, {True: Decimal('1')}, {1: 0.5}])
def test_invalid_history(shipment, product, plans, history):
    with pytest.raises(CalculationInputError):
        calculate_kpi([shipment()], [product], plans, paid_history=history)
