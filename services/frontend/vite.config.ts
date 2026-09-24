import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // Matched by prefix, so "/chat" also routes "/chats" and "/chats/{id}/messages".
      "/chat": "http://localhost:8000",
      "/faq": "http://localhost:8000",
      "/console": "http://localhost:8000",
      // "/admin" is deliberately absent: nothing in the browser calls it.
    },
  },
});
