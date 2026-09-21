import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig({
  base: '/dashboard/',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/health': 'http://127.0.0.1:8000',
      '/system/mode': 'http://127.0.0.1:8000',
      '/paper/status': 'http://127.0.0.1:8000',
      '/paper/account': 'http://127.0.0.1:8000',
      '/paper/positions/live': 'http://127.0.0.1:8000',
      '/approvals': 'http://127.0.0.1:8000',
      '/watchlist': 'http://127.0.0.1:8000',
      '/opportunity-alerts': 'http://127.0.0.1:8000',
      '/portfolio': 'http://127.0.0.1:8000',
      '/portfolio/history/live': 'http://127.0.0.1:8000',
      '/investment-committee': 'http://127.0.0.1:8000',
      '/market-overview': 'http://127.0.0.1:8000',
      '/execution-audit': 'http://127.0.0.1:8000',
      '/audit': 'http://127.0.0.1:8000',
      '/paper/preview': 'http://127.0.0.1:8000',
      '/paper/approval': 'http://127.0.0.1:8000',
      '/paper/execute': 'http://127.0.0.1:8000',
      '/paper/reconcile': 'http://127.0.0.1:8000',
      '/paper/risk-snapshot': 'http://127.0.0.1:8000',
      '/news': 'http://127.0.0.1:8000',
      '/stock': 'http://127.0.0.1:8000',
      '/api': 'http://127.0.0.1:8000',
    }
  }
})
