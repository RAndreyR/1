"""Desktop orchestration without Qt or financial logic in widgets."""
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from app.models.application import AppSettings, SavedCalculation
from app.models.domain import Plans
from app.models.import_data import ExcelImport
from app.repositories.application_repository import ApplicationRepository
from app.services.mapping_service import ImportSession, ImportNotReadyError
from app.utils.normalization import CalculationInputError


class ApplicationService:
    def __init__(self, repository: ApplicationRepository) -> None:
        self.repository = repository
        self.settings = repository.settings()
        self.session: ImportSession | None = None

    def set_import(self, imported: ExcelImport) -> ImportSession:
        self.session = ImportSession(imported, self.repository)
        return self.session

    def save_settings(self, settings: AppSettings) -> None:
        self.repository.save_settings(settings)
        self.settings = settings

    def calculate(self, employee: str, year: int, plans: Plans, gate: Decimal,
                  paid_history: Mapping[int, Decimal] | None = None) -> SavedCalculation:
        if not employee.strip() or type(year) is not int or not 1900 <= year <= 2100:
            raise CalculationInputError('Укажите сотрудника и корректный год')
        if self.session is None:
            raise ImportNotReadyError('Сначала проверьте Excel-файл')
        result = self.session.calculate(plans, replace(self.settings.rules, plan_gate=gate), paid_history)
        return self.repository.save_calculation(self.session.imported, employee, year, result, self.session.mappings)
