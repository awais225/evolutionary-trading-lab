import { useEffect } from 'react'
import { HistoricalDataPanel } from '../components/HistoricalDataPanel'
import { Mt5InstrumentSpecifications } from '../components/Mt5InstrumentSpecifications'
import { NodeDashboard } from '../components/NodeDashboard'
import { useInstrument } from '../state/instrument'
import { logUserAction } from '../lib/opLog'

/**
 * DATA tab: historical market data (top) over the generated-nodes dashboard
 * (below). Both sections read the running application's own databases —
 * the canonical market-data store and the per-instrument node registry.
 */
export function DataPage() {
  const { instrument } = useInstrument()

  useEffect(() => {
    logUserAction('user opened the DATA tab', { instrument, subsystem: 'web' })
  }, [instrument])

  return (
    <div className="data-page">
      <section className="card data-section" aria-labelledby="historical-data-label">
        <div className="card-title" id="historical-data-label">
          Historical market data
          <span className="faint" style={{ fontWeight: 400, fontSize: 10.5, marginLeft: 8 }}>
            canonical stored candles for {instrument} — OHLC never substitutes for MT5 symbol metadata
          </span>
        </div>
        <HistoricalDataPanel />
      </section>

      <Mt5InstrumentSpecifications />
      <section className="card data-section" aria-labelledby="generated-nodes-label">
        <div className="card-title" id="generated-nodes-label">
          Generated nodes
          <span className="faint" style={{ fontWeight: 400, fontSize: 10.5, marginLeft: 8 }}>
            persisted registry for {instrument} — timeframe, direction and lineage from the saved records
          </span>
        </div>
        <NodeDashboard />
      </section>
    </div>
  )
}
