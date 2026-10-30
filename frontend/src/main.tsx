import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initPwa } from './pwa'
import { progressStart, hideSplash } from './progress'

// Start the top progress bar as early as possible — the module bundle is still
// downloading while the inline splash covers the first paint at this point.
progressStart()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// React has committed its first frame by now. Fade the splash out on the next
// animation frame so it covers the very first paint — never a white flash.
requestAnimationFrame(() => hideSplash())

void initPwa()