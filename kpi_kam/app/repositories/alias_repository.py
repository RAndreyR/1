"""SQLite product identity and explicit alias persistence (not calculation history)."""
from collections.abc import Iterable
from datetime import datetime, timezone
import os
from pathlib import Path
from types import TracebackType
import sqlite3

from app.models.domain import Product
from app.services.product_matcher import ProductMatcher
from app.utils.normalization import CalculationInputError, normalize_text


class AliasStorageError(RuntimeError):
    """SQLite could not persist/read an alias operation."""


def default_database_path() -> Path:
    if os.environ.get('APPDATA'):
        base = Path(os.environ['APPDATA'])
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share')))
    return base / 'KPI_KAM' / 'kpi_kam.db'


class AliasRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price_lpu TEXT,
                    price_distributor TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    import_id TEXT
                );
                CREATE TABLE IF NOT EXISTS product_aliases (
                    id INTEGER PRIMARY KEY,
                    alias_normalized TEXT NOT NULL UNIQUE,
                    product_id TEXT NOT NULL REFERENCES products(id),
                    created_at TEXT NOT NULL
                );
            ''')
        except (OSError, sqlite3.Error) as exc:
            if hasattr(self, 'connection'):
                self.connection.close()
            raise AliasStorageError('Не удалось открыть хранилище сопоставлений') from exc

    def __enter__(self) -> 'AliasRepository':
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def sync_products(self, products: Iterable[Product], import_id: str) -> None:
        products = tuple(products)
        ProductMatcher(products)
        try:
            with self.connection:
                self.connection.execute('UPDATE products SET active = 0')
                self.connection.executemany('''
                    INSERT INTO products (id, canonical_name, category, price_lpu,
                                          price_distributor, active, import_id)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        canonical_name = excluded.canonical_name,
                        category = excluded.category,
                        price_lpu = excluded.price_lpu,
                        price_distributor = excluded.price_distributor,
                        active = 1, import_id = excluded.import_id
                ''', [(p.id, p.canonical_name, p.category,
                       str(p.price_lpu) if p.price_lpu is not None else None,
                       str(p.price_distributor) if p.price_distributor is not None else None,
                       import_id) for p in products])
        except sqlite3.Error as exc:
            raise AliasStorageError('Не удалось сохранить текущий прайс') from exc

    def aliases(self) -> dict[str, str]:
        """Includes stale bindings; sessions filter against their current price list."""
        try:
            return dict(self.connection.execute('SELECT alias_normalized, product_id FROM product_aliases'))
        except sqlite3.Error as exc:
            raise AliasStorageError('Не удалось прочитать сопоставления') from exc

    def remember(self, raw_alias: str, product_id: str) -> None:
        alias = normalize_text(raw_alias)
        if not alias:
            raise CalculationInputError('Пустое название нельзя запомнить как алиас')
        try:
            with self.connection:
                if self.connection.execute('SELECT 1 FROM products WHERE id = ? AND active = 1', (product_id,)).fetchone() is None:
                    raise CalculationInputError('Выберите продукт из текущего прайса')
                self.connection.execute('''
                    INSERT INTO product_aliases (alias_normalized, product_id, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(alias_normalized) DO UPDATE SET product_id = excluded.product_id
                ''', (alias, product_id, datetime.now(timezone.utc).isoformat()))
        except sqlite3.Error as exc:
            raise AliasStorageError('Не удалось сохранить сопоставление') from exc
