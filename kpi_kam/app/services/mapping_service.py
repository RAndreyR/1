"""Explicit product decisions and the validated import-to-calculation boundary."""
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
import logging

from app.models.domain import CalculationResult, Plans, Rules
from app.models.import_data import ExcelImport, ProductMapping, ValidationReport, ValidationIssue
from app.repositories.alias_repository import AliasRepository
from app.services.calculation_engine import calculate_kpi
from app.services.product_matcher import ProductMatcher
from app.services.validation_service import validate_import
from app.utils.normalization import CalculationInputError, normalize_text

logger = logging.getLogger(__name__)


class ImportNotReadyError(CalculationInputError):
    """Structural errors or undecided mappings prevent a final calculation."""


def mapping_key(raw_name: str | None, source_row: int) -> str:
    name = normalize_text(raw_name)
    return 'name:' + name if name else f'row:{source_row}'


class ImportSession:
    def __init__(self, imported: ExcelImport, repository: AliasRepository | None = None) -> None:
        self.imported = imported
        self.repository = repository
        self._products = {p.id: p for p in imported.products}
        saved = repository.aliases() if repository else {}
        self._aliases = {name: pid for name, pid in saved.items() if pid in self._products}
        self._stale_aliases = {name: pid for name, pid in saved.items() if pid not in self._products}
        groups = {}
        for shipment in imported.shipments:
            key = mapping_key(shipment.product_raw, shipment.source_row)
            groups.setdefault(key, []).append(shipment)
        self._mappings: dict[str, ProductMapping] = {}
        matcher = ProductMatcher(imported.products, self._aliases)
        for key, rows in groups.items():
            raw = rows[0].product_raw
            product, kind = matcher.match(raw)
            status = 'exact' if kind == 'exact' else 'saved_alias' if kind == 'alias' else 'unresolved'
            self._mappings[key] = ProductMapping(key, normalize_text(raw),
                tuple(dict.fromkeys(row.product_raw for row in rows)),
                tuple(row.source_row for row in rows), product, status)
        logger.info('Product matching: matched_rows=%d unmatched_rows=%d',
                    sum(len(entry.source_rows) for entry in self.mappings if entry.product is not None),
                    sum(len(entry.source_rows) for entry in self.mappings if entry.product is None))
        if repository and not imported.errors:
            repository.sync_products(imported.products, imported.source_hash)

    @property
    def mappings(self) -> tuple[ProductMapping, ...]:
        return tuple(self._mappings.values())

    def _entry(self, key: str) -> ProductMapping:
        if key not in self._mappings:
            raise CalculationInputError('Не найдено сопоставление в текущем импорте')
        return self._mappings[key]

    def select_product(self, key: str, product_id: str, *, remember: bool = False) -> None:
        entry = self._entry(key)
        if self.imported.errors:
            raise ImportNotReadyError('Сначала исправьте ошибки структуры файла')
        if product_id not in self._products:
            raise CalculationInputError('Выберите продукт из текущего прайса')
        if entry.status == 'exact' and entry.product.id != product_id:
            raise CalculationInputError('Точное совпадение имеет приоритет')
        if remember:
            if not entry.alias_normalized:
                raise CalculationInputError('Пустое название нельзя запомнить как алиас')
            if self.repository is None:
                raise CalculationInputError('Не подключено хранилище сопоставлений')
            self.repository.remember(entry.alias_normalized, product_id)
        self._mappings[key] = replace(entry, product=self._products[product_id],
                                      status='exact' if entry.status == 'exact' else 'manual')

    def leave_unmatched(self, key: str) -> None:
        entry = self._entry(key)
        if entry.status == 'exact':
            raise CalculationInputError('Точное совпадение нельзя исключить из сопоставления')
        self._mappings[key] = replace(entry, product=None, status='left_unmatched')

    def _decisions(self) -> tuple[dict[str, str], dict[int, str], set[int]]:
        aliases, assignments, left = {}, {}, set()
        for entry in self.mappings:
            if entry.status == 'saved_alias':
                aliases[entry.alias_normalized] = entry.product.id
            elif entry.status == 'manual':
                assignments.update({row: entry.product.id for row in entry.source_rows})
            elif entry.status == 'left_unmatched':
                left.update(entry.source_rows)
        return aliases, assignments, left

    @property
    def validation(self) -> ValidationReport:
        aliases, assignments, left = self._decisions()
        report = validate_import(self.imported, aliases=aliases,
                                 product_assignments=assignments, left_unmatched_rows=left)
        stale_issues = tuple(ValidationIssue('stale_alias',
            'Сохраненный алиас ссылается на продукт, отсутствующий в текущем прайсе',
            'warning', source_row=row)
            for entry in self.mappings if entry.status == 'unresolved' and entry.alias_normalized in self._stale_aliases
            for row in entry.source_rows)
        return replace(report, issues=report.issues + stale_issues)

    def calculate(self, plans: Plans, rules: Rules = Rules(),
                  paid_history: Mapping[int, Decimal] | None = None) -> CalculationResult:
        report = self.validation
        if not report.can_calculate:
            logger.info('Calculation blocked: structural_errors=%d pending_mappings=%d',
                        len(report.errors), len(report.pending_mapping_rows))
            raise ImportNotReadyError('Исправьте ошибки файла и разрешите все сопоставления')
        aliases, assignments, _ = self._decisions()
        result = calculate_kpi(self.imported.shipments, self.imported.products, plans, rules,
                               paid_history, aliases=aliases, product_assignments=assignments)
        logger.info('KPI calculated: shipments=%d included=%d annual_actual=%s annual_premium=%s rules=%s',
                    len(result.audit), len(result.included_rows), result.annual_actual,
                    result.annual_calculated_premium, rules)
        return result
