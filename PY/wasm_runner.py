"""Drive UG's Pro-tab WASM player headlessly and dump the parsed score JSON
plus surrounding metadata.

Why drive the live page instead of a standalone harness?

The "XTZ" player is an Emscripten/WASM engine (~MB binary, audio + notation
+ canvas renderer). To run it we'd otherwise have to recreate setUpNotationApi,
provide a canvas, mount fonts and the .mss notation style, plumb the audio
context, etc. Far simpler and more robust: use the authenticated Camoufox
session to load the Pro tab page UG already wrote, and hook the JSON-shaped
exits (`JSON.parse` boundary, `fetch` blobs, `WebAssembly.instantiate`).

The score JSON crosses JS at chunk 8811 @ 84158:

    function i(e, n) { var a = JSON.parse(n); t.onScoreLoaded && t.onScoreLoaded(e, a) }

i.e. the WASM emits the entire score as a JSON string, which the glue parses
before fanning it out to React. Hooking JSON.parse before any page script runs
is enough to grab the whole structure, plus every other JSON-bearing callback
(canvas size, instruments, playable parts, playing notes, lyrics, etc.).

Output layout (under PY/captures/<ts>-<route>-json/):

    manifest.json          summary
    score.json             the score JSON (the prize)
    score-meta.json        when it landed, parse idx, input length
    parses/                every JSON.parse capture (idx-prefixed); useful as a
                           fallback if score.json detection misses, and for the
                           secondary callback payloads
    blobs/                 raw XTZ download, WASM modules, notation-style .mss
    blobs/index.jsonl      url/status/content-type per blob
    wasm-modules.json      import surface + export names per WebAssembly module
    ugapp-store.json       page-embedded store JSON (artist, song, tab metadata)
    page-snapshot.html     rendered DOM at exit
    errors.json            console.error + window.error captures, if any
    browser.log            console + pageerror stream

Limitations
- Captures only what crosses JS. Anything the WASM keeps internal (e.g. a beat
  list never emitted to JS) won't appear here. If score.json is missing
  note-level structure, see JSON_GP7.md for follow-up paths.
- Targets a tab by URL/route; doesn't ingest an arbitrary local XTZ blob into a
  freshly-built player harness. (Possible but a separate, larger build.)
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import time
from pathlib import Path

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
from playwright.sync_api import Page

from common import (
    BrowserConsoleLogger,
    CloudflareChallengeTimeout,
    file_timestamp,
    timestamp,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)
from login import attach_auth_diagnostics, login

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UG_PROXY = os.getenv("UG_PROXY")
CAMOUFOX_DISABLE_COOP = os.getenv("CAMOUFOX_DISABLE_COOP", "").lower() in {
    "1", "true", "yes", "on",
}

TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"
DEFAULT_TAB_ROUTE = "eagles/hotel-california-official-1910943"
BROWSER_PROFILE_DIR = BASE_DIR / "camoufox-profile"

MIN_PARSE_INPUT_LEN = 64           # ignore tiny config-shaped JSON
MAX_PARSES_TO_KEEP = 1500          # safety cap on JSON.parse captures
MAX_BLOB_BYTES = 8 * 1024 * 1024   # cap per-blob capture size

INSTRUMENTATION = r"""
(() => {
  if (window.__xtzCapture) return;
  const cap = window.__xtzCapture = {
    parses: [],          // [{idx, ts, inputLen, shape, keys, result}]
    score: null,         // first parse whose result has a `parts` array
    parseCount: 0,
    parsesDropped: 0,
    fetchBlobs: [],      // {via, url, status, contentType, bytesLen, base64?}
    wasmModules: [],     // {bytesLen?, importNamespaces, importNames, exportNames, streaming?}
    consoleErrors: [],
    pageErrors: [],
    misc: {},
  };

  const MIN_LEN = __MIN_PARSE_INPUT_LEN__;
  const MAX_KEEP = __MAX_PARSES_TO_KEEP__;
  const MAX_BLOB = __MAX_BLOB_BYTES__;

  // Keep originals so our hooks don't recurse on our own bookkeeping.
  const origParse = JSON.parse;
  const origStringify = JSON.stringify;
  const origInstantiate = WebAssembly.instantiate;
  const origInstantiateStreaming = WebAssembly.instantiateStreaming;
  const origFetch = window.fetch;

  // 1) JSON.parse hook ------------------------------------------------------
  // The XTZ glue calls JSON.parse(...) inside several event handlers. The big
  // one is `function i(e, n) { var a = JSON.parse(n); ...onScoreLoaded(e, a) }`
  // (chunk 8811 @ 84158) -- that's the entire score arriving as a JSON string.
  JSON.parse = function (text, reviver) {
    const idx = cap.parseCount++;
    const result = origParse.apply(this, arguments);
    try {
      const inputLen = typeof text === "string" ? text.length : 0;
      if (inputLen >= MIN_LEN && result && typeof result === "object") {
        if (cap.parses.length < MAX_KEEP) {
          const isArr = Array.isArray(result);
          const keys = isArr ? null : Object.keys(result).slice(0, 80);
          cap.parses.push({ idx, inputLen, ts: Date.now(), shape: isArr ? "array" : "object", keys, result });
        } else {
          cap.parsesDropped++;
        }
        if (
          !cap.score &&
          !Array.isArray(result) &&
          Array.isArray(result.parts) &&
          ("duration" in result || "startTempoBpm" in result || "lyrics" in result || result.parts.length > 0)
        ) {
          cap.score = { idx, inputLen, ts: Date.now(), result };
        }
      }
    } catch (_) {}
    return result;
  };

  // 2) WebAssembly hooks ----------------------------------------------------
  const summarizeImports = (importObj) => {
    const importNamespaces = importObj ? Object.keys(importObj) : [];
    const importNames = {};
    for (const ns of importNamespaces) {
      try { importNames[ns] = Object.keys(importObj[ns] || {}); } catch (_) {}
    }
    return { importNamespaces, importNames };
  };
  const summarizeExports = (instLike) => {
    const inst = instLike && instLike.instance ? instLike.instance : instLike;
    return inst && inst.exports ? Object.keys(inst.exports) : [];
  };

  WebAssembly.instantiate = async function (...args) {
    const r = await origInstantiate.apply(this, args);
    try {
      const buf = args[0];
      const bytesLen =
        (buf && buf.byteLength) || (buf && buf.buffer && buf.buffer.byteLength) || -1;
      cap.wasmModules.push({
        bytesLen,
        ...summarizeImports(args[1]),
        exportNames: summarizeExports(r),
      });
    } catch (_) {}
    return r;
  };
  if (origInstantiateStreaming) {
    WebAssembly.instantiateStreaming = async function (...args) {
      const r = await origInstantiateStreaming.apply(this, args);
      try {
        cap.wasmModules.push({
          streaming: true,
          ...summarizeImports(args[1]),
          exportNames: summarizeExports(r),
        });
      } catch (_) {}
      return r;
    };
  }

  // 3) fetch hook -----------------------------------------------------------
  const interesting = (url) => {
    if (typeof url !== "string") return false;
    const lower = url.toLowerCase();
    return (
      lower.includes("/tab/download/") ||
      lower.includes("/download/public/") ||
      lower.endsWith(".wasm") ||
      lower.endsWith(".mss") ||
      lower.includes("xtz-player")
    );
  };
  const bytesToBase64 = (bytes) => {
    const chunk = 0x8000;
    let bin = "";
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(bin);
  };
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const resp = await origFetch.apply(this, arguments);
    try {
      if (interesting(url)) {
        const clone = resp.clone();
        const ct = clone.headers && clone.headers.get ? clone.headers.get("content-type") : "";
        const buf = await clone.arrayBuffer();
        const bytes = new Uint8Array(buf);
        const entry = { via: "fetch", url, status: clone.status, contentType: ct, bytesLen: bytes.length };
        if (bytes.length <= MAX_BLOB) {
          entry.base64 = bytesToBase64(bytes);
        } else {
          entry.skipped = "too_large";
        }
        cap.fetchBlobs.push(entry);
      }
    } catch (_) {}
    return resp;
  };

  // 4) Errors ---------------------------------------------------------------
  window.addEventListener("error", (ev) => {
    try { cap.pageErrors.push({ ts: Date.now(), msg: String(ev.error || ev.message), src: (ev.filename || "") + ":" + (ev.lineno || 0) }); } catch (_) {}
  });
  const origConsoleError = console.error;
  console.error = function (...args) {
    try { cap.consoleErrors.push({ ts: Date.now(), msg: args.map(String).join(" ") }); } catch (_) {}
    return origConsoleError.apply(this, args);
  };

  // 5) Page metadata snapshot ----------------------------------------------
  const snapshot = () => {
    try {
      cap.misc.url = location.href;
      cap.misc.title = document.title;
      cap.misc.userAgent = navigator.userAgent;
      cap.misc.snapshotAt = Date.now();
      const el = document.querySelector(".js-store");
      if (el) {
        const raw = el.getAttribute("data-content");
        if (raw) {
          try { cap.misc.jsStore = origParse(raw); } catch (e) { cap.misc.jsStoreError = String(e); }
        }
      }
      if (window.UGAPP) {
        try {
          const safe = origStringify(window.UGAPP, (k, v) => typeof v === "function" ? undefined : v);
          cap.misc.ugapp = origParse(safe);
        } catch (e) { cap.misc.ugappError = String(e); }
      }
    } catch (_) {}
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", snapshot);
  } else {
    snapshot();
  }
  window.addEventListener("load", snapshot);
})();
"""

INSTRUMENTATION = (
    INSTRUMENTATION
    .replace("__MIN_PARSE_INPUT_LEN__", str(MIN_PARSE_INPUT_LEN))
    .replace("__MAX_PARSES_TO_KEEP__", str(MAX_PARSES_TO_KEEP))
    .replace("__MAX_BLOB_BYTES__", str(MAX_BLOB_BYTES))
)


def safe_slug(value: str, fallback: str = "tab") -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")[:120] or fallback


def wait_for_score(page: Page, timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            ready = page.evaluate("() => !!(window.__xtzCapture && window.__xtzCapture.score)")
            if ready:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def write_blobs(blobs: list[dict], blobs_dir: Path) -> None:
    blobs_dir.mkdir(parents=True, exist_ok=True)
    index_path = blobs_dir / "index.jsonl"
    for i, blob in enumerate(blobs):
        url = blob.get("url", "")
        url_tail = re.sub(r"[^a-zA-Z0-9._-]+", "-", url).strip("-")[-100:] or "blob"
        if "/tab/download/" in url or "/download/public/" in url:
            ext = ".gp"
        elif url.lower().endswith(".wasm"):
            ext = ".wasm"
        elif url.lower().endswith(".mss"):
            ext = ".mss"
        else:
            ext = ".bin"
        b64 = blob.get("base64")
        if b64:
            try:
                data = base64.b64decode(b64)
                (blobs_dir / f"{i:03d}-{url_tail}{ext}").write_bytes(data)
            except (binascii.Error, ValueError) as e:
                blob = {**blob, "decode_error": str(e)}
        meta = {k: v for k, v in blob.items() if k != "base64"}
        with index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(meta) + "\n")


def write_parses(parses: list[dict], parses_dir: Path) -> None:
    parses_dir.mkdir(parents=True, exist_ok=True)
    for parse in parses:
        idx = parse.get("idx", 0)
        if parse.get("shape") == "array":
            label = "array"
        else:
            keys = parse.get("keys") or []
            label = "-".join(keys[:4]) or "obj"
        label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label)[:40] or "obj"
        (parses_dir / f"{idx:05d}-{label}.json").write_text(
            json.dumps(parse, indent=2), encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--route",
        default=DEFAULT_TAB_ROUTE,
        help="Tab route under tabs.ultimate-guitar.com/tab/ (default: %(default)s)",
    )
    parser.add_argument(
        "--score-timeout-ms",
        type=int,
        default=90_000,
        help="ms to wait for the score JSON to appear (default: %(default)s)",
    )
    parser.add_argument(
        "--linger-ms",
        type=int,
        default=10_000,
        help="ms to wait after the score lands, to pick up follow-up callbacks (default: %(default)s)",
    )
    args = parser.parse_args()

    tab_url = f"{TAB_BASE_URL}/{args.route.strip('/')}"
    out_dir = BASE_DIR / "captures" / f"{file_timestamp()}-{safe_slug(args.route)}-json"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {out_dir}")

    console_logger = BrowserConsoleLogger(out_dir / "browser.log")

    options: dict = {
        "headless": False,
        "humanize": True,
        "persistent_context": True,
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "os": "windows",
        "locale": "en-US",
        "block_webrtc": True,
    }
    if UG_PROXY:
        options["proxy"] = {"server": UG_PROXY}
        options["geoip"] = True
    if CAMOUFOX_DISABLE_COOP:
        options["disable_coop"] = True
        options["i_know_what_im_doing"] = True

    cap: dict = {}
    page_html = ""

    try:
        with Camoufox(**options) as context:
            console_logger.attach_to_context(context)
            context.add_init_script(INSTRUMENTATION)

            page = context.pages[0] if context.pages else context.new_page()
            console_logger.attach_to_page(page)
            attach_auth_diagnostics(page, console_logger)

            if not login(page, console_logger):
                print("Login failed.")
                return

            print(f"Navigating to {tab_url}")
            page.goto(tab_url, wait_until="domcontentloaded", timeout=60_000)
            wait_for_load_or_pause(page)
            wait_for_cloudflare_wall(page, console_logger)

            score_loaded = wait_for_score(page, args.score_timeout_ms)
            if score_loaded:
                print("Score JSON detected. Lingering for follow-up callbacks...")
                page.wait_for_timeout(args.linger_ms)
            else:
                print("Timed out waiting for score JSON. Dumping whatever we captured.")

            cap = page.evaluate("() => JSON.parse(JSON.stringify(window.__xtzCapture || {}))")
            page_html = page.content()
    except CloudflareChallengeTimeout as e:
        print(e)
    finally:
        console_logger.close()

    # ---- persist ----
    misc = cap.get("misc", {}) or {}
    score = cap.get("score")

    manifest = {
        "captured_at": timestamp(),
        "tab_url": tab_url,
        "route": args.route,
        "page_title": misc.get("title"),
        "page_url_at_snapshot": misc.get("url"),
        "user_agent": misc.get("userAgent"),
        "parse_count_total": cap.get("parseCount"),
        "parses_kept": len(cap.get("parses", []) or []),
        "parses_dropped_overflow": cap.get("parsesDropped", 0),
        "score_present": score is not None,
        "score_input_len": (score or {}).get("inputLen"),
        "wasm_modules": len(cap.get("wasmModules", []) or []),
        "fetch_blobs": len(cap.get("fetchBlobs", []) or []),
        "console_errors": len(cap.get("consoleErrors", []) or []),
        "page_errors": len(cap.get("pageErrors", []) or []),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if score:
        (out_dir / "score.json").write_text(json.dumps(score["result"], indent=2), encoding="utf-8")
        meta = {k: v for k, v in score.items() if k != "result"}
        (out_dir / "score-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    write_parses(cap.get("parses", []) or [], out_dir / "parses")
    write_blobs(cap.get("fetchBlobs", []) or [], out_dir / "blobs")

    (out_dir / "wasm-modules.json").write_text(
        json.dumps(cap.get("wasmModules", []) or [], indent=2), encoding="utf-8"
    )

    if misc.get("ugapp") or misc.get("jsStore"):
        store = misc.get("ugapp") or misc.get("jsStore")
        (out_dir / "ugapp-store.json").write_text(json.dumps(store, indent=2), encoding="utf-8")

    if cap.get("consoleErrors") or cap.get("pageErrors"):
        (out_dir / "errors.json").write_text(
            json.dumps({
                "consoleErrors": cap.get("consoleErrors", []) or [],
                "pageErrors": cap.get("pageErrors", []) or [],
            }, indent=2),
            encoding="utf-8",
        )

    if page_html:
        (out_dir / "page-snapshot.html").write_text(page_html, encoding="utf-8")

    print()
    print(f"Done. Output: {out_dir}")
    print(f"  score.json present:   {manifest['score_present']}  (input_len={manifest['score_input_len']})")
    print(f"  parses kept:          {manifest['parses_kept']}  (dropped={manifest['parses_dropped_overflow']})")
    print(f"  blobs saved:          {manifest['fetch_blobs']}")
    print(f"  wasm modules:         {manifest['wasm_modules']}")
    print(f"  errors:               console={manifest['console_errors']} page={manifest['page_errors']}")


if __name__ == "__main__":
    main()
