import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted, bundled with the app: no request to a third-party font CDN (which
// would tell Google about every view of a private dashboard), and the fonts
// still load offline. The design always named these two; until now nothing
// loaded them, so every page fell back to system fonts.
import '@fontsource-variable/inter'
import '@fontsource-variable/source-serif-4'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
