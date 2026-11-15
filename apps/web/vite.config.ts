import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API runs on port 8000 in development; in the container the same server serves both.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The GeoTIFF readers of maplibre-gl-raster run in module workers.
  worker: { format: "es" },
  server: {
    port: 5173,
    // locales/ and docs/contratos/examples/ live above apps/web; the dev server must serve them.
    fs: { allow: ["../.."] },
    proxy: { "/api": "http://localhost:8000", "/health": "http://localhost:8000", "/q/": "http://localhost:8000" },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], css: false },
});
