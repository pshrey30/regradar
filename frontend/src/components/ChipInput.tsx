import { useId, useState, type KeyboardEvent } from 'react'

export interface ChipInputProps {
  label: string
  value: string[]
  onChange: (value: string[]) => void
  placeholder?: string
  required?: boolean
}

function commit(current: string, existing: string[]): string[] | null {
  const trimmed = current.trim()
  if (trimmed.length === 0) return null
  if (existing.includes(trimmed)) return null
  return [...existing, trimmed]
}

export function ChipInput({ label, value, onChange, placeholder, required }: ChipInputProps) {
  const [draft, setDraft] = useState('')
  const id = useId()

  function tryCommit() {
    const next = commit(draft, value)
    if (next) {
      onChange(next)
      setDraft('')
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      tryCommit()
    } else if (event.key === 'Backspace' && draft.length === 0 && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  function removeAt(index: number) {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-slate-900">
        {label}
      </label>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-slate-300 px-2 py-1.5 focus-within:border-primary-600 focus-within:ring-2 focus-within:ring-primary-600">
        {value.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="inline-flex items-center gap-1 rounded-full border border-primary-100 bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700"
          >
            {item}
            <button
              type="button"
              aria-label={`Remove ${item}`}
              onClick={() => removeAt(index)}
              className="text-primary-700 hover:text-primary-900"
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          type="text"
          required={required && value.length === 0}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={tryCommit}
          placeholder={value.length === 0 ? placeholder : undefined}
          className="min-w-32 flex-1 border-none text-sm text-slate-900 outline-none placeholder:text-slate-400"
        />
      </div>
    </div>
  )
}
