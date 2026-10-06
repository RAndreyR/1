from dataclasses import replace
from decimal import Decimal
import pytest
from app.services.calculation_engine import calculate_kpi
from app.services.product_matcher import ProductMatcher
from app.utils.normalization import CalculationInputError


def test_normalized_exact_match(shipment, product, plans):
    product = replace(product, canonical_name='Товар новый')
    row = shipment(product_raw=' ТОВАР\u00a0  новый ')
    result = calculate_kpi([row], [product], plans)
    assert result.included_rows[0].match_kind == 'exact'


def test_alias_category_from_current_price_list(shipment, product, plans):
    product = replace(product, category=' Novionta ')
    result = calculate_kpi([shipment(product_raw='СБКС старое имя')], [product], plans,
                           aliases={' сбкс  старое имя ': 'p'})
    assert result.audit[0].product.category == 'Novionta'
    assert result.audit[0].match_kind == 'alias'
    assert result.quarters[0].blocks[2].base == Decimal('1000')
    assert result.quarters[0].blocks[0].base == 0


def test_exact_precedes_alias(product):
    other = replace(product, id='other', canonical_name='Другой')
    assert ProductMatcher([product, other], {'ТОВАР': 'other'}).match('товар')[0] == product


def test_near_name_is_not_guessed(shipment, product, plans):
    result = calculate_kpi([shipment(product_raw='Товары')], [product], plans)
    assert result.quarters[0].actual == Decimal('1000')
    assert result.quarters[0].calculated_premium == 0
    assert result.excluded_rows[0].exclusion_reason == 'Не сопоставлен продукт'
    assert result.warnings


@pytest.mark.parametrize('mode', ['duplicate_name', 'duplicate_id', 'stale_alias', 'conflict_alias'])
def test_ambiguous_mapping_rejected(product, mode):
    products, aliases = [product], None
    if mode == 'duplicate_name':
        products.append(replace(product, id='other', canonical_name=' ТОВАР '))
    elif mode == 'duplicate_id':
        products.append(replace(product, canonical_name='Другой'))
    elif mode == 'stale_alias':
        aliases = {'старое': 'missing'}
    else:
        products.append(replace(product, id='other', canonical_name='Другой'))
        aliases = {'alias': 'p', ' ALIAS ': 'other'}
    with pytest.raises(CalculationInputError):
        ProductMatcher(products, aliases)
