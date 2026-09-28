import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { Card } from '../components/Card'
import { Table, type TableColumn } from '../components/Table'
import { apiFetch } from '../lib/api'
import type { ApiKeyItem } from './ApiKeys'
import type { SourceConfigItem } from './SourceConfig'
import type { FunnelResponse } from './Metrics'

function FunnelPanel() {
  const query = useQuery({
    queryKey: ['metrics', 'funnel'],
    queryFn: () => apiFetch<FunnelResponse>('/v1/metrics/funnel'),
  })

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-900">Filing pipeline</h2>
      {query.isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {query.isError && <p className="text-sm text-risk-critical">Couldn't load filing status counts.</p>}
      {query.isSuccess && query.data.data.length === 0 && (
        <p className="text-sm text-slate-500">No filings yet.</p>
      )}
      {query.isSuccess && query.data.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {query.data.data.map((row) => (
            <li key={row.status} className="flex items-center justify-between text-sm">
              <span className="capitalize text-slate-600">{row.status.replace(/_/g, ' ')}</span>
              <span className="font-medium text-slate-900">{row.count}</span>
            </li>
          ))}
          <li className="flex items-center justify-between border-t border-slate-200 pt-2 text-sm font-semibold">
            <span>Total</span>
            <span>{query.data.total}</span>
          </li>
        </ul>
      )}
    </Card>
  )
}

function TeamPanel() {
  const query = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => apiFetch<ApiKeyItem[]>('/v1/api-keys'),
  })

  const columns: TableColumn<ApiKeyItem>[] = [
    { header: 'Name', accessor: (row) => row.owner_label },
    { header: 'Role', accessor: (row) => <span className="capitalize">{row.role.replace(/_/g, ' ')}</span> },
    { header: 'Status', accessor: (row) => (row.is_active ? 'Active' : 'Revoked') },
  ]

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Team</h2>
        <Link to="/users" className="text-xs font-medium text-primary-600 hover:underline">
          Manage users →
        </Link>
      </div>
      {query.isError && <p className="text-sm text-risk-critical">Couldn't load the team roster.</p>}
      {!query.isError && (
        <Table
          data={query.data ?? []}
          columns={columns}
          getRowKey={(row) => row.id}
          loading={query.isPending}
          emptyMessage="No team members yet."
        />
      )}
    </Card>
  )
}

function SourcesPanel() {
  const query = useQuery({
    queryKey: ['config', 'sources'],
    queryFn: () => apiFetch<SourceConfigItem[]>('/v1/config/sources'),
  })

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Source feeds</h2>
        <Link to="/source-config" className="text-xs font-medium text-primary-600 hover:underline">
          Configure sources →
        </Link>
      </div>
      {query.isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {query.isError && <p className="text-sm text-risk-critical">Couldn't load source configuration.</p>}
      {query.isSuccess && query.data.length === 0 && (
        <p className="text-sm text-slate-500">No sources configured yet.</p>
      )}
      {query.isSuccess && query.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {query.data.map((source) => (
            <li key={source.source} className="flex items-center justify-between text-sm">
              <span>{source.source}</span>
              <span className={source.is_active ? 'text-risk-low-text' : 'text-slate-400'}>
                {source.is_active ? 'Active' : 'Inactive'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function AdminOverview() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Organization overview</h1>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <FunnelPanel />
        <TeamPanel />
        <SourcesPanel />
      </div>
    </div>
  )
}
