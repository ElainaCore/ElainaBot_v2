import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/web/',
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': { target: 'ws://localhost:8080', ws: true },
    },
  },
  build: {
    target: 'es2015',
    outDir: '../ElainaBot_v2/web/dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // 配置页随设置项更新更换 URL，避免旧 Config.js 缓存遮住新增开关。
        chunkFileNames: chunk => chunk.name === 'Config' ? 'assets/[name]-[hash].js' : 'assets/[name].js',
        entryFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('naive-ui')) return 'naive-ui'
          if (id.includes('chart.js') || id.includes('vue-chartjs') || id.includes('chartjs-plugin')) return 'charts'
          if (id.includes('js-yaml')) return 'js-yaml'
          // Keep qrcode and its CommonJS dependencies together to avoid initialization cycles.
          return 'vendor'
        },
      },
    },
  },
})
