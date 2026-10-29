"""Frozen V10.3 insertion method, commit 8abf3042; regression reference only."""
import time,json,sqlite3
from typing import Dict,Any,Optional
def insert_strategy(self, s: Dict[str, Any]) -> Optional[int]:
    now = time.time()
    rid = s.get("run_id") or ""
    data_source = s.get("data_source")
    if not data_source:
        # V5: default to the research population. LEGACY_TEST is a
        # classification of *imported historical* records and is applied by
        # the one-time V3.6 migration (and by explicit callers), never
        # inferred here from the run id's spelling.
        data_source = "USER_RESEARCH"

    research_node_num = s.get("research_node_num")
    if research_node_num is None:
        if rid:
            cnt = self.one("SELECT COUNT(*) c FROM strategies WHERE run_id=?", (rid,))["c"]
        else:
            cnt = self.one("SELECT COUNT(*) c FROM strategies WHERE data_source=?", (data_source,))["c"]
        research_node_num = cnt + 1

    try:
        return self.x(
            """INSERT INTO strategies
               (hash,parent_id,generation,symbol,timeframe,direction,status,
                creation_reason,mutation_type,species_key,genome,complexity,
                fitness,created_at,updated_at,origin,hypothesis_id,run_id,
                data_source,research_node_num)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s["hash"], s.get("parent_id"), s.get("generation", 0), s["symbol"],
             s["timeframe"], s.get("direction", "both"), s.get("status", "BORN"),
             s.get("creation_reason"), s.get("mutation_type"), s.get("species_key"),
             json.dumps(s["genome"]), s.get("complexity", 0), s.get("fitness"),
             now, now, s.get("origin", "seed"), s.get("hypothesis_id"),
             s.get("run_id"), data_source, research_node_num),
        )
    except sqlite3.IntegrityError:
        # duplicate genome hash -> caller handles dedupe
        return None
