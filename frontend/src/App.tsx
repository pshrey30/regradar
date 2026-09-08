// FE-01 scope is the design system (tokens + component library + Storybook),
// not a real screen — those are FE-03+. This placeholder just confirms the
// Tailwind tokens and a couple of components render correctly.
import { Badge } from './components/Badge'
import { Button } from './components/Button'
import { Card } from './components/Card'

function App() {
  return (
    <div className="p-8">
      <Card>
        <h1 className="mb-4 text-xl font-semibold text-slate-900">RegRadar Design System</h1>
        <div className="mb-4 flex gap-2">
          <Badge variant="risk" value="critical" />
          <Badge variant="domain" value="financial" />
        </div>
        <Button variant="primary">Primary action</Button>
      </Card>
    </div>
  )
}

export default App
