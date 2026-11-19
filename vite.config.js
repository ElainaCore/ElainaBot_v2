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
        // 资源使用固定文件名，禁止添加内容哈希；更新由服务器缓存控制处理。
        chunkFileNames: 'assets/[name].js',
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
