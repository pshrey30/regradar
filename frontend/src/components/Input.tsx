import { useId, type InputHTMLAttributes } from 'react'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string
  helperText?: string
  error?: string
}

export function Input({ label, helperText, error, disabled, id, className = '', ...rest }: InputProps) {
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
          'h-10 rounded-md border px-3 text-sm text-slate-900 placeholder:text-slate-400',
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
