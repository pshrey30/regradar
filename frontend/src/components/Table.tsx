import type { ReactNode } from 'react'

export interface TableColumn<T> {
  header: string
  accessor: (row: T) => ReactNode
}

export interface TableProps<T> {
  columns: TableColumn<T>[]
  data: T[]
  getRowKey: (row: T) => string
  emptyMessage?: string
  // FE-03: rows in a clickable list navigate to a detail screen; leaving
  // this optional keeps Table itself usable for a non-navigable list too.
  onRowClick?: (row: T) => void
  // FE-03: skeleton rows while a query is in flight — a fixed placeholder
  // count rather than reusing `data.length` (which is stale/empty on the
  // very first load, the exact moment loading needs to render something).
  loading?: boolean
  loadingRowCount?: number
}

function SkeletonRow({ columnCount }: { columnCount: number }) {
  return (
    <tr className="h-14">
      {Array.from({ length: columnCount }).map((_, index) => (
        // Static skeleton cells within one render — index is a stable,
        // correct key here, not a data identity.
        <td key={index} className="px-4">
          <div className="h-4 w-3/4 animate-pulse rounded bg-slate-200" />
        </td>
      ))}
    </tr>
  )
}

export function Table<T>({
  columns,
  data,
  getRowKey,
  emptyMessage = 'No data',
  onRowClick,
  loading = false,
  loadingRowCount = 5,
}: TableProps<T>) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="sticky top-0 bg-slate-50">
          <tr>
            {columns.map((column) => (
              <th
                key={column.header}
                scope="col"
                className="h-14 px-4 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {loading ? (
            Array.from({ length: loadingRowCount }).map((_, index) => (
              <SkeletonRow key={index} columnCount={columns.length} />
            ))
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="h-14 px-4 text-center text-sm text-slate-500">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            data.map((row) => (
              <tr
                key={getRowKey(row)}
                className={[
                  'h-14 hover:bg-slate-50',
                  onRowClick ? 'cursor-pointer' : '',
                ].join(' ')}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((column) => (
                  <td key={column.header} className="px-4 text-sm text-slate-900">
                    {column.accessor(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
