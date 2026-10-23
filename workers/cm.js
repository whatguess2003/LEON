import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const IDLE_MS = 60 * 60 * 1000;
const NAMES = [
  "DATABASE_URL",
  "MONGO_URL",
  "REDIS_URL",
  "OPENAI_API_KEY",
  "AIOSSELL_USERNAME",
  "AIOSSELL_PASSWORD",
  "AIOSSELL_PARTNER_ID",
  "AIOSSELL_CARD_KEY",
  "FRONTEND_ORIGINS",
  "HOTEL_TIMEZONE",
];

function passedEnv(env) {
  const out = { CHANNEL_MANAGER_PORT: String(PORT) };
  for (const name of NAMES) {
    const value = env[name];
    if (typeof value === "string" && value !== "") out[name] = value;
  }
  return out;
}

function errorText(error) {
  return error && error.message ? error.message : String(error);
}

async function probe(port) {
  const response = await Promise.race([
    port.fetch("http://container/health", { signal: AbortSignal.timeout(3000) }),
    scheduler.wait(4000).then(() => {
      throw new Error("health probe timed out");
    }),
  ]);
  await response.body?.cancel();
  if (!response.ok) throw new Error("Health check returned " + response.status);
}

async function waitHealthy(container) {
  let lastError;
  for (let attempt = 0; attempt < 45; attempt++) {
    try {
      if (!container.running) throw new Error("container is not running");
      await probe(container.getTcpPort(PORT));
      return;
    } catch (error) {
      lastError = error;
      console.log("health probe failed", errorText(error));
      await scheduler.wait(2000);
    }
  }
  throw new Error("Channel manager container did not become ready: " + errorText(lastError), { cause: lastError });
}

export class CmContainer extends DurableObject {
  async fetch() {
    return new Response("Channel manager container moved", { status: 410 });
  }
}

export class CmService extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = undefined;
  }

  async fetch(request) {
    this.starting ??= this.startAndWait().finally(() => {
      this.starting = undefined;
    });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return this.ctx.container.getTcpPort(PORT).fetch(forwarded);
  }

  async startAndWait() {
    const container = this.ctx.container;
    if (!container.running) {
      container.start({
        enableInternet: true,
        env: passedEnv(this.env),
      });
    }
    await container.setInactivityTimeout(IDLE_MS);
    await waitHealthy(container);
  }
}

export default {
  fetch(request, env) {
    return env.CM.getByName("leon-cm").fetch(request);
  },
  scheduled(_event, env, ctx) {
    ctx.waitUntil(env.CM.getByName("leon-cm").fetch("https://cm.inap.ai/health"));
  },
};
