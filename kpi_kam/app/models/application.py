"""Settings and history metadata for the desktop workflow."""
from dataclasses import dataclass
from decimal import Decimal
from app.models.domain import CalculationResult, Rules
from app.models.import_data import ProductMapping


@dataclass(frozen=True)
class AppSettings:
    rules: Rules = Rules()
    export_folder: str = ''
    price_precision: int = 1

    def __post_init__(self) -> None:
        if self.price_precision != 1:
            raise ValueError('Точность сравнения цены фиксирована: 1 знак')


@dataclass(frozen=True)
class SavedCalculation:
    id: int
    employee: str
    year: int
    source_file: str
    source_hash: str
    calculated_at: str
    result: CalculationResult
    mappings: tuple[ProductMapping, ...]


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    employee: str
    year: int
    source_file: str
    calculated_at: str
    quarter_payables: tuple[Decimal, ...]
    annual_payable: Decimal
