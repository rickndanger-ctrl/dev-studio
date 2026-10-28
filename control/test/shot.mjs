#!/usr/bin/env node
// Screenshot the Studio Routing viewer through headless Chromium over the DevTools protocol.
// Waits until the page is live (its EventSource opened) and the stage has drawn nodes, so the
// PNG is never the blank "connecting" state that chromium --screenshot captured at page load.
// Usage: node test/shot.mjs URL OUTDIR [WxH ...]     default sizes: 1280x800 1920x1080
// Needs node with the built-in WebSocket (node 22+) and a chromium binary (CHROME overrides /usr/bin/chromium).
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, outDir, ...sizeArgs] = process.argv.slice(2);
if (!url || !outDir) {
  console.error("usage: node test/shot.mjs URL OUTDIR [WxH ...]");
  process.exit(2);
}
const sizes = sizeArgs.length ? sizeArgs : ["1280x800", "1920x1080"];
const CHROME = process.env.CHROME || "/usr/bin/chromium";
const READY_TIMEOUT_MS = 30000;
const SETTLE_TIMEOUT_MS = 60000;

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(check, what, timeoutMs) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out after ${timeoutMs / 1000}s waiting for ${what}`);
    await sleep(250);
  }
}

async function main() {
  mkdirSync(outDir, { recursive: true });
  const profile = mkdtempSync(join(tmpdir(), "shot-"));
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "--window-size=1280,800", "about:blank",
  ], { stdio: "ignore" });

  let ws;
  try {
    // Chromium writes the DevTools port and browser path to DevToolsActivePort once it is listening.
    const [port, path] = await waitFor(() => {
      const f = join(profile, "DevToolsActivePort");
      return existsSync(f) ? readFileSync(f, "utf8").trim().split("\n") : null;
    }, "chromium DevToolsActivePort", 20000);

    ws = new WebSocket(`ws://127.0.0.1:${port}${path}`);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("DevTools socket failed")); });

    let nextId = 0;
    const pending = new Map();
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (!m.id || !pending.has(m.id)) return;
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) rej(new Error(`${m.error.message} (${m.error.data || ""})`));
      else res(m.result);
    };
    const send = (method, params = {}, sessionId) => {
      const id = ++nextId;
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      return new Promise((res, rej) => pending.set(id, { res, rej }));
    };

    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    await send("Page.enable", {}, sessionId);

    const state = async () => {
      const r = await send("Runtime.evaluate", {
        expression: `JSON.stringify({
          live: document.getElementById("connText")?.textContent === "live",
          nodes: document.querySelectorAll("#stage *").length,
          connecting: [...document.querySelectorAll("#stage *")].filter(e => e.children.length === 0 && e.textContent.trim() === "connecting").length
        })`,
        returnByValue: true,
      }, sessionId);
      return JSON.parse(r.result.value);
    };

    for (const size of sizes) {
      const [w, h] = size.split("x").map(Number);
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sessionId);
      await send("Page.navigate", { url }, sessionId);

      let last = null;
      await waitFor(async () => {
        last = await state();
        return last.live && last.nodes > 0;
      }, `live viewer at ${size} (last state ${JSON.stringify(last)})`, READY_TIMEOUT_MS);

      // Mini panels say "connecting" until their first SSH data arrives. Give them up to 60s; warn, do not fail.
      const settled = await waitFor(async () => {
        last = await state();
        return last.connecting === 0;
      }, "Mini panels to leave connecting", SETTLE_TIMEOUT_MS).then(() => true, () => false);
      if (!settled) console.warn(`warning: ${last.connecting} panel(s) still connecting at ${size} after 60s`);

      await sleep(1500);
      const { data } = await send("Page.captureScreenshot", { format: "png" }, sessionId);
      const file = join(outDir, `viewer-${w}x${h}.png`);
      writeFileSync(file, Buffer.from(data, "base64"));
      console.log(file);
    }
  } finally {
    if (ws) ws.close();
    chrome.kill();
    await sleep(300);
    rmSync(profile, { recursive: true, force: true });
  }
}

main().catch(err => {
  console.error(`shot failed: ${err.message}`);
  process.exit(1);
});
