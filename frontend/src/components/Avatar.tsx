// No image-upload/storage infra exists in this project (no S3 field for a
// user photo, no upload endpoint) — a real "profile picture" would be a
// separate backend feature. This gives every user a stable, recognizable
// visual identity today: a colored circle with their initials, the same
// pattern Slack/Linear/etc. use as the default before a real photo is set.
export const AVATAR_PALETTE = [
  'bg-primary-600',
  'bg-domain-financial',
  'bg-domain-clinical',
  'bg-domain-environmental',
  'bg-domain-engineering',
  'bg-risk-high',
] as const

export type AvatarColor = (typeof AVATAR_PALETTE)[number]

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

// A simple deterministic hash so the same name always gets the same color
// by default, across renders and across every place this component is used.
function defaultColorFor(name: string): AvatarColor {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length]
}

function storageKey(name: string): string {
  return `regradar:avatar-color:${name}`
}

// Per-browser only (no backend field for this) — read/write wrapped in
// try/catch since localStorage can throw (private browsing, blocked site
// data) and must never break rendering the avatar itself.
export function getSavedAvatarColor(name: string): AvatarColor | null {
  try {
    const value = localStorage.getItem(storageKey(name))
    return (AVATAR_PALETTE as readonly string[]).includes(value ?? '')
      ? (value as AvatarColor)
      : null
  } catch {
    return null
  }
}

export function setSavedAvatarColor(name: string, color: AvatarColor): void {
  try {
    localStorage.setItem(storageKey(name), color)
  } catch {
    // Best-effort — a viewer with storage blocked just keeps the default color.
  }
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
  colorOverride,
}: {
  name: string
  size?: keyof typeof SIZE_CLASSES
  className?: string
  // Lets a caller (the Profile page's color picker) reflect a just-picked
  // color immediately, without waiting for a re-read of localStorage.
  colorOverride?: AvatarColor
}) {
  const color = colorOverride ?? getSavedAvatarColor(name) ?? defaultColorFor(name)
  return (
    <span
      role="img"
      aria-label={name}
      title={name}
      className={[
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white',
        SIZE_CLASSES[size],
        color,
        className,
      ].join(' ')}
    >
      {initialsOf(name)}
    </span>
  )
}
