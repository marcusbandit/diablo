import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server sends every hub call to the hub named in DIABLO_DEV_HUB, so the app runs
// same origin in dev the way it does when the hub serves the build at /app.
const hub = process.env.DIABLO_DEV_HUB ?? "http://127.0.0.1:8755";
const repo = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: {
    port: Number(process.env.PORT ?? 5173),
    host: "0.0.0.0",
    fs: { allow: [repo] },
    proxy: {
      "/v1": { target: hub, changeOrigin: true },
    },
  },
});
