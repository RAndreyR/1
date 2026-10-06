from dataclasses import replace
from decimal import Decimal, localcontext
import pytest
from app.models.domain import Product, Rules
from app.services.calculation_engine import calculate_kpi
from app.utils.normalization import CalculationInputError


@pytest.mark.parametrize('actual,threshold,rounded_actual,rounded_threshold,eligible', [
    ('154.04','154.04','154.0','154.0',True),
    ('154.04','154.06','154.0','154.1',False),
    ('154.05','154.05','154.1','154.1',True),
    ('154.049','154.05','154.0','154.1',False),
    ('155','154','155.0','154.0',True),
])
def test_decimal_comparison(shipment, product, plans, actual, threshold, rounded_actual, rounded_threshold, eligible):
    product = replace(product, price_distributor=Decimal(threshold))
    result = calculate_kpi([shipment(unit_price=Decimal(actual))], [product], plans)
    audit = result.audit[0]
    assert audit.eligible == eligible
    assert audit.actual_price_raw == Decimal(actual)
    assert audit.threshold_raw == Decimal(threshold)
    assert audit.actual_price_rounded == Decimal(rounded_actual)
    assert audit.threshold_rounded == Decimal(rounded_threshold)
    assert audit.price_delta == Decimal(rounded_actual) - Decimal(rounded_threshold)
    assert bool(result.included_rows) == eligible
    assert result.quarters[0].blocks[0].base == (Decimal('1000') if eligible else 0)


@pytest.mark.parametrize('price', [None, Decimal('0')])
def test_price_fallback(shipment, product, plans, price):
    result = calculate_kpi([shipment(unit_price=price, quantity=Decimal('3'), revenue=Decimal('300.15'))], [product], plans)
    assert result.audit[0].actual_price_raw == Decimal('100.05')
    assert result.audit[0].actual_price_rounded == Decimal('100.1')
    assert result.quarters[0].calculated_premium == Decimal('15.0075')


@pytest.mark.parametrize('override,reason', [
    ({'quantity': Decimal('0'), 'unit_price': None}, 'Невозможно определить цену'),
    ({'quarter': '5 кв.'}, 'Некорректный период'),
    ({'client_type': 'unknown'}, 'Неизвестный тип клиента'),
    ({'product_raw': None}, 'Не сопоставлен продукт'),
])
def test_exclusion_audit(shipment, product, plans, override, reason):
    result = calculate_kpi([shipment(**override)], [product], plans)
    assert result.excluded_rows[0].exclusion_reason == reason
    assert result.annual_actual == (0 if 'quarter' in override else Decimal('1000'))
    assert result.annual_calculated_premium == 0


@pytest.mark.parametrize('threshold', [None, Decimal('0')])
def test_missing_threshold_counts_in_actual(shipment, product, plans, threshold):
    result = calculate_kpi([shipment()], [replace(product, price_distributor=threshold)], plans)
    assert result.annual_actual == Decimal('1000')
    assert result.excluded_rows[0].exclusion_reason == 'Не задана зеленая зона'
    assert result.annual_calculated_premium == 0


def test_zero_quantity_with_explicit_price(shipment, product, plans):
    assert calculate_kpi([shipment(quantity=Decimal('0'))], [product], plans).audit[0].eligible


@pytest.mark.parametrize('category', ['СБКС', 'ВМК', 'ЭП', 'Latema', 'Novionta'])
@pytest.mark.parametrize('client_type', ['ЛПУ', 'дистрибьютер'])
def test_each_category_and_client_uses_price_list(shipment, product, plans, category, client_type):
    product = replace(product, category=category)
    result = calculate_kpi([shipment(client_type=client_type)], [product], plans)
    block = (1 if client_type == 'дистрибьютер' else 2) if category in ('СБКС', 'ВМК') else (3 if client_type == 'дистрибьютер' else 4)
    assert result.green_totals[1, client_type, category] == Decimal('1000')
    assert result.quarters[0].blocks[block-1].base == Decimal('1000')
    assert result.quarters[0].blocks[block-1].source_rows == (3,)
    assert sum(b.base for b in result.quarters[0].blocks) == Decimal('1000')


def test_lpu_threshold_is_distinct(shipment, product, plans):
    product = replace(product, price_lpu=Decimal('200'))
    result = calculate_kpi([shipment(client_type='ЛПУ')], [product], plans)
    assert result.audit[0].threshold_raw == Decimal('200')
    assert not result.audit[0].eligible


def test_returns_and_fractional_kopecks(shipment, product, plans):
    rows = [shipment(revenue=Decimal('1000.01')), shipment(source_row=4, revenue=Decimal('-100.01'), quantity=Decimal('-1'))]
    result = calculate_kpi(rows, [product], plans)
    assert result.quarters[0].actual == Decimal('900.00')
    assert result.quarters[0].calculated_premium == Decimal('45.0000')
    assert result.quarters[0].blocks[0].source_rows == (3, 4)


def test_global_decimal_context_does_not_change_calculation(shipment, product, plans):
    with localcontext() as ctx:
        ctx.prec = 3
        result = calculate_kpi([shipment(revenue=Decimal('123456.789'))], [product], plans)
    assert result.annual_calculated_premium == Decimal('6172.83945')


def test_duplicate_rows_rejected(shipment, product, plans):
    with pytest.raises(CalculationInputError):
        calculate_kpi([shipment(), shipment()], [product], plans)
