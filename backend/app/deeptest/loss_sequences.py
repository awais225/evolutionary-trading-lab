"""Observed realized net loss sequences, separate from drawdown."""
from decimal import Decimal,ROUND_HALF_UP
import math
from collections import Counter
def calculate(trades,expected_count=None):
 from .analytics import eligible_trades
 raw=[t for t in trades if not t.get('status') or str(t['status']).upper() in ('CLOSED','COMPLETED')];rows=eligible_trades(raw)
 def finite(v):
  try:return v is not None and math.isfinite(float(v))
  except (ValueError,TypeError):return False
 reason=None
 if len(raw)!=len(rows) and any(not finite(t.get('net_pnl')) for t in raw):reason='Missing/non-finite closed trade P/L; sequence incomplete'
 if any(not finite(t.get('exit_ts')) for t in rows):reason='Missing closed-trade exit timestamp; chronology unavailable'
 if expected_count is not None and len(rows)!=expected_count:reason='Trade population incomplete'
 if not rows:reason='No eligible closed trades'
 convention='Exit timestamp ascending, then trade_no/canonical row ID; net P/L < 0 only; breakeven interrupts. Currency amount is positive loss magnitude, rounded HALF_UP to cents after summing. Observed retained trade population, not equity/balance drawdown.'
 def missing(reason):return dict(max_consecutive_losing_trades=None,max_consecutive_loss_currency=None,loss_sequence_reason=reason,loss_sequence_convention=convention)
 if reason:return missing(reason)
 ties=Counter(float(t['exit_ts']) for t in rows)
 if any(ties[float(t['exit_ts'])]>1 and t.get('trade_no') is None and t.get('id') is None for t in rows):return missing('Ambiguous same-timestamp trade order')
 def order(t):
  seq=t.get('trade_no') if t.get('trade_no') is not None else t.get('id')
  if seq is not None and (not finite(seq) or float(seq)!=int(float(seq))):raise ValueError('invalid closed-trade sequence identifier')
  return float(t['exit_ts']),int(float(seq)) if seq is not None else 0
 try:
  keys=[order(t) for t in rows]
  if len(set(keys))!=len(keys):raise ValueError('ambiguous same-timestamp sequence identifiers')
  rows=sorted(rows,key=order)
 except (ValueError,TypeError,OverflowError) as exc:return missing(str(exc))
 length=maximum=0;amount=largest=Decimal('0')
 for t in rows:
  pnl=Decimal(str(t['net_pnl']))
  if pnl<0:length+=1;amount-=pnl
  else:length=0;amount=Decimal('0')
  maximum=max(maximum,length);largest=max(largest,amount)
 return dict(max_consecutive_losing_trades=maximum,max_consecutive_loss_currency=float(largest.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),loss_sequence_reason=None,loss_sequence_convention=convention)
