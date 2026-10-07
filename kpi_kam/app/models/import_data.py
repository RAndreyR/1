"""Excel import, validation and mapping records; independent of GUI widgets."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from app.models.domain import Product, Shipment


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Literal['error', 'warning']
    sheet: str | None = None
    source_row: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class SheetLayout:
    name: str
    header_row: int
    columns: Mapping[str, int]  # Logical field -> 1-based column.
    packaging_unit: str = ''  # Explicit unit for numeric cells with no unit/header.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'columns', MappingProxyType(dict(self.columns)))


@dataclass(frozen=True)
class ExcelImport:
    source_file: str
    source_hash: str
    detected_sheets: tuple[str, ...]
    layouts: tuple[SheetLayout, ...]
    shipments: tuple[Shipment, ...]
    products: tuple[Product, ...]
    issues: tuple[ValidationIssue, ...]
    shipment_row_count: int
    price_row_count: int

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == 'error')


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    shipment_count: int
    product_count: int
    matched_row_count: int
    unmatched_rows: tuple[int, ...]
    unmatched_products: tuple[str | None, ...]
    invalid_quarters: tuple[int, ...]
    unknown_client_types: tuple[int, ...]
    missing_price_rows: tuple[int, ...]
    missing_threshold_rows: tuple[int, ...]
    pending_mapping_rows: tuple[int, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == 'error')

    @property
    def can_calculate(self) -> bool:
        return not self.errors and not self.pending_mapping_rows


@dataclass(frozen=True)
class ProductMapping:
    key: str
    alias_normalized: str
    raw_names: tuple[str | None, ...]
    source_rows: tuple[int, ...]
    product: Product | None
    status: Literal['exact', 'saved_alias', 'manual', 'unresolved', 'left_unmatched']
