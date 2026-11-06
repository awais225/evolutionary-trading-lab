interface StarButtonProps {
  starred: boolean
  busy?: boolean
  onToggle: () => void
  label?: string
}

/** Favorite toggle — themed icon button (SVG star, theme accent). Not a data operation:
 *  flips research metadata only; never touches spec/hash/version/lineage. */
export function StarButton({ starred, busy, onToggle, label }: StarButtonProps) {
  return (
    <button
      type="button"
      className={`star-btn${starred ? ' on' : ''}`}
      aria-pressed={starred}
      aria-label={label ?? (starred ? 'Unstar node' : 'Star node')}
      title={starred ? 'Starred — click to unstar' : 'Star this node (research metadata only)'}
      disabled={busy}
      onClick={(e) => { e.stopPropagation(); onToggle() }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"
           fill={starred ? 'currentColor' : 'none'} stroke="currentColor"
           strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2.6l2.94 6.34 6.9.6-5.24 4.6 1.58 6.74L12 17.5l-6.18 3.38 1.58-6.74-5.24-4.6 6.9-.6L12 2.6z" />
      </svg>
    </button>
  )
}
