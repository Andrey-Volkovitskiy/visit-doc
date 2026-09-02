import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
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
