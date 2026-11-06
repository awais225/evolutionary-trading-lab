/**
 * Sequential C-name allocation — first available number, gap reuse.
 * Mirrors the server-side allocator (_alloc_sequential_names in the nodes router):
 * the registry is the source of truth; the UI preview is advisory — the server
 * allocates authoritatively and the unique (instrument_id, name) index prevents
 * concurrent duplicates.
 */
export const C_NAME_RE = /^c(\d+)$/i

export function nextCNames(existingNames: string[], count: number): string[] {
  const used = new Set<number>()
  for (const raw of existingNames) {
    const m = C_NAME_RE.exec(String(raw).trim())
    if (m) used.add(parseInt(m[1], 10))
  }
  const out: string[] = []
  let k = 1
  while (out.length < count) {
    if (!used.has(k)) { out.push(`C${k}`); used.add(k) }
    k += 1
  }
  return out
}

export function nextAutoName(existingNames: string[]): string {
  return nextCNames(existingNames, 1)[0]
}
