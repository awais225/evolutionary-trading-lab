import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { classifyDeepTestRun, DeepTestingErrorPanel } from '../components/DeepTestingErrorPanel'

describe('DeepTesting error classification and details', () => {
  it('keeps blocked validation distinct from unexpected execution failure', () => {
    expect(classifyDeepTestRun('FAILED', 'HISTORICAL_SPREAD_UNAVAILABLE: missing spread')).toBe('BLOCKED')
    expect(classifyDeepTestRun('FAILED', 'UNEXPECTED ValueError: bad value')).toBe('FAILED')
    expect(classifyDeepTestRun('DONE')).toBe('COMPLETED')
    expect(classifyDeepTestRun('DONE', null)).toBe('COMPLETED')
  })
  it('shows node/run/stage/code and expandable technical traceback', () => {
    render(<DeepTestingErrorPanel nodeId={2} runId={1}
      error={'UNEXPECTED ValueError at engine.py:15 (run_backtest): bad input\nStage: ENGINE_EXECUTION; Exception: ValueError\nTraceback: details'} />)
    expect(screen.getByText(/Node #2 · Run #1/)).toBeTruthy()
    expect(screen.getByText(/ENGINE_EXECUTION · Code: UNEXPECTED/)).toBeTruthy()
    expect(screen.getByText(/Traceback: details/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Copy error details/ })).toBeTruthy()
  })
})
