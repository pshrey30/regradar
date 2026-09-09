import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import { FilingDetail } from './pages/FilingDetail'
import { FilingsList } from './pages/FilingsList'
import { Login } from './pages/Login'
import { Search } from './pages/Search'

function ProtectedShell() {
  const { status } = useAuth()

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  return (
    <AppShell>
      <Routes>
        {/* FE-03: the Filings List is the default landing screen. */}
        <Route path="/" element={<FilingsList />} />
        <Route path="/filings/:filingId" element={<FilingDetail />} />
        {/* FE-05: API-06's own 403 already blocks the Executive role server-side; */}
        {/* the nav item's own role-gate (useAuth.ts) hides the entry point too. */}
        <Route path="/search" element={<Search />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<ProtectedShell />} />
      </Routes>
    </AuthProvider>
  )
}

export default App
