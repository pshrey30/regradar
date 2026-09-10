import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import { ApiKeys } from './pages/ApiKeys'
import { FilingDetail } from './pages/FilingDetail'
import { FilingsList } from './pages/FilingsList'
import { Login } from './pages/Login'
import { Search } from './pages/Search'
import { Webhooks } from './pages/Webhooks'

// Landing pulls in three.js for its 3D hero (~600KB) — code-split so that
// weight is only ever fetched by a logged-out visitor hitting "/", never
// bundled into the authenticated app's main chunk.
const Landing = lazy(() => import('./pages/Landing').then((m) => ({ default: m.Landing })))

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}

export default App
