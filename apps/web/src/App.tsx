import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { InstrumentProvider } from './state/instrument'
import { FeedProvider, useFeed } from './state/feed'
import { StarScopeProvider } from './state/starScope'
import { TimeframeScopeProvider } from './state/timeframeScope'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { GlobalLiveFeed } from './components/GlobalLiveFeed'
import { TaskProgressBars } from './components/TaskProgress'
import { TasksProvider, acknowledgeCategory } from './state/tasks'
import { ManualNodes } from './pages/ManualNodes'
import { Settings } from './pages/Settings'
import { WWWResearch, InteractiveWWW, FineTuning, LiveTesting } from './pages/_placeholderPages'
import { DeepTesting } from './pages/DeepTesting'
import { MonteCarlo } from './pages/MonteCarlo'
import { DataPage } from './pages/Data'
import { LogView } from './pages/Log'

const TAB_TASK_CATEGORIES: Record<string, string[]> = {
  '/deeptesting': ['DEEPTESTING'],
  '/data': ['DATA_FETCHING'],
  '/manual-nodes': ['NODE_GENERATION', 'VALIDATION'],
}

function Frame() {
  const { open } = useFeed()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  // Opening a tab acknowledges ONLY that tab's completed tasks (Section 1.3);
  // active tasks are never dismissed by viewing them.
  useEffect(() => {
    for (const cat of TAB_TASK_CATEGORIES[location.pathname] ?? []) acknowledgeCategory(cat)
  }, [location.pathname])

  return (
    <div className={`app-frame${open ? ' feed-open' : ''}`}>
      <Sidebar open={sidebarOpen} />
      <div className="main-column">
        <TopBar onMenu={() => setSidebarOpen((v) => !v)} />
        <TaskProgressBars />
        <main className="content" key={location.pathname}>
          <div className="content-inner">
            <Routes>
              <Route path="/" element={<Navigate to="/manual-nodes" replace />} />
              <Route path="/manual-nodes" element={<ManualNodes />} />
              <Route path="/data" element={<DataPage />} />
              <Route path="/www-research" element={<WWWResearch />} />
              <Route path="/interactive-www" element={<InteractiveWWW />} />
              <Route path="/deeptesting" element={<DeepTesting />} />
              <Route path="/monte-carlo" element={<MonteCarlo />} />
              <Route path="/fine-tuning" element={<FineTuning />} />
              <Route path="/live-testing" element={<LiveTesting />} />
              <Route path="/log" element={<LogView />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/manual-nodes" replace />} />
            </Routes>
          </div>
        </main>
      </div>
      <GlobalLiveFeed />
    </div>
  )
}

export default function App() {
  return (
    <InstrumentProvider>
      <TimeframeScopeProvider>
      <StarScopeProvider>
        <TasksProvider>
          <FeedProvider>
            <Frame />
          </FeedProvider>
        </TasksProvider>
      </StarScopeProvider>
      </TimeframeScopeProvider>
    </InstrumentProvider>
  )
}
