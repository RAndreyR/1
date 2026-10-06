"""Normalization without fuzzy matching or financial float conversion."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class CalculationInputError(ValueError):
    """Input cannot be used safely in a calculation."""


def normalize_text(value: str | None) -> str:
    return ' '.join((value or '').split()).casefold()


def normalize_quarter(value: str | int | None) -> int | None:
    if type(value) is int:
        return value if 1 <= value <= 4 else None
    match = re.fullmatch(r'([1-4])\s*кв\.?', normalize_text(value))
    return int(match[1]) if match else None


def normalize_client_type(value: str | None) -> str | None:
    value = normalize_text(value)
    if value in ('дистрибьютер', 'дистрибьютор'):
        return 'дистрибьютер'
    return 'ЛПУ' if value == 'лпу' else None


def decimal_value(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise CalculationInputError('Use Decimal, numeric text or int; float is forbidden')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CalculationInputError('Invalid decimal value') from exc
    if not result.is_finite():
        raise CalculationInputError('Decimal must be finite')
    return result


def price_1dp(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    rounded = decimal_value(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f'{rounded:,.2f}'.replace(',', ' ').replace('.', ',') + ' ₽'
