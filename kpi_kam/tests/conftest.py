from decimal import Decimal
import pytest
from app.models.domain import Plans, Product, Shipment


@pytest.fixture
def product():
    return Product('p', 'Товар', 'СБКС', Decimal('100'), Decimal('100'))


@pytest.fixture
def plans():
    return Plans(*(Decimal('0') for _ in range(4)))


@pytest.fixture
def shipment():
    def make(**overrides):
        data = dict(source_row=3, quarter='1 кв.', client_type='дистрибьютер',
                    product_raw='Товар', quantity=Decimal('10'),
                    revenue=Decimal('1000'), unit_price=Decimal('100'))
        data.update(overrides)
        return Shipment(**data)
    return make
