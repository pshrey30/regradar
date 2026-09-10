import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { AuthProvider } from './auth/AuthContext'
import { canSeeNavItem, useAuth, type NavItem } from './auth/useAuth'
import { ApiKeys } from './pages/ApiKeys'
import { FilingDetail } from './pages/FilingDetail'
import { FilingsList } from './pages/FilingsList'
import { Login } from './pages/Login'
import { Metrics } from './pages/Metrics'
import { Search } from './pages/Search'
import { SourceConfig } from './pages/SourceConfig'
import { Webhooks } from './pages/Webhooks'

// Landing pulls in three.js for its 3D hero (~600KB) — code-split so that
// weight is only ever fetched by a logged-out visitor hitting "/", never
// bundled into the authenticated app's main chunk.
const Landing = lazy(() => import('./pages/Landing').then((m) => ({ default: m.Landing })))

// `navItem` is optional — routes with no role restriction (Filings, Filing
// Detail) omit it. When present, a role that can't see the matching nav
// item is redirected to /filings before the page (and its data queries)
// ever mount — belt-and-suspenders on top of the backend's own 403, since
// nav-hiding alone still let a typed-in URL reach the full page shell.
function ProtectedRoute({ children, navItem }: { children: ReactNode; navItem?: NavItem }) {
  const { status, role } = useAuth()

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  if (navItem && !canSeeNavItem(role, navItem)) {
    return <Navigate to="/filings" replace />
  }
  return <AppShell>{children}</AppShell>
}

// "/" is the public marketing page — an already-authenticated visitor
// lands straight on the app instead of the pitch meant for a logged-out
// visitor.
function HomeRoute() {
  const { status } = useAuth()
  // Wait out the loading state rather than flashing the marketing page
  // for an already-signed-in visitor before redirecting them away from it.
  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center bg-white text-slate-500">Loading…</div>
  }
  if (status === 'authenticated') {
    return <Navigate to="/filings" replace />
  }
  return (
    <Suspense fallback={<div className="min-h-screen bg-white" />}>
      <Landing />
    </Suspense>
  )
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/filings"
          element={
            <ProtectedRoute>
              <FilingsList />
            </ProtectedRoute>
          }
        />
        <Route
          path="/filings/:filingId"
          element={
            <ProtectedRoute>
              <FilingDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/search"
          element={
            <ProtectedRoute navItem="search">
              <Search />
            </ProtectedRoute>
          }
        />
        <Route
          path="/webhooks"
          element={
            <ProtectedRoute navItem="webhooks">
              <Webhooks />
            </ProtectedRoute>
          }
        />
        <Route
          path="/api-keys"
          element={
            <ProtectedRoute navItem="api_keys">
              <ApiKeys />
            </ProtectedRoute>
          }
        />
        <Route
          path="/metrics"
          element={
            <ProtectedRoute navItem="metrics">
              <Metrics />
            </ProtectedRoute>
          }
        />
        <Route
          path="/source-config"
          element={
            <ProtectedRoute navItem="source_config">
              <SourceConfig />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}

export default App
