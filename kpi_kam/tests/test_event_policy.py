from decimal import Decimal
import pytest
from app.models.domain import Plans, Product, Rules
from app.models.events import ShipmentEvent, ReturnEvent, RolePolicy
from app.services.event_calculation import calculate_events, calls_bonus
from app.services.sales_importer import client_identity


def sale(month=1, revenue='1000', quantity='10', line='line', raw='Товар', eid='s1'):
    return ShipmentEvent(eid, line, 'контракты ЭП', 40, 'Трофимов', 2026, month,
                         raw, Decimal(quantity), Decimal(revenue), 'Клиент', 'Дистрибьютер')


def returned(month=7, revenue='200', quantity='2', line='line', eid='r1'):
    return ReturnEvent(eid, line, 'контракты ЭП', 40, 'Трофимов', 2026, month,
                       'Товар', Decimal(quantity), Decimal(revenue), 'Клиент', 'Дистрибьютер')


PRODUCT = Product('p', 'Товар', 'ВМК', '100', '100')
PLANS = Plans('1000', '0', '100', '0')


@pytest.mark.parametrize('db', ['', '-', '—', ' НЕТ ', 'не указан', 'не указано',
                                  'не задан', 'нет данных', 'n/a', 'NA', 'none', 'null'])
def test_empty_db_means_lpu(db):
    assert client_identity(db, 'Больница') == ('ЛПУ', 'Больница')


def test_real_db_means_distributor():
    assert client_identity('ООО Дистрибьютер', 'Больница') == ('дистрибьютер', 'ООО Дистрибьютер')


@pytest.mark.parametrize('fact,amount', [(600,'0'), (675,'27000'), (700,'28000'),
                                        (750,'30000'), (900,'30000')])
def test_support_calls_formula(fact, amount):
    assert calls_bonus(Decimal(fact), Decimal('750'), RolePolicy.support()) == Decimal(amount)


def test_price_category_only_from_product_and_rounding_equality():
    product = Product('p', 'ВМК НМ Плюс', 'ЭП', '100.04', '100.04')
    result = calculate_events([sale(raw='ВМК НМ Плюс',revenue='1000.4')],[],[product],
                              Plans('0','0','0','0'),RolePolicy.kam())
    assert result.result.audit[0].block == 3
    assert result.result.quarters[0].calculated_premium == Decimal('15.006')


def test_zero_quantity_has_actual_but_no_eligibility():
    result=calculate_events([sale(quantity='0')],[],[PRODUCT],PLANS,RolePolicy.kam())
    assert result.result.quarters[0].actual == 1000
    assert not result.result.audit[0].eligible


def test_unpaid_return_changes_old_base_and_current_actual():
    result=calculate_events([sale()],[returned()],[PRODUCT],PLANS,RolePolicy.kam())
    assert result.result.quarters[0].blocks[0].base == 800
    assert result.result.quarters[0].actual == 1000
    assert result.result.quarters[2].actual == -200
    assert result.quarters[2].clawback == 0


def test_fifo_partial_return_and_unallocated_remainder():
    result=calculate_events([sale(eid='s1',quantity='2',revenue='200'),
                             sale(month=2,eid='s2',quantity='3',revenue='300')],
                            [returned(quantity='7',revenue='700')],[PRODUCT],PLANS,RolePolicy.kam())
    assert [(a.shipment_id,a.quantity) for a in result.allocations] == [('s1',Decimal(2)),('s2',Decimal(3))]
    assert result.unallocated_returns == ('r1',)
    assert result.result.quarters[2].actual == -700


def test_support_rates_calls_gate_and_q4_no_double_count():
    product=Product('p','Товар','ЭП','100','100')
    result=calculate_events([sale(month=q*3,eid=f's{q}') for q in range(1,5)],[],[product],
                           Plans('2000','1000','1000','1000'),RolePolicy.support(),
                           calls_facts=(Decimal(750),)*4,paid_amounts={1:Decimal(0),2:Decimal(30050),3:Decimal(30050)})
    assert result.quarters[0].calls_bonus == 30000
    assert result.quarters[0].payable == 0
    # Annual actual 4000 < 4500: ordinary Q4 only.
    assert result.quarters[3].payable == 30050
    annual=calculate_events([sale(month=q*3,eid=f's{q}') for q in range(1,5)],[],[product],
                           Plans('1000','1000','1000','1000'),RolePolicy.support(),
                           calls_facts=(Decimal(750),)*4,paid_amounts={1:Decimal(30050),2:Decimal(30050),3:Decimal(30050)})
    assert annual.result.annual_calculated_premium == 120200
    assert annual.quarters[3].payable == 30050
