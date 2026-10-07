"""Explicit package identity; no fuzzy product or category inference."""
from decimal import Decimal
import re
from app.utils.normalization import normalize_text

MARKER=' ⟦фасовка: '
EMPTY={'','-','—','нет','не указана','не указан','не указано','не задан','нет данных','n/a','na','none','null'}
SIZE=re.compile(r'(?<![\w.,])([0-9]+(?:[.,][0-9]+)?)\s*(мл|ml|литр(?:а|ов)?|л|l|кг|kg|гр|г|g)(?!\w)',re.I)
UNITS={'мл':('мл',1),'ml':('мл',1),'л':('мл',1000),'l':('мл',1000),
       'литр':('мл',1000),'литра':('мл',1000),'литров':('мл',1000),
       'г':('г',1),'гр':('г',1),'g':('г',1),'кг':('г',1000),'kg':('г',1000)}


def normalize_packaging(value,unit=''):
    text=normalize_text(str(value or ''))
    if text in EMPTY:return ''
    if unit and re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?',text):text+=f' {unit}'
    match=SIZE.fullmatch(text.rstrip('.'))
    if match:
        base,multiplier=UNITS[match[2].casefold()]
        amount=Decimal(match[1].replace(',','.'))*multiplier
        return f'{format(amount.normalize(),"f")} {base}'
    if re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?',text):
        return format(Decimal(text.replace(',','.')).normalize(),'f')
    return text


def name_packaging(name):
    found={normalize_packaging(match.group()) for match in SIZE.finditer(normalize_text(name))}
    return next(iter(found)) if len(found)==1 else ''


def sales_packaging(raw_name,value,unit=''):
    size=normalize_packaging(value)
    explicit=name_packaging(raw_name)
    if size and ' ' not in size and explicit and size==explicit.split(' ')[0]:
        # Mixed liquid/powder sheets: an explicit "350 г" retains its mass unit.
        return explicit
    return normalize_packaging(value,unit)


def product_mapping_key(raw_name,packaging=''):
    name,existing=split_mapping_key(raw_name or '')
    size=normalize_packaging(packaging) or existing
    return f'{name}{MARKER}{size}⟧' if size else name


def split_mapping_key(value):
    if MARKER in value and value.endswith('⟧'):
        name,size=value.rsplit(MARKER,1)
        return name,normalize_packaging(size[:-1])
    return value,''


def packaging_compatible(raw_name,canonical_name):
    name,pack=split_mapping_key(raw_name or '')
    explicit=name_packaging(name)
    target=name_packaging(canonical_name)
    def compatible(left,right):
        if not left or not right:return True
        if left==right:return True
        # A number without units can only be checked against that same amount.
        if ' ' not in left:return left==right.split(' ')[0]
        if ' ' not in right:return right==left.split(' ')[0]
        return False
    return compatible(pack,explicit) and compatible(pack or explicit,target)
