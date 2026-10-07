from decimal import Decimal
import pytest
from app.utils.input_values import percent_text


@pytest.mark.parametrize('value,expected',[(None,'—'),('0.9','90,0%'),('0.933333333333333333','93,3%'),('0.12345','12,3%'),('0.9995','100,0%'),('-0.1005','-10,1%')])
def test_percent_display_has_one_decimal_and_uses_half_up(value,expected):
    assert percent_text(Decimal(value) if value is not None else None)==expected
