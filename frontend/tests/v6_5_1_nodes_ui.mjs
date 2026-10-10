/* V6.5.1 acceptance driver — bundles tests/v6_5_1_nodes_ui.jsx with the
 * project's own esbuild, runs it in a jsdom window with the network stubbed,
 * and reports each acceptance behaviour. Exits non-zero on any failure.
 *
 * Usage (from frontend/):  node tests/v6_5_1_nodes_ui.mjs
 */
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const here = path.dirname(new URL(import.meta.url).pathname);
const outDir = mkdtempSync(path.join(tmpdir(), "v651-ui-"));
const outFile = path.join(outDir, "bundle.mjs");

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
globalThis.SVGElement = dom.window.SVGElement;
globalThis.HTMLDivElement = dom.window.HTMLDivElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.Event = dom.window.Event;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.KeyboardEvent = dom.window.KeyboardEvent;
globalThis.localStorage = dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
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

class FakeWebSocket {
  constructor() { this.readyState = 0; }
  close() {}
  send() {}
}
globalThis.WebSocket = FakeWebSocket;
dom.window.WebSocket = FakeWebSocket;

const fetchCalls = [];
let payloadFactory = () => ({});
function installFetch() {
  globalThis.fetch = async (url, opts = {}) => {
    fetchCalls.push(String(url));
    const body = payloadFactory(String(url), (opts && opts.method) || "GET");
    const resolved = body instanceof Promise ? await body : body;
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      async json() { return resolved; },
      async text() { return JSON.stringify(resolved); },
    };
  };
  dom.window.fetch = globalThis.fetch;
}
installFetch();

let code = 0;
try {
  await build({
    entryPoints: [path.join(here, "v6_5_1_nodes_ui.jsx")],
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

  console.log(`v6.5.1 acceptance harness: ${result.total - result.failures.length}/${result.total} behaviours OK`);
  for (const f of result.failures) console.log(`  FAIL ${f.label}: ${f.error}`);
  if (result.failures.length) {
    code = 1;
  } else {
    console.log("  lifecycle, inline editing, account panel and identity behaviours all verified");
  }
} catch (e) {
  console.error("v6.5.1 acceptance harness failed to run:", e);
  code = 2;
} finally {
  try { rmSync(outDir, { recursive: true, force: true }); } catch {}
}

process.exit(code);
