"""Pure exact/explicit-alias matching. Persistence belongs to Phase 2."""
from collections.abc import Iterable, Mapping
from app.models.domain import Product
from app.utils.normalization import CalculationInputError, normalize_text


class ProductMatcher:
    def __init__(self, products: Iterable[Product], aliases: Mapping[str, str] | None = None) -> None:
        self.by_name: dict[str, Product] = {}
        self.by_id: dict[str, Product] = {}
        for product in products:
            name = normalize_text(product.canonical_name)
            if name in self.by_name or product.id in self.by_id:
                raise CalculationInputError('Ambiguous product name or id in current price list')
            self.by_name[name] = product
            self.by_id[product.id] = product
        self.aliases: dict[str, Product] = {}
        for alias, product_id in (aliases or {}).items():
            name = normalize_text(alias)
            if not name or product_id not in self.by_id:
                raise CalculationInputError('Alias must reference a current price-list product')
            if name in self.aliases and self.aliases[name].id != product_id:
                raise CalculationInputError('Conflicting normalized aliases')
            self.aliases[name] = self.by_id[product_id]

    def match(self, raw_name: str | None) -> tuple[Product | None, str | None]:
        name = normalize_text(raw_name)
        if name in self.by_name:
            return self.by_name[name], 'exact'
        if name in self.aliases:
            return self.aliases[name], 'alias'
        return None, None
