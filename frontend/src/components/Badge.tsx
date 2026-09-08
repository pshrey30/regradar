export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type DomainValue = 'financial' | 'clinical' | 'environmental' | 'other'

export type BadgeProps =
  | { variant: 'risk'; value: RiskLevel; className?: string }
  | { variant: 'domain'; value: DomainValue; className?: string }

// White background + colored border, not a filled tint: a filled tint
// (this project's own earlier -bg tokens) put text-critical (#DC2626) on a
// light tint at ~4.42:1 — axe flagged that as a Serious WCAG AA violation
// (needs 4.5:1 at this text size). White as the surface, border in the
// pinned base hex, text in the AA-safe -text variant where one exists
// (see tailwind.config.ts) — critical's own base hex already passes 4.5:1.
const RISK_CLASSES: Record<RiskLevel, string> = {
  low: 'bg-white border border-risk-low text-risk-low-text',
  medium: 'bg-white border border-risk-medium text-risk-medium-text',
  high: 'bg-white border border-risk-high text-risk-high-text',
  critical: 'bg-white border border-risk-critical text-risk-critical',
}

// "other" has no documented domain color — falls back to a neutral slate
// tint rather than inventing an unspecified hue.
const DOMAIN_CLASSES: Record<DomainValue, string> = {
  financial: 'bg-white border border-domain-financial text-domain-financial',
  clinical: 'bg-white border border-domain-clinical text-domain-clinical',
  environmental: 'bg-white border border-domain-environmental text-domain-environmental-text',
  other: 'bg-slate-100 text-slate-600',
}

export function Badge(props: BadgeProps) {
  const classes = props.variant === 'risk' ? RISK_CLASSES[props.value] : DOMAIN_CLASSES[props.value]
  return (
    <span
      className={[
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize',
        classes,
        props.className ?? '',
      ].join(' ')}
    >
      {props.value}
    </span>
  )
}
