import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    rollupOptions: {
      input: 'static/src/main.js',
      output: {
        dir: 'static/dist',
        entryFileNames: 'main.js'
      }
    }
  }
});
