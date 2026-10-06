import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard talks to the Python backend through relative URLs only.
// Vite proxies /api and /ws to the backend so the browser never needs to
// know backend hostnames (works behind the e2b preview proxy and on
// localhost alike).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    proxy: {
      "/health": { target: "http://127.0.0.1:8787", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8787", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
