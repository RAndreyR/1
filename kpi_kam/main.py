"""Desktop entry point with retained Phase 2 CLI commands."""
import argparse
from contextlib import ExitStack
from decimal import Decimal
import logging
from pathlib import Path
import sys

from app.models.domain import CalculationResult, Plans, Product, Shipment
from app.repositories.alias_repository import AliasRepository, AliasStorageError
from app.services.calculation_engine import calculate_kpi
from app.services.excel_importer import ExcelImportError, import_excel
from app.services.mapping_service import ImportSession, mapping_key
from app.utils.normalization import CalculationInputError, format_money, normalize_text


def _print_result(result: CalculationResult) -> None:
    for quarter in result.quarters:
        print(f'Q{quarter.quarter}: факт {format_money(quarter.actual)}, '
              f'расчетная премия {format_money(quarter.calculated_premium)}, '
              f'к выплате {format_money(quarter.payable)}')
    print(f'Итого к выплате: {format_money(result.annual_payable)}')


def _demo() -> None:
    product = Product('demo', 'Демонстрационный продукт', 'СБКС', Decimal('100'), Decimal('100'))
    shipments = [Shipment(q, q, 'дистрибьютер', product.canonical_name,
                          Decimal('10'), Decimal(value), Decimal('100'))
                 for q, value in enumerate(('800', '800', '1000', '1000'), 1)]
    print('KPI KAM — демонстрация расчетного ядра (искусственные данные)')
    _print_result(calculate_kpi(shipments, [product], Plans('1000', '1000', '1000', '1000')))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='KPI KAM — импорт и расчет без GUI')
    commands = parser.add_subparsers(dest='command')
    gui = commands.add_parser('gui', help='Открыть desktop-приложение')
    gui.add_argument('--db',type=Path,help='Путь SQLite (по умолчанию папка данных приложения)')
    commands.add_parser('demo',help='Демонстрация расчетного ядра')
    for name in ('check', 'calculate'):
        command = commands.add_parser(name)
        command.add_argument('file', type=Path)
        command.add_argument('--db', type=Path, help='SQLite-хранилище алиасов (без параметра — временные решения)')
        if name == 'calculate':
            command.add_argument('--plans', nargs=4, required=True, metavar=('Q1','Q2','Q3','Q4'))
            command.add_argument('--map', nargs=2, action='append', default=[], metavar=('ALIAS','PRODUCT'))
            command.add_argument('--map-row', nargs=2, action='append', default=[], metavar=('ROW','PRODUCT'))
            command.add_argument('--remember-mappings', action='store_true')
            command.add_argument('--leave-unmatched', action='append', default=[], metavar='ALIAS')
            command.add_argument('--leave-row', type=int, action='append', default=[], metavar='ROW')
            command.add_argument('--leave-all-unmatched', action='store_true')
    return parser


def _apply_decisions(session: ImportSession, args: argparse.Namespace) -> None:
    products = {normalize_text(p.canonical_name): p.id for p in session.imported.products}
    selections = [(mapping_key(alias, 0), name) for alias, name in args.map]
    for raw_row, name in args.map_row:
        try:
            number = int(raw_row)
        except ValueError as exc:
            raise CalculationInputError('Номер строки должен быть целым числом') from exc
        if not any(s.source_row == number for s in session.imported.shipments):
            raise CalculationInputError('Номер строки отсутствует в импорте')
        shipment = next(s for s in session.imported.shipments if s.source_row == number)
        selections.append((mapping_key(shipment.product_raw, number), name))
    for key, name in selections:
        if normalize_text(name) not in products:
            raise CalculationInputError('Выбранный товар отсутствует в текущем прайсе')
        session.select_product(key, products[normalize_text(name)], remember=args.remember_mappings)
    for alias in args.leave_unmatched:
        session.leave_unmatched(mapping_key(alias, 0))
    for number in args.leave_row:
        shipment = next((s for s in session.imported.shipments if s.source_row == number), None)
        if shipment is None:
            raise CalculationInputError('Номер строки отсутствует в импорте')
        session.leave_unmatched(mapping_key(shipment.product_raw, number))
    if args.leave_all_unmatched:
        for entry in session.mappings:
            if entry.status == 'unresolved':
                session.leave_unmatched(entry.key)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None or args.command == 'gui':
        from app.ui.launcher import run_gui
        return run_gui(getattr(args,'db',None))
    if args.command == 'demo':
        _demo()
        return 0
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        # Validate mandatory plans before saving any explicitly remembered mappings.
        plans = Plans(*args.plans) if args.command == 'calculate' else None
        imported = import_excel(args.file)
        with ExitStack() as stack:
            repository = stack.enter_context(AliasRepository(args.db)) if args.db else None
            session = ImportSession(imported, repository)
            if args.command == 'calculate':
                _apply_decisions(session, args)
            report = session.validation
            print('Листы: ' + ', '.join(imported.detected_sheets))
            print(f'Строк отгрузок: {imported.shipment_row_count}; прочитано: {report.shipment_count}; '
                  f'товаров прайса: {report.product_count}; сопоставлено строк: {report.matched_row_count}')
            for issue in report.issues:
                location = f'{issue.sheet or ""}, строка {issue.source_row}' if issue.source_row else issue.sheet or 'файл'
                print(f'{issue.severity}: {location}: {issue.message}')
            for entry in session.mappings:
                if entry.status == 'unresolved':
                    print(f'Требуется решение: {entry.key}; строки {entry.source_rows}; названия {entry.raw_names}')
            print('Готов к расчету: ' + ('да' if report.can_calculate else 'нет'))
            if args.command == 'calculate':
                _print_result(session.calculate(plans))
            return 2 if report.errors else 0
    except (CalculationInputError, ExcelImportError, AliasStorageError) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
