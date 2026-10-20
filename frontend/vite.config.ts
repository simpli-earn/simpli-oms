import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.OMS_BACKEND_URL ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/docs": backend,
      "/redoc": backend,
      "/openapi.json": backend,
    },
  },
});
