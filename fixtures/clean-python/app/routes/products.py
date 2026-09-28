import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.fx import rate
from app.models import Product
from app.schemas import PriceQuote, ProductCreate, ProductOut

router = APIRouter(prefix="/products", tags=["products"])
logger = logging.getLogger("catalog.products")


def _to_out(product: Product) -> ProductOut:
    return ProductOut(
        sku=product.sku,
        name=product.name,
        category=product.category,
        condition=product.condition,
        price_cents=product.price_cents,
        currency=product.currency,
    )


@router.get("")
def list_products(
    category: str | None = None, session: Session = Depends(get_session)
) -> list[ProductOut]:
    query = session.query(Product)
    if category:
        query = query.filter(Product.category == category)
    return [_to_out(p) for p in query.order_by(Product.sku).limit(100)]


@router.post("", status_code=201)
def create_product(
    payload: ProductCreate, request: Request, session: Session = Depends(get_session)
) -> ProductOut:
    product = Product(**payload.model_dump())
    session.add(product)
    session.commit()
    logger.info(
        "product created sku=%s name=%s condition=%s request_id=%s",
        product.sku,
        product.name,
        product.condition,
        request.headers.get("x-request-id", "-"),
    )
    return _to_out(product)


@router.get("/{sku}")
def get_product(sku: str, session: Session = Depends(get_session)) -> ProductOut:
    product = session.query(Product).filter(Product.sku == sku).first()
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return _to_out(product)


@router.get("/{sku}/quote")
def quote(sku: str, currency: str, session: Session = Depends(get_session)) -> PriceQuote:
    product = session.query(Product).filter(Product.sku == sku).first()
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    amount = product.price_cents / 100 * rate(product.currency, currency.upper())
    return PriceQuote(sku=sku, currency=currency.upper(), amount=round(amount, 2))
