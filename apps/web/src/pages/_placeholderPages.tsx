import { Placeholder } from '../components/Placeholder'
import { BatchScope } from '../components/BatchScope'

export function WWWResearch() {
  return (
    <Placeholder phase="Phase 7" title="WWW Research"
      blurb="Autonomous web research with a budget ledger: search strategy sites, forums and papers; extract concrete, reproducible rules with source URLs and evidence quotes; checkpoint every source so runs resume without repeating work. Nothing is invented — a strategy missing any required field stays INCOMPLETE and can never reach live trading."
      chips={['start / pause / resume / stop', 'budget & source limits', 'evidence quotes + URLs', 'checkpoint resume', 'bulk convert to nodes']}
      icon={<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18" /></>} />
  )
}

export function InteractiveWWW() {
  return (
    <Placeholder phase="Phase 7" title="Interactive WWW"
      blurb="A conversational research agent: investigate a strategy through dialogue, ask clarifying questions, compare variants, and produce an editable, validated strategy specification that becomes an INTERACTIVE node."
      chips={['chat with tools', 'clarification loop', 'variant comparison', 'editable spec output', 'INTERACTIVE origin lineage']}
      icon={<path d="M8 10h.01M12 10h.01M16 10h.01M21 11a8 8 0 01-11.6 7.1L4 20l1.9-5.4A8 8 0 1121 11z" />} />
  )
}

export function DeepTesting() {
  return (
    <Placeholder phase="Phase 4" title="DeepTesting"
      blurb="The shared testing engine for every origin: initial backtesting, DeepTest, out-of-sample, walk-forward, robustness and Monte-Carlo stress; comparison matrices, trade-level inspection and exports. One verified execution semantic across all modes — never a copy."
      chips={['OOS / walk-forward / robustness', 'trade-level inspector', 'cross-node comparison', 'CSV / JSON export', 'CUSTOM · WWW · INTERACTIVE · FINE_TUNED']}
      icon={<><path d="M4 5h16M4 10h16M4 15h10M4 20h6" /></>}>
      <BatchScope />
    </Placeholder>
  )
}

export function FineTuning() {
  return (
    <Placeholder phase="Phase 8" title="Fine Tuning"
      blurb="Conversational AI over full DeepTest trade histories: losing/winning trade buckets, drawdown periods, session and volatility breakdowns — then evidence-based change proposals as CHILD nodes. The original strategy is never overwritten; improvements must beat baseline out-of-sample before any live consideration."
      chips={['full trade-history access', 'losing-trade analysis', 'session / volatility buckets', 'child-node proposals', 'baseline + OOS gate', 'overfit warnings']}
      icon={<path d="M4 17l6-6-6-6M12 17h8" />}>
      <BatchScope />
    </Placeholder>
  )
}

export function LiveTesting() {
  return (
    <Placeholder phase="Phase 5" title="Live Testing"
      blurb="Demo-forward execution against MT5 with hard gates: only versions with an explicit APPROVAL record and sufficient testing can be activated. AI never silently changes a live strategy and cannot place unauthorized orders. Kill switch always visible; auto-INACTIVE on restart."
      chips={['approved versions only', 'demo-first guardrails', 'kill switch', 'restart-safe recovery', 'separate Approval ledger', 'Windows + MT5 required']}
      icon={<path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12L13 2z" />}>
      <BatchScope />
    </Placeholder>
  )
}

export function LogView() {
  return (
    <Placeholder phase="Phase 6" title="LOG"
      blurb="Searchable, filterable operational log: timestamps, levels, instrument, task and node filters; copy and .txt export; server-side secret redaction so credentials and tokens can never appear, even in exports."
      chips={['level / instrument / task / node filters', 'free-text search', 'copy & .txt export', 'server-side secret redaction']}
      icon={<path d="M7 8h13M7 12h13M7 16h7M3 8h.01M3 12h.01M3 16h.01" />} />
  )
}
