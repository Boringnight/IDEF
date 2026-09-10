// 备注:必须用 location.host(含端口)而非 location.hostname(不含端口)。
// 否则本地 :5174 访问时会把 /ws 连到默认 80 端口导致 ERR_CONNECTION_REFUSED,
// snap 永远收不到,页面卡在 boot 屏(无节点、无悬停)。
const HOST = typeof location !== "undefined" && location.host
  ? location.host
  : "127.0.0.1:5000";
// 走同源：API 直接相对路径，WebSocket 按当前协议(wss/ws)连到「主机名:端口」，
// 由 Vite dev server 代理到后端 :5000。
const WS_SCHEME = typeof location !== "undefined" && location.protocol === "https:" ? "wss" : "ws";
const API = "";
const RECONNECT_MS = 1500;   // 断线重连间隔

export function connectWS({ onInit, onSnap }) {
  let closed = false;
  let sock = null;
  function open() {
    sock = new WebSocket(`${WS_SCHEME}://${HOST}/ws`);
    sock.onmessage = (e) => {
      let m;
      try {
        m = JSON.parse(e.data);
      } catch (err) {
        console.warn("[LTRP] 忽略非法 WS 帧", err);   // 不静默吞掉:留痕便于排查
        return;
      }
      if (m.cmd === "init") onInit(m);
      else if (m.cmd === "snap") onSnap(m);
    };
    sock.onclose = () => {
      if (!closed) setTimeout(open, RECONNECT_MS);
    };
  }
  open();
  return {
    close: () => {
      closed = true;
      try { sock?.close(); } catch (err) { console.warn("[LTRP] 关闭 WS 失败", err); }
    },
  };
}

export async function api(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) console.warn(`[LTRP] ${path} 返回 HTTP ${r.status}`);   // 失败不静默
  return r;
}
