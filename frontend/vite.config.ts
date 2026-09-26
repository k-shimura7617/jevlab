import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// ビルド成果物は FastAPI が /static で配信する（同一ポート・同一オリジン）。
// 開発時は Vite の dev サーバから API を FastAPI（既定 127.0.0.1:8000）へ中継する。
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/static/' : '/',
  build: {
    outDir: '../src/jevlab/static',
    emptyOutDir: true,
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': process.env.JEVLAB_API_URL ?? 'http://127.0.0.1:8000',
    },
  },
}))
