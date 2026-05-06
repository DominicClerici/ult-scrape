import { createWriteStream, writeFileSync, type WriteStream } from "node:fs";
import { resolve } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { config } from "dotenv";
import { plugin as fingerprintPlugin } from "playwright-with-fingerprints";
import type {
  BrowserContext,
  ConsoleMessage,
  Locator,
  Page,
  Request,
  Response,
  Route,
} from "playwright";

config();

const UG_EMAIL = process.env.UG_EMAIL;
const UG_PASSWORD = process.env.UG_PASSWORD;
const FINGERPRINT_SERVICE_KEY = process.env.FINGERPRINT_SERVICE_KEY ?? "";
const FINGERPRINT_PROXY = process.env.FINGERPRINT_PROXY;

const TAB_URL =
  "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943";
const OUTPUT_FILE = "hotel_california.gp";
const DOWNLOAD_FILE_URL = /^https:\/\/tabs\.ultimate-guitar\.com\/tab\/download\/file\?/;
const BROWSER_PROFILE_DIR = "playwright-profile";
const FINGERPRINT_WORKING_FOLDER = "fingerprint-engine";
const MANUAL_LOGIN = false;
const FINGERPRINT_TAGS = ["Microsoft Windows", "Chrome"] as const;
const FINGERPRINT_REQUEST_TIMEOUT_MS = 5 * 60_000;
const FINGERPRINT_ENGINE_TIMEOUT_MS = 10 * 60_000;
const AUTH_LOG_URL_PARTS = [
  "/user/auth/processSignIn",
  "/v1/user/register/view",
] as const;
const REQUEST_HEADER_LOG_NAMES = [
  "accept",
  "content-type",
  "origin",
  "referer",
  "sec-fetch-dest",
  "sec-fetch-mode",
  "sec-fetch-site",
  "user-agent",
  "x-requested-with",
] as const;
const RESPONSE_HEADER_LOG_NAMES = [
  "access-control-allow-origin",
  "cache-control",
  "cf-ray",
  "content-type",
  "server",
  "x-cache",
] as const;
const AUTH_BODY_LOG_LIMIT = 3000;
const CLOUDFLARE_WAIT_TIMEOUT_MS = 120_000;
const CLOUDFLARE_POLL_INTERVAL_MS = 1000;
const CLOUDFLARE_CHALLENGE_SELECTOR = [
  "iframe[src*='challenges.cloudflare.com']",
  "iframe[src*='turnstile']",
  "input[name='cf-turnstile-response']",
  ".cf-turnstile",
  "[id*='cf-chl']",
  "[class*='cf-chl']",
].join(", ");
const CLOUDFLARE_CHALLENGE_TEXT = [
  "verify you are human",
  "checking if the site connection is secure",
  "cloudflare",
] as const;

class CloudflareChallengeTimeout extends Error {
  constructor() {
    super("Timed out waiting for the Cloudflare challenge to clear.");
    this.name = "CloudflareChallengeTimeout";
  }
}

class BrowserConsoleLogger {
  private readonly logPath: string;
  private readonly logFile: WriteStream;
  private readonly attachedPages = new WeakSet<Page>();

  constructor(logPath: string) {
    this.logPath = resolve(logPath);
    this.logFile = createWriteStream(this.logPath, {
      flags: "a",
      encoding: "utf8",
    });
  }

  attachToContext(context: BrowserContext): void {
    context.on("page", (page) => this.attachToPage(page));
  }

  attachToPage(page: Page): void {
    if (this.attachedPages.has(page)) {
      return;
    }

    this.attachedPages.add(page);
    page.on("console", (message) => this.writeConsoleMessage(page, message));
    page.on("pageerror", (error) => this.writePageError(page, error));
  }

  writeConsoleMessage(page: Page, message: ConsoleMessage): void {
    const location = message.location();
    const source = location.url || page.url() || "unknown";
    const line = location.lineNumber ?? 0;
    const column = location.columnNumber ?? 0;

    this.write(
      `[${timestamp()}] [console:${message.type()}] ${source}:${line}:${column} ${message.text()}`,
    );
  }

  writePageError(page: Page, error: Error): void {
    this.write(`[${timestamp()}] [pageerror] ${page.url() || "unknown"} ${error}`);
  }

  write(line: string): void {
    this.logFile.write(`${line}\n`);
  }

  close(): void {
    this.logFile.end();
  }
}

function timestamp(): string {
  const now = new Date();
  const pad = (value: number) => value.toString().padStart(2, "0");

  return [
    now.getFullYear(),
    "-",
    pad(now.getMonth() + 1),
    "-",
    pad(now.getDate()),
    "T",
    pad(now.getHours()),
    ":",
    pad(now.getMinutes()),
    ":",
    pad(now.getSeconds()),
  ].join("");
}

function fileTimestamp(): string {
  const now = new Date();
  const pad = (value: number) => value.toString().padStart(2, "0");

  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("");
}

function isAuthLogUrl(url: string): boolean {
  return AUTH_LOG_URL_PARTS.some((part) => url.includes(part));
}

function selectedHeaders(
  headers: Record<string, string>,
  names: readonly string[],
): Record<string, string> {
  return Object.fromEntries(names.filter((name) => name in headers).map((name) => [name, headers[name]]));
}

function sanitizeLogText(text: string | null | undefined): string {
  if (text == null) {
    return "";
  }

  let sanitized = text;

  for (const [secret, replacement] of [
    [UG_PASSWORD, "[REDACTED_PASSWORD]"],
    [UG_EMAIL, "[REDACTED_EMAIL]"],
  ] as const) {
    if (secret) {
      sanitized = sanitized.replaceAll(secret, replacement);
    }
  }

  sanitized = sanitized.replaceAll("\r", "\\r").replaceAll("\n", "\\n");

  if (sanitized.length > AUTH_BODY_LOG_LIMIT) {
    return `${sanitized.slice(0, AUTH_BODY_LOG_LIMIT)}... [truncated]`;
  }

  return sanitized;
}

function maskEmail(value: string | undefined): string {
  if (!value) {
    return "";
  }

  if (!value.includes("@")) {
    return `${value.slice(0, 2)}***`;
  }

  const [local, domain] = value.split("@", 2);
  const visible = local.length > 1 ? local.slice(0, 2) : local.slice(0, 1);
  return `${visible}***@${domain}`;
}

function attachAuthDiagnostics(page: Page, logger: BrowserConsoleLogger): void {
  page.on("request", (request) => {
    if (!isAuthLogUrl(request.url())) {
      return;
    }

    const postData = request.postData() ?? "";
    logger.write(
      `[${timestamp()}] [auth:request] ${request.method()} ${request.url()} ` +
        `headers=${JSON.stringify(selectedHeaders(request.headers(), REQUEST_HEADER_LOG_NAMES))} ` +
        `post_data_length=${postData.length}`,
    );
  });

  page.on("response", (response) => {
    void logAuthResponse(response, logger);
  });

  page.on("requestfailed", (request) => {
    if (!isAuthLogUrl(request.url())) {
      return;
    }

    logger.write(
      `[${timestamp()}] [auth:request-failed] ${request.method()} ${request.url()} ` +
        `failure=${JSON.stringify(request.failure())}`,
    );
  });
}

async function logAuthResponse(
  response: Response,
  logger: BrowserConsoleLogger,
): Promise<void> {
  if (!isAuthLogUrl(response.url())) {
    return;
  }

  logger.write(
    `[${timestamp()}] [auth:response] ${response.status()} ${response.url()} ` +
      `headers=${JSON.stringify(selectedHeaders(response.headers(), RESPONSE_HEADER_LOG_NAMES))}`,
  );

  try {
    const body = await response.text();
    logger.write(
      `[${timestamp()}] [auth:response-body] ${response.url()} ${sanitizeLogText(body)}`,
    );
  } catch (error) {
    logger.write(
      `[${timestamp()}] [auth:response-body-error] ${response.url()} ${String(error)}`,
    );
  }
}

async function humanPause(minMs = 120, maxMs = 420): Promise<void> {
  await sleep(randomInt(minMs, maxMs));
}

async function waitForLoadOrPause(
  page: Page,
  minMs = 10_000,
  maxMs = 15_000,
): Promise<void> {
  try {
    await page.waitForLoadState("load", { timeout: randomInt(minMs, maxMs) });
  } catch (error) {
    if (!isPlaywrightTimeout(error)) {
      throw error;
    }
  }
}

async function isCloudflareWall(page: Page): Promise<boolean> {
  const url = page.url().toLowerCase();

  if (
    url.includes("challenges.cloudflare.com") ||
    url.includes("/cdn-cgi/challenge-platform/")
  ) {
    return true;
  }

  try {
    if ((await page.locator(CLOUDFLARE_CHALLENGE_SELECTOR).count()) > 0) {
      return true;
    }
  } catch {
    // Keep checking the title/body when selector probing fails during navigation.
  }

  let title = "";
  try {
    title = (await page.title()).toLowerCase();
  } catch {
    title = "";
  }

  if (title.includes("just a moment") || title.includes("attention required")) {
    return true;
  }

  let bodyText = "";
  try {
    bodyText = (await page.locator("body").innerText({ timeout: 1000 })).toLowerCase();
  } catch {
    bodyText = "";
  }

  return CLOUDFLARE_CHALLENGE_TEXT.some((text) => bodyText.includes(text));
}

async function waitForCloudflareWall(
  page: Page,
  logger?: BrowserConsoleLogger,
): Promise<void> {
  if (!(await isCloudflareWall(page))) {
    return;
  }

  console.log("Cloudflare challenge detected. Waiting up to 2 minutes for it to clear...");
  logger?.write(`[${timestamp()}] [cloudflare:detected] ${page.url()}`);

  const deadline = Date.now() + CLOUDFLARE_WAIT_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await sleep(CLOUDFLARE_POLL_INTERVAL_MS);

    if (!(await isCloudflareWall(page))) {
      console.log("Cloudflare challenge cleared. Continuing.");
      logger?.write(`[${timestamp()}] [cloudflare:cleared] ${page.url()}`);
      return;
    }
  }

  logger?.write(`[${timestamp()}] [cloudflare:timeout] ${page.url()}`);
  throw new CloudflareChallengeTimeout();
}

async function humanClick(
  page: Page,
  locator: Locator,
  options: { timeout?: number } = {},
): Promise<void> {
  const timeout = options.timeout ?? 10_000;
  await locator.waitFor({ state: "visible", timeout });
  const box = await locator.boundingBox();

  if (box) {
    const targetX = box.x + box.width * randomFloat(0.35, 0.65);
    const targetY = box.y + box.height * randomFloat(0.35, 0.65);
    const currentX = targetX + randomFloat(-180, 180);
    const currentY = targetY + randomFloat(-90, 90);

    await page.mouse.move(currentX, currentY);
    await humanPause(80, 220);
    await page.mouse.move(targetX, targetY, { steps: randomInt(8, 18) });
    await humanPause(90, 260);
    await page.mouse.down();
    await humanPause(45, 130);
    await page.mouse.up();
    return;
  }

  await locator.click();
}

async function humanType(page: Page, locator: Locator, text: string): Promise<void> {
  await locator.waitFor({ state: "visible" });
  await humanClick(page, locator);
  await humanPause(120, 300);

  for (const char of text) {
    await locator.type(char, { delay: randomInt(45, 170) });

    if (Math.random() < 0.08) {
      await humanPause(180, 520);
    }
  }
}

async function login(page: Page, logger?: BrowserConsoleLogger): Promise<void> {
  if (!UG_EMAIL || !UG_PASSWORD) {
    throw new Error("UG_EMAIL and UG_PASSWORD must be set in .env before logging in.");
  }

  await page.goto("https://www.ultimate-guitar.com/", {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });
  await waitForLoadOrPause(page);
  await waitForCloudflareWall(page, logger);
  await humanPause(700, 1600);

  await humanClick(
    page,
    page.locator("button[type='button'][tabindex='0'][data-react-aria-pressable='true']", {
      has: page.locator("span", { hasText: /^Log in$/i }),
    }),
    { timeout: 20_000 },
  );

  await humanPause(800, 1400);

  const emailInput = page.locator("input[name='username'][placeholder='Username or e-mail']");
  const passwordInput = page.locator("input[name='password'][placeholder='Password']");
  await emailInput.waitFor({ state: "visible" });
  await humanPause(250, 650);

  await humanType(page, emailInput, UG_EMAIL);
  await humanPause(250, 700);
  await humanType(page, passwordInput, UG_PASSWORD);
  await humanPause(2050, 4275);

  const emailValue = await emailInput.inputValue();
  const passwordValue = await passwordInput.inputValue();

  logger?.write(
    `[${timestamp()}] [auth:field-check] ` +
      `email=${maskEmail(emailValue)} ` +
      `email_matches_env=${emailValue === UG_EMAIL} ` +
      `password_length=${passwordValue.length} ` +
      `password_matches_env=${passwordValue === UG_PASSWORD}`,
  );

  await humanClick(
    page,
    page.locator("button[type='submit']", {
      has: page.locator("span", { hasText: /^Log in$/i }),
    }),
    { timeout: 20_000 },
  );

  await humanPause(4050, 8275);
  await page.waitForSelector("input[name='username'][placeholder='Username or e-mail']", {
    state: "detached",
  });

  console.log("Logged in.");
}

async function downloadTab(page: Page, logger?: BrowserConsoleLogger): Promise<void> {
  const outputPath = resolve(OUTPUT_FILE);
  let downloaded = false;
  let downloadError: Error | undefined;
  let resolveDownloaded: (() => void) | undefined;

  const downloadedPromise = new Promise<void>((resolvePromise) => {
    resolveDownloaded = resolvePromise;
  });

  const captureDownload = async (route: Route): Promise<void> => {
    const response = await route.fetch();
    const body = await response.body();

    if (DOWNLOAD_FILE_URL.test(route.request().url())) {
      const contentType = response.headers()["content-type"] ?? "";

      if (contentType.toLowerCase().includes("text/html") || body.subarray(0, 1).toString() === "<") {
        downloadError = new Error("Ultimate Guitar returned HTML instead of the tab data.");
      } else {
        writeFileSync(outputPath, body);
        downloaded = true;
        resolveDownloaded?.();
      }
    }

    await route.fulfill({ response, body });
  };

  await page.route("**/tab/download/file**", captureDownload);

  try {
    await page.goto(TAB_URL, { waitUntil: "domcontentloaded" });
    await waitForLoadOrPause(page);
    await waitForCloudflareWall(page, logger);

    try {
      await page.waitForLoadState("networkidle", { timeout: 20_000 });
    } catch (error) {
      if (!isPlaywrightTimeout(error)) {
        throw error;
      }
    }

    if (!downloaded) {
      const downloadControls = [
        page.getByRole("link", { name: /download/i }),
        page.getByRole("button", { name: /download/i }),
        page.locator("a[href*='/download/public/']").first(),
      ];

      for (const control of downloadControls) {
        try {
          await humanClick(page, control, { timeout: 4000 });
          await withTimeout(downloadedPromise, 15_000);
          break;
        } catch (error) {
          if (!isPlaywrightTimeout(error) && !(error instanceof TimeoutError)) {
            throw error;
          }
        }
      }
    }

    if (downloadError) {
      throw downloadError;
    }

    if (!downloaded) {
      throw new TimeoutError("Timed out waiting for the tab download request.");
    }

    console.log(`Downloaded to ${outputPath}`);
  } finally {
    await page.unroute("**/tab/download/file**", captureDownload);
  }
}

async function main(): Promise<void> {
  const consoleLogger = new BrowserConsoleLogger(`${fileTimestamp()}-logs.txt`);
  let context: BrowserContext | undefined;

  try {
    await configureFingerprintPlugin(consoleLogger);

    const launchedContext = await fingerprintPlugin.launchPersistentContext(resolve(BROWSER_PROFILE_DIR), {
      headless: false,
    });
    context = launchedContext;
    consoleLogger.attachToContext(launchedContext);

    const page = launchedContext.pages()[0] ?? (await launchedContext.newPage());
    consoleLogger.attachToPage(page);

    if (MANUAL_LOGIN) {
      await page.goto("https://www.ultimate-guitar.com/", {
        waitUntil: "domcontentloaded",
        timeout: 60_000,
      });
      await waitForLoadOrPause(page);
      await waitForCloudflareWall(page, consoleLogger);
      console.log(
        "MANUAL_LOGIN is enabled. Log in manually, then close the browser window. " +
          `Profile: ${resolve(BROWSER_PROFILE_DIR)}`,
      );

      await waitForClose(launchedContext, page);
      return;
    }

    attachAuthDiagnostics(page, consoleLogger);
    await login(page, consoleLogger);
    await downloadTab(page, consoleLogger);
  } catch (error) {
    if (error instanceof CloudflareChallengeTimeout) {
      console.log(error.message);
    } else {
      throw error;
    }
  } finally {
    await context?.close();
    consoleLogger.close();
  }
}

async function configureFingerprintPlugin(logger: BrowserConsoleLogger): Promise<void> {
  fingerprintPlugin.setServiceKey(FINGERPRINT_SERVICE_KEY);
  fingerprintPlugin.setWorkingFolder(resolve(FINGERPRINT_WORKING_FOLDER));
  fingerprintPlugin.setRequestTimeout(FINGERPRINT_REQUEST_TIMEOUT_MS);
  fingerprintPlugin.setEngineTimeout(FINGERPRINT_ENGINE_TIMEOUT_MS);

  logger.write(
    `[${timestamp()}] [fingerprint:fetch] tags=${JSON.stringify(FINGERPRINT_TAGS)}`,
  );
  const fingerprint = await fingerprintPlugin.fetch({
    tags: [...FINGERPRINT_TAGS],
  });

  fingerprintPlugin.useFingerprint(fingerprint, {
    safeElementSize: true,
  });

  if (FINGERPRINT_PROXY) {
    fingerprintPlugin.useProxy(FINGERPRINT_PROXY, {
      changeTimezone: true,
      changeGeolocation: true,
    });
    logger.write(`[${timestamp()}] [fingerprint:proxy] enabled=true`);
  }

  logger.write(`[${timestamp()}] [fingerprint:applied]`);
}

async function waitForClose(context: BrowserContext, page: Page): Promise<void> {
  await Promise.race([
    new Promise<void>((resolvePromise) => context.once("close", () => resolvePromise())),
    new Promise<void>((resolvePromise) => page.once("close", () => resolvePromise())),
  ]);
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timeout: NodeJS.Timeout | undefined;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timeout = setTimeout(() => reject(new TimeoutError()), timeoutMs);
  });

  try {
    return await Promise.race([promise, timeoutPromise]);
  } finally {
    if (timeout) {
      clearTimeout(timeout);
    }
  }
}

function randomInt(min: number, max: number): number {
  return Math.floor(randomFloat(min, max + 1));
}

function randomFloat(min: number, max: number): number {
  return Math.random() * (max - min) + min;
}

function isPlaywrightTimeout(error: unknown): boolean {
  return error instanceof Error && error.name === "TimeoutError";
}

class TimeoutError extends Error {
  constructor(message = "Timed out.") {
    super(message);
    this.name = "TimeoutError";
  }
}

void main();
