import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AdminShell } from './components/AdminShell'
import { UserShell } from './components/UserShell'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import { Activity } from './pages/Activity'
import { AdminOverview } from './pages/AdminOverview'
import { ApiKeys } from './pages/ApiKeys'
import { FilingDetail } from './pages/FilingDetail'
import { FilingsList } from './pages/FilingsList'
import { Login } from './pages/Login'
import { Metrics } from './pages/Metrics'
import { Onboarding } from './pages/Onboarding'
import { Profile } from './pages/Profile'
import { Search } from './pages/Search'
import { SourceConfig } from './pages/SourceConfig'
import { Users } from './pages/Users'
import { Webhooks } from './pages/Webhooks'

// Landing pulls in three.js for its 3D hero (~600KB) — code-split so that
// weight is only ever fetched by a logged-out visitor hitting "/", never
// bundled into the authenticated app's main chunk.
const Landing = lazy(() => import('./pages/Landing').then((m) => ({ default: m.Landing })))

// Admin-only routes render inside AdminShell; every other authenticated
// role renders inside UserShell. A route not listed in adminOnly is
// reachable by any authenticated role that also isn't gated elsewhere
// (Onboarding has its own internal role branch, unaffected by this).
const ADMIN_ONLY_PATHS = new Set(['/overview', '/users', '/source-config', '/api-keys', '/metrics'])

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status, role, organizationSetupComplete } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  if (!organizationSetupComplete && location.pathname !== '/onboarding') {
    return <Navigate to="/onboarding" replace />
  }
  if (ADMIN_ONLY_PATHS.has(location.pathname) && role !== 'admin') {
    return <Navigate to="/filings" replace />
  }

  const Shell = role === 'admin' ? AdminShell : UserShell
  return <Shell>{children}</Shell>
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
    return <RoleHomeRedirect />
  }
  return (
    <Suspense fallback={<div className="min-h-screen bg-white" />}>
      <Landing />
    </Suspense>
  )
}

function RoleHomeRedirect() {
  const { role } = useAuth()
  return <Navigate to={role === 'admin' ? '/overview' : '/filings'} replace />
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/onboarding"
          element={
            <ProtectedRoute>
              <Onboarding />
            </ProtectedRoute>
          }
        />
        <Route
          path="/overview"
          element={
            <ProtectedRoute>
              <AdminOverview />
            </ProtectedRoute>
          }
        />
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
          path="/activity"
          element={
            <ProtectedRoute>
              <Activity />
            </ProtectedRoute>
          }
        />
        <Route
          path="/search"
          element={
            <ProtectedRoute>
              <Search />
            </ProtectedRoute>
          }
        />
        <Route
          path="/webhooks"
          element={
            <ProtectedRoute>
              <Webhooks />
            </ProtectedRoute>
          }
        />
        <Route
          path="/api-keys"
          element={
            <ProtectedRoute>
              <ApiKeys />
            </ProtectedRoute>
          }
        />
        <Route
          path="/metrics"
          element={
            <ProtectedRoute>
              <Metrics />
            </ProtectedRoute>
          }
        />
        <Route
          path="/source-config"
          element={
            <ProtectedRoute>
              <SourceConfig />
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <Profile />
            </ProtectedRoute>
          }
        />
        <Route
          path="/users"
          element={
            <ProtectedRoute>
              <Users />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}

export default App
