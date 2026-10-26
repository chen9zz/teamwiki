import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          includeDependenciesRecursively: false,
          groups: [
            { name: 'graph', test: /cytoscape/ },
            { name: 'syntax-parser', test: /@lezer/, priority: 10 },
            { name: 'editor', test: /codemirror/, maxSize: 450_000 },
            { name: 'markdown', test: /react-markdown|remark-|micromark|mdast-|hast-/ },
          ],
        },
      },
    },
  },
});
