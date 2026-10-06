from decimal import Decimal, localcontext
import pytest
from app.models.domain import Plans, Product, Rules, Shipment
from app.utils.normalization import (CalculationInputError, decimal_value, format_money,
    normalize_text, normalize_quarter, normalize_client_type)


@pytest.mark.parametrize('q', range(1, 5))
@pytest.mark.parametrize('suffix', [' кв.', ' кв', 'кв.', 'кв', '\u00a0КВ. '])
def test_quarters(q, suffix):
    assert normalize_quarter(f'{q}{suffix}') == q


@pytest.mark.parametrize('value', [None, '', '5 кв.', '2026', '1 квартал'])
def test_invalid_quarter(value):
    assert normalize_quarter(value) is None


@pytest.mark.parametrize('value', ['дистрибьютер', ' ДИСТРИБЬЮТОР\u00a0', 'дистрибьютор'])
def test_distributors(value):
    assert normalize_client_type(value) == 'дистрибьютер'


def test_text_and_lpu():
    assert normalize_text('  ТОВАР\u00a0  новый ') == 'товар новый'
    assert normalize_client_type(' лПу ') == 'ЛПУ'
    assert normalize_client_type('магазин') is None


@pytest.mark.parametrize('value', [1.1, True, 'NaN', 'Infinity', 'abc', None])
def test_invalid_decimal(value):
    with pytest.raises(CalculationInputError):
        decimal_value(value)


@pytest.mark.parametrize('values', [(None, '1', '1', '1'), ('-1', '1', '1', '1')])
def test_all_plans_required_and_nonnegative(values):
    with pytest.raises(CalculationInputError):
        Plans(*values)


def test_money_display():
    assert format_money(Decimal('1234567.895')) == '1 234 567,90 ₽'
    assert format_money(Decimal('-1.005')) == '-1,01 ₽'


@pytest.mark.parametrize('kwargs', [{'plan_gate': '-0.1'}, {'rate_block_1': '1.01'}, {'rate_block_2': None}])
def test_invalid_rules(kwargs):
    with pytest.raises(CalculationInputError):
        Rules(**kwargs)


def test_input_float_rejected(shipment):
    with pytest.raises(CalculationInputError):
        shipment(revenue=1.1)
