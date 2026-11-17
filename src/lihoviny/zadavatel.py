"""Zadavatel: portfolio zadavatele a sledovaní konkurenti z konfigurace `zadavatel.toml`.

Soubor upravuje tým (portfolio je fiktivní, ke srovnání), proto se při načtení kontroluje:
chybná kategorie nebo počet produktů mimo 5–8 zastaví agenta hned při startu.
"""

import tomllib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from lihoviny.products import CATEGORIES, Category

CONFIG = Path(__file__).with_name("zadavatel.toml")
PORTFOLIO_SIZE = range(5, 9)


@dataclass(frozen=True)
class PortfolioProduct:
    code: str
    name: str
    category: Category
    volume_ml: int
    abv: Decimal
    regular_price: Decimal

    @property
    def price_per_l(self) -> Decimal:
        return (self.regular_price / (Decimal(self.volume_ml) / 1000)).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )

    @property
    def price_per_l_ethanol(self) -> Decimal:
        ethanol_l = Decimal(self.volume_ml) / 1000 * self.abv / 100
        return (self.regular_price / ethanol_l).quantize(Decimal("0.01"), ROUND_HALF_UP)


def load(path: Path = CONFIG) -> tuple[list[PortfolioProduct], list[str]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    portfolio = [
        PortfolioProduct(
            code=item["code"],
            name=item["name"],
            category=item["category"],
            volume_ml=item["volume_ml"],
            abv=Decimal(item["abv"]),
            regular_price=Decimal(item["regular_price"]),
        )
        for item in data["portfolio"]
    ]
    if len(portfolio) not in PORTFOLIO_SIZE:
        raise ValueError(f"{path.name}: portfolio má mít 5–8 produktů, má {len(portfolio)}")
    codes = [product.code for product in portfolio]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{path.name}: kódy produktů v portfoliu se opakují")
    for product in portfolio:
        if product.category not in CATEGORIES:
            raise ValueError(f"{path.name}: {product.code} má neznámou kategorii")
    return portfolio, list(data["watched_competitors"])


PORTFOLIO, WATCHED_COMPETITORS = load()
