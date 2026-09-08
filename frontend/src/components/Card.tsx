import type { HTMLAttributes, ReactNode } from 'react'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
}

export function Card({ children, className = '', ...rest }: CardProps) {
  return (
    <div
      className={['rounded-lg border border-slate-200 bg-white p-6 shadow-sm', className].join(' ')}
      {...rest}
    >
      {children}
    </div>
  )
}
