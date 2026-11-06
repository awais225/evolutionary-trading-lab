from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_registry_session
from app.schemas import InstrumentListOut, InstrumentOut
from hero1_storage.repositories import InstrumentRepository

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])


@router.get("", response_model=InstrumentListOut)
def list_instruments(registry: Session = Depends(get_registry_session)) -> InstrumentListOut:
    items = InstrumentRepository(registry).list()
    return InstrumentListOut(instruments=[InstrumentOut.model_validate(i, from_attributes=True) for i in items])


@router.get("/{symbol}", response_model=InstrumentOut)
def get_instrument(symbol: str, registry: Session = Depends(get_registry_session)) -> InstrumentOut:
    inst = InstrumentRepository(registry).get(symbol.upper())
    if inst is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument: {symbol.upper()}")
    return InstrumentOut.model_validate(inst, from_attributes=True)
