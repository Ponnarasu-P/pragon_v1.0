import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    proxy: {
      '/livekit': 'http://localhost:8080',
      '/customdraw': 'http://localhost:8080',
      '/pulse': 'http://localhost:8080',
      '/api': 'http://localhost:8080',
      '/friday': 'http://localhost:8080',
      '/ghost': 'http://localhost:8080',
      '/generate-image': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8765',
        ws: true,
      }
    }
  }
})
