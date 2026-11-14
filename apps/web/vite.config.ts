import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.VIORIS_GATEWAY_URL ?? "http://127.0.0.1:8420";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VIORIS_UI_PORT ?? 5173),
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/auth": gateway,
      "/v1": gateway,
      "/devices": gateway,
      "/health": gateway,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three", "@designcodeio/threeui"],
          react: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
