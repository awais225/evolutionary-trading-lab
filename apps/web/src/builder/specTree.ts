/**
 * Immutable helpers over a StrategySpec expression tree (entry conditions).
 * Mirrors the backend limits (hero1_strategy_spec.expressions):
 *   MAX_TREE_NODES = 64, MAX_TREE_DEPTH = 8, max 16 children per logic node,
 *   operand offsets 0..500. The backend re-checks all of these.
 */
import { indicatorSummary, OPERATOR_LABELS } from './indicatorMeta'

export const MAX_TREE_NODES = 64
export const MAX_TREE_DEPTH = 8
export const MAX_CHILDREN = 16
export const MAX_OFFSET = 500

export type Path = (number | 'child')[]

export function isLogic(n: unknown): n is { type: string; children?: unknown[]; child?: unknown } {
  return !!n && typeof n === 'object' && ['and', 'or', 'not'].includes((n as { type?: string }).type ?? '')
}
export function isComparison(n: unknown): n is { type: 'compare'; op: string; left: any; right: any } {
  return !!n && typeof n === 'object' && (n as { type?: string }).type === 'compare'
}

export function operandSummary(o: any): string {
  if (!o || typeof o !== 'object') return '?'
  switch (o.kind) {
    case 'candle': return `${String(o.field).toUpperCase()}${o.offset ? `[-${o.offset}]` : ''}`
    case 'forming_candle': return `forming ${String(o.field).toUpperCase()} (opt-in)`
    case 'indicator': return indicatorSummary(o)
    case 'constant': return String(o.value)
    default: return String(o.kind ?? '?')
  }
}
export function comparisonSummary(c: any): string {
  return `${operandSummary(c?.left)} ${OPERATOR_LABELS[c?.op] ?? c?.op ?? '?'} ${operandSummary(c?.right)}`
}
export function logicLabel(type: string): string {
  return type === 'and' ? 'AND' : type === 'or' ? 'OR' : 'NOT'
}

export function treeSize(node: any): number {
  if (!node || typeof node !== 'object') return 0
  if (node.type === 'and' || node.type === 'or') {
    return 1 + (node.children ?? []).reduce((acc: number, c: any) => acc + treeSize(c), 0)
  }
  if (node.type === 'not') return 1 + treeSize(node.child)
  return 1 // comparison + its two operands counted loosely; backend counts operands precisely
}
export function treeDepth(node: any, depth = 1): number {
  if (!node || typeof node !== 'object') return depth
  if (node.type === 'and' || node.type === 'or') {
    return Math.max(...(node.children ?? []).map((c: any) => treeDepth(c, depth + 1)), depth)
  }
  if (node.type === 'not') return treeDepth(node.child, depth + 1)
  return depth + 1
}

export function nodeAt(root: any, path: Path): any {
  let cur = root
  for (const step of path) {
    if (step === 'child') { cur = cur?.child; continue }
    cur = cur?.children?.[step]
  }
  return cur
}

/** Replace the node at `path` using updater(oldNode). */
export function updateAt(root: any, path: Path, updater: (node: any) => any): any {
  if (path.length === 0) return updater(root)
  const [step, ...rest] = path
  if (step === 'child') return { ...root, child: updateAt(root.child, rest, updater) }
  const children = [...(root.children ?? [])]
  children[step] = updateAt(children[step], rest, updater)
  return { ...root, children }
}

/** Remove the node at `path` from its parent (splice children). Root cannot be removed. */
export function removeAt(root: any, path: Path): any {
  if (path.length === 0) return root // never delete the root
  const parentPath = path.slice(0, -1)
  const last = path[path.length - 1]
  return updateAt(root, parentPath, (parent) => {
    if (last === 'child') {
      // unwrap NOT over its only child
      return parent?.child ?? parent
    }
    const children = (parent?.children ?? []).filter((_: unknown, i: number) => i !== last)
    return { ...parent, children }
  })
}

/** Insert `child` into the logic node at `path` (append to children). */
export function insertChildAt(root: any, path: Path, child: any): any {
  return updateAt(root, path, (parent) => {
    if (isLogic(parent) && Array.isArray(parent.children)) {
      return { ...parent, children: [...parent.children, child] }
    }
    return parent
  })
}

export function collectIndicators(node: any): { name: string; params: Record<string, unknown>; field?: string | null; offset?: number }[] {
  const out: { name: string; params: Record<string, unknown>; field?: string | null; offset?: number }[] = []
  const visit = (o: any) => {
    if (!o || typeof o !== 'object') return
    if (o.kind === 'indicator') out.push({ name: o.name, params: o.params ?? {}, field: o.field ?? null, offset: o.offset ?? 0 })
  }
  const walkNode = (n: any) => {
    if (!n || typeof n !== 'object') return
    if (n.type === 'and' || n.type === 'or') (n.children ?? []).forEach(walkNode)
    else if (n.type === 'not') walkNode(n.child)
    else if (n.type === 'compare') { visit(n.left); visit(n.right) }
  }
  walkNode(node)
  return out
}

/** Update a single operand at `operandPath` (e.g. [.., 'left'] or [.., 'right']). */
export function updateOperand(root: any, cmpPath: Path, side: 'left' | 'right', updater: (op: any) => any): any {
  return updateAt(root, cmpPath, (cmp) => ({ ...cmp, [side]: updater(cmp[side]) }))
}

export function emptyComparison() {
  return {
    type: 'compare',
    op: 'gt',
    left: { kind: 'candle', field: 'close', offset: 0 },
    right: { kind: 'constant', value: 0 },
  }
}
export function emptyGroup(type: 'and' | 'or' = 'and') {
  return { type, children: [emptyComparison()] }
}
