"""Pure exact/explicit-alias matching, independent of alias persistence."""
from collections.abc import Iterable, Mapping
from app.models.domain import Product
from app.utils.normalization import CalculationInputError, normalize_text
from app.utils.packaging import product_mapping_key,split_mapping_key,packaging_compatible,name_packaging


class ProductMatcher:
    def __init__(self, products: Iterable[Product], aliases: Mapping[str, str] | None = None) -> None:
        self.by_name: dict[str, list[Product]] = {}
        self.by_id: dict[str, Product] = {}
        for product in products:
            name = normalize_text(product.canonical_name)
            variants=self.by_name.setdefault(name,[])
            pack=product.packaging or name_packaging(product.canonical_name)
            if product.id in self.by_id or any(
                not pack or not (p.packaging or name_packaging(p.canonical_name)) or
                pack==(p.packaging or name_packaging(p.canonical_name)) for p in variants):
                raise CalculationInputError('Ambiguous product name or id in current price list')
            variants.append(product)
            self.by_id[product.id] = product
        self.aliases: dict[str, Product] = {}
        for alias, product_id in (aliases or {}).items():
            name = normalize_text(alias)
            if not name or product_id not in self.by_id:
                raise CalculationInputError('Alias must reference a current price-list product')
            if name in self.aliases and self.aliases[name].id != product_id:
                raise CalculationInputError('Conflicting normalized aliases')
            self.aliases[name] = self.by_id[product_id]

    def match(self, raw_name: str | None, packaging: str = '') -> tuple[Product | None, str | None]:
        key=product_mapping_key(raw_name,packaging)
        raw,_=split_mapping_key(key)
        name = normalize_text(raw)
        exact=[p for p in self.by_name.get(name,()) if packaging_compatible(key,p.canonical_name,p.packaging)]
        if len(exact)==1:
            return exact[0], 'exact'
        alias=normalize_text(key)
        if alias in self.aliases and packaging_compatible(key,self.aliases[alias].canonical_name,self.aliases[alias].packaging):
            return self.aliases[alias], 'alias'
        return None, None
