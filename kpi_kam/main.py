"""Runnable Phase 1 demonstration, not an Excel importer or desktop UI."""
from decimal import Decimal
from app.models.domain import Plans, Product, Shipment
from app.services.calculation_engine import calculate_kpi
from app.utils.normalization import format_money


def main() -> None:
    product = Product('demo', 'Демонстрационный продукт', 'СБКС', Decimal('100'), Decimal('100'))
    shipments = [Shipment(q, q, 'дистрибьютер', product.canonical_name,
                          Decimal('10'), Decimal(value), Decimal('100'))
                 for q, value in enumerate(('800', '800', '1000', '1000'), 1)]
    result = calculate_kpi(shipments, [product], Plans('1000', '1000', '1000', '1000'))
    print('KPI KAM — демонстрация расчетного ядра (искусственные данные)')
    for quarter in result.quarters:
        print(f'Q{quarter.quarter}: факт {format_money(quarter.actual)}, '
              f'расчетная премия {format_money(quarter.calculated_premium)}, '
              f'к выплате {format_money(quarter.payable)}')
    print(f'Итого к выплате: {format_money(result.annual_payable)}')


if __name__ == '__main__':
    main()
