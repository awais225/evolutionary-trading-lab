import { useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { InstrumentProvider } from './state/instrument'
import { FeedProvider, useFeed } from './state/feed'
import { StarScopeProvider } from './state/starScope'
import { TimeframeScopeProvider } from './state/timeframeScope'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { GlobalLiveFeed } from './components/GlobalLiveFeed'
import { ManualNodes } from './pages/ManualNodes'
import { Settings } from './pages/Settings'
import { WWWResearch, InteractiveWWW, DeepTesting, FineTuning, LiveTesting } from './pages/_placeholderPages'
import { DataPage } from './pages/Data'
import { LogView } from './pages/Log'

function Frame() {
  const { open } = useFeed()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  return (
    <div className={`app-frame${open ? ' feed-open' : ''}`}>
      <Sidebar open={sidebarOpen} />
      <div className="main-column">
        <TopBar onMenu={() => setSidebarOpen((v) => !v)} />
        <main className="content" key={location.pathname}>
          <div className="content-inner">
            <Routes>
              <Route path="/" element={<Navigate to="/manual-nodes" replace />} />
              <Route path="/manual-nodes" element={<ManualNodes />} />
              <Route path="/data" element={<DataPage />} />
              <Route path="/www-research" element={<WWWResearch />} />
              <Route path="/interactive-www" element={<InteractiveWWW />} />
              <Route path="/deeptesting" element={<DeepTesting />} />
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
        <FeedProvider>
          <Frame />
        </FeedProvider>
      </StarScopeProvider>
      </TimeframeScopeProvider>
    </InstrumentProvider>
  )
}
