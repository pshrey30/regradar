// No image-upload/storage infra exists in this project (no S3 field for a
// user photo, no upload endpoint) — a real "profile picture" would be a
// separate backend feature. This gives every user a stable, recognizable
// visual identity today: a colored circle with their initials, the same
// pattern Slack/Linear/etc. use as the default before a real photo is set.
const PALETTE = [
  'bg-primary-600',
  'bg-domain-financial',
  'bg-domain-clinical',
  'bg-domain-environmental',
  'bg-domain-engineering',
  'bg-risk-high',
] as const

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

// A simple deterministic hash so the same name always gets the same color,
// across renders and across every place this component is used.
function colorFor(name: string): (typeof PALETTE)[number] {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return PALETTE[hash % PALETTE.length]
}

const SIZE_CLASSES = {
  sm: 'h-7 w-7 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-16 w-16 text-xl',
} as const

export function Avatar({
  name,
  size = 'md',
  className = '',
}: {
  name: string
  size?: keyof typeof SIZE_CLASSES
  className?: string
}) {
  return (
    <span
      role="img"
      aria-label={name}
      title={name}
      className={[
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white',
        SIZE_CLASSES[size],
        colorFor(name),
        className,
      ].join(' ')}
    >
      {initialsOf(name)}
    </span>
  )
}
