import { useMemo, useState } from 'react'

/** Read-only, pretty-printed JSON code viewer (Section 10): line numbers,
 * syntax colours, internal scrolling, copy button. Presentation only -
 * the underlying values are never modified. */

function highlight(line: string, key: number) {
  const parts: React.ReactNode[] = []
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false)\b|\bnull\b/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) parts.push(<span key={i++} className="jv-punct">{line.slice(last, m.index)}</span>)
    if (m[1] !== undefined) {
      parts.push(<span key={i++} className={m[2] ? 'jv-key' : 'jv-str'}>{m[1]}</span>)
      if (m[2]) parts.push(<span key={i++} className="jv-punct">{m[2]}</span>)
    } else if (m[3] !== undefined) {
      parts.push(<span key={i++} className="jv-num">{m[3]}</span>)
    } else if (m[4] !== undefined) {
      parts.push(<span key={i++} className="jv-bool">{m[4]}</span>)
    } else {
      parts.push(<span key={i++} className="jv-null">null</span>)
    }
    last = m.index + m[0].length
  }
  if (last < line.length) parts.push(<span key={i++} className="jv-punct">{line.slice(last)}</span>)
  return <span key={key}>{parts}</span>
}

export function JsonViewer({ raw, title }: { raw: string; title?: string }) {
  const [copied, setCopied] = useState(false)
  const pretty = useMemo(() => {
    try {
      return JSON.stringify(JSON.parse(raw), null, 2)
    } catch {
      return raw
    }
  }, [raw])
  const lines = useMemo(() => pretty.split('\n'), [pretty])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(pretty)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard unavailable in this context */ }
  }

  return (
    <div>
      <div className="dt-toolbar">
        {title && <strong style={{ fontSize: 12 }}>{title}</strong>}
        <button type="button" className="btn btn-ghost btn-xs" onClick={copy}>
          {copied ? 'Copied ✓' : 'Copy JSON'}
        </button>
      </div>
      <div className="json-viewer" role="region" aria-label={title ?? 'JSON viewer'} tabIndex={0}>
        <table>
          <tbody>
            {lines.map((line, i) => (
              <tr key={i}>
                <td className="jv-line">{i + 1}</td>
                <td className="jv-code">{highlight(line, i)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
