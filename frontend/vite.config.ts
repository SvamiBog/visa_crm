import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The libraries every page needs go into a file of their own: it changes only when they
// are updated, so after a deploy of our code a phone keeps it from its cache. Only these,
// by name: the passport reader's engine must stay in the file loaded when a photo is read.
const VENDOR =
  /[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom|@remix-run[\\/]router|@tanstack[\\/](query-core|react-query)|i18next|react-i18next)[\\/]/;

// In dev, Vite serves the UI and proxies /api to FastAPI on :8000.
// In production FastAPI serves the built files from frontend/dist (same origin).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id) => (VENDOR.test(id) ? "vendor" : undefined),
      },
    },
  },
});
