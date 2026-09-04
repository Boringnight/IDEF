import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    // 放行 cloudflared 快速隧道的 Host(*.trycloudflare.com)，Vite 5.4 默认会以 403 拦截非白名单 Host
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      // 前端与后端走同源代理，这样一条穿透隧道即可承载整站
      "/ws": { target: "http://localhost:5000", ws: true, changeOrigin: true },
      "/action": { target: "http://localhost:5000", changeOrigin: true },
    },
  },
});
