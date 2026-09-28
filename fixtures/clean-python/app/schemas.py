from typing import Literal

from pydantic import BaseModel


class ProductCreate(BaseModel):
    sku: str
    name: str
    description: str = ""
    category: str
    condition: Literal["new", "used", "refurbished"] = "new"
    country_of_origin: str
    price_cents: int
    currency: str = "USD"


class ProductOut(BaseModel):
    sku: str
    name: str
    category: str
    condition: str
    price_cents: int
    currency: str


class PriceQuote(BaseModel):
    sku: str
    currency: str
    amount: float
