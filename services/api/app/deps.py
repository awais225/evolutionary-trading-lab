from __future__ import annotations

import re
from typing import Iterator

from fastapi import Depends, HTTPException, Path
from sqlalchemy.orm import Session

from hero1_storage.engine import instrument_session, registry_session, validate_symbol
from hero1_storage.repositories import InstrumentRepository
from hero1_storage.engine import InstrumentContextError

SYMBOL_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def get_registry_session() -> Iterator[Session]:
    s = registry_session()
    try:
        yield s
    finally:
        s.close()


def get_instrument_context(
    instrument: str = Path(..., description="Explicit instrument symbol, e.g. XAUUSD"),
    registry: Session = Depends(get_registry_session),
) -> str:
    """Validate the instrument path parameter. No defaults, ever."""
    if not SYMBOL_RE.match(instrument or ""):
        raise HTTPException(status_code=422, detail=f"malformed instrument identifier: {instrument!r}")
    try:
        symbol = validate_symbol(instrument)
    except InstrumentContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    inst = InstrumentRepository(registry).get(symbol)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument: {symbol}")
    if not inst.enabled:
        raise HTTPException(status_code=409, detail=f"instrument disabled: {symbol}")
    return symbol


def get_instrument_session(symbol: str = Depends(get_instrument_context)) -> Iterator[Session]:
    s = instrument_session(symbol)
    try:
        yield s
    finally:
        s.close()
