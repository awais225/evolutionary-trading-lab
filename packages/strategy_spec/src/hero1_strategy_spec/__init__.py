"""Hero1 strategy specification DSL — Phase 2.

Phase 1 boundary: not implemented. The design contract (see docs/PHASE_ROADMAP.md):
  * Strategies are DATA (versioned, hashable JSON/YAML specs), never Python.
  * The compiler validates -> canonicalizes -> lowers to a sandboxed IR.
  * AI-generated strategies MUST pass the same validator as human-written ones.
  * H1/H4/D1 timeframes and origin taxonomy (CUSTOM/WWW/INTERACTIVE/FINE_TUNED) are
    first-class in the spec.
"""
__phase__ = 2
