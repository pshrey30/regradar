import { useId, type InputHTMLAttributes } from 'react'

export type InputSize = 'md' | 'lg'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string
  helperText?: string
  error?: string
  // FE-05: Ask RegRadar's search field is the design system's large
  // (56px-height, 12px-radius) input variant — every other Input usage
  // keeps the default 40px/8px 'md' sizing.
  size?: InputSize
}

const SIZE_CLASSES: Record<InputSize, string> = {
  md: 'h-10 rounded-md text-sm',
  lg: 'h-14 rounded-lg text-base',
}

export function Input({
  label,
  helperText,
  error,
  disabled,
  id,
  size = 'md',
  className = '',
  ...rest
}: InputProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hasError = Boolean(error)

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium text-slate-900">
          {label}
        </label>
      )}
      <input
        id={inputId}
        disabled={disabled}
        aria-invalid={hasError}
        aria-describedby={helperText || error ? `${inputId}-helper` : undefined}
        className={[
          'border px-3 text-slate-900 placeholder:text-slate-400',
          SIZE_CLASSES[size],
          'focus:outline-none focus:ring-2 focus:ring-offset-1',
          hasError
            ? 'border-risk-critical focus:ring-risk-critical'
            : 'border-slate-300 focus:border-primary-600 focus:ring-primary-600',
          disabled ? 'cursor-not-allowed bg-slate-100 text-slate-400' : 'bg-white',
          className,
        ].join(' ')}
        {...rest}
      />
      {(helperText || error) && (
        <p id={`${inputId}-helper`} className={hasError ? 'text-sm text-risk-critical' : 'text-sm text-slate-500'}>
          {error ?? helperText}
        </p>
      )}
    </div>
  )
}
