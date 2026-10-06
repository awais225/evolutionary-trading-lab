"""V4.4 — Research Statistics & Node Economics (analytics layer).

This package is *read-only analytics*. It never generates, qualifies, kills,
retires or evolves a node, and it never touches the research engine's decision
gates. Every number it reports is either read back from the existing
authoritative state (engine snapshot, scoped counters) or aggregated with SQL
over exactly the same population the V4.0 display scope defines.

Population scope (spec §3/§5/§6):

* ``USER_RESEARCH`` is the research population — every aggregate, list and
  per-node calculation in this package is restricted to it;
* ``LEGACY_TEST`` infrastructure records are excluded (and reported as an
  excluded count for transparency), reusing the V4.0 predicate
  ``COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'`` rather than a new
  classification system;
* execution/audit records (PAPER / MT5 DEMO / LIVE TEST / manual executions)
  are reported in their own clearly-labelled section and are never summed into
  a research metric.
"""

from .research_stats import (  # noqa: F401
    LEGACY_POPULATION,
    SCOPE_PREDICATE,
    USER_POPULATION,
    counter_audit,
    evolution,
    execution_records,
    node_economics,
    node_list,
    node_stats,
    overview,
    population,
    research_performance,
    scope_info,
)

__all__ = [
    "LEGACY_POPULATION",
    "SCOPE_PREDICATE",
    "USER_POPULATION",
    "counter_audit",
    "evolution",
    "execution_records",
    "node_economics",
    "node_list",
    "node_stats",
    "overview",
    "population",
    "research_performance",
    "scope_info",
]
