/* V4.7 render-smoke driver.
 *
 * Bundles tests/v47_pages_smoke.jsx with the project's own esbuild, runs it in a
 * jsdom window with the network stubbed, and reports the result. Exits non-zero
 * if any page or node-detail render throws or emits undefined/NaN/[object Object].
 *
 * Usage (from frontend/):  node tests/v47_render_smoke.mjs
 */
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const here = path.dirname(new URL(import.meta.url).pathname);
const outDir = mkdtempSync(path.join(tmpdir(), "v47-smoke-"));
const outFile = path.join(outDir, "bundle.mjs");

// ---- jsdom environment, installed before the bundle is imported ----
const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://localhost:8787/",
  pretendToBeVisual: true,
});

globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.navigator = dom.window.navigator;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Element = dom.window.Element;
globalThis.Node = dom.window.Node;
// DOM classes the graph view (react-flow) checks with instanceof
globalThis.SVGElement = dom.window.SVGElement;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.HTMLDivElement = dom.window.HTMLDivElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.Event = dom.window.Event;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.localStorage = dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
// chart libraries (recharts) measure their container - jsdom has no layout but
// they only need the observer to exist
class FakeObserver {
  constructor(cb) { this.cb = cb; }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}
globalThis.ResizeObserver = FakeObserver;
dom.window.ResizeObserver = FakeObserver;
globalThis.IntersectionObserver = FakeObserver;
dom.window.IntersectionObserver = FakeObserver;
dom.window.matchMedia = dom.window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

// WebSocket stub (the App shell opens one; no server in this harness)
class FakeWebSocket {
  constructor() { this.readyState = 0; }
  close() {}
  send() {}
}
globalThis.WebSocket = FakeWebSocket;
dom.window.WebSocket = FakeWebSocket;

// fetch stub: every dashboard endpoint answers with data the real components
// must be able to survive. Installed after the bundle is built (the payload
// factory lives in the bundle).
const fetchCalls = [];
let payloadFactory = () => ({});
function installFetch() {
  globalThis.fetch = async (url) => {
    fetchCalls.push(String(url));
    const body = payloadFactory(String(url));
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      async json() { return body; },
      async text() { return JSON.stringify(body); },
    };
  };
  dom.window.fetch = globalThis.fetch;
}
installFetch();

let code = 0;
try {
  await build({
    entryPoints: [path.join(here, "v47_pages_smoke.jsx")],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2020",
    outfile: outFile,
    jsx: "automatic",
    loader: { ".js": "jsx" },
    logLevel: "warning",
    define: { "process.env.NODE_ENV": '"development"' },
    external: [],
  });

  const mod = await import(pathToFileURL(outFile).href);
  if (typeof mod.payloadFor === "function") payloadFactory = mod.payloadFor;
  installFetch();
  const result = await mod.runSmoke();
  console.log(`  (stubbed ${fetchCalls.length} API calls)`);

  console.log(`render smoke: ${result.total - result.failures.length}/${result.total} renders OK`);
  for (const f of result.failures) console.log(`  FAIL ${f.label}: ${f.error}`);
  if (result.failures.length) {
    console.log("  (failures above are real rendering crashes / unsafe output)");
    code = 1;
  } else {
    console.log("  no page or node-detail render crashed; no undefined/NaN/[object Object] rendered");
  }
} catch (e) {
  console.error("render smoke harness failed to run:", e);
  code = 2;
} finally {
  try { rmSync(outDir, { recursive: true, force: true }); } catch {}
}

process.exit(code);
