import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import { Home } from './pages/Home'
import { Login } from './pages/Login'

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
      <Home />
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
