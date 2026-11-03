import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../tokens.css'
import './landing.css'
import { Landing } from './Landing'

// Old links to the app (/?view=day...) now live under /app/.
if (new URLSearchParams(window.location.search).has('view')) {
  window.location.replace(`/app/${window.location.search}`)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
)
