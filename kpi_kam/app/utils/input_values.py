"""Localized decimal input; Qt spin boxes never handle money or percentages."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
import re
from app.utils.normalization import CalculationInputError, decimal_value


def nonnegative_number(text: str) -> Decimal:
    text = text.strip()
    if any(c.isspace() for c in text):
        text = re.sub(r'\s', ' ', text)
        if not re.fullmatch(r'\d{1,3}(?: \d{3})+(?:[.,]\d+)?', text):
            raise CalculationInputError('Введите неотрицательное число')
        text = text.replace(' ', '')
    if not re.fullmatch(r'\d+(?:[.,]\d+)?', text):
        raise CalculationInputError('Введите неотрицательное число')
    return decimal_value(text.replace(',', '.'))


def percentage(text: str) -> Decimal:
    value = nonnegative_number(text)
    if value > Decimal('100'):
        raise CalculationInputError('Процент должен быть от 0 до 100')
    with localcontext() as context:
        context.prec = 50
        return value / Decimal('100')


def percent_text(value: Decimal | None) -> str:
    if value is None:
        return '—'
    with localcontext() as context:
        context.prec = 50
        rounded=(value*Decimal('100')).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)
        return format(rounded,'f').replace('.', ',') + '%'


def percent_input(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        text = format(value * Decimal('100'), 'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
