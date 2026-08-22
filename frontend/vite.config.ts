import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(root, "./src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    // The backend stays on 8000; proxying keeps the browser same-origin so
    // there is no CORS surface in development.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/.well-known": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/docs": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/openapi.json": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
