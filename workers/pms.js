import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const IDLE_MS = 60 * 60 * 1000;
const NAMES = [
  "DATABASE_URL",
  "MONGO_URL",
  "REDIS_URL",
  "JWT_SECRET",
  "OPENAI_API_KEY",
  "AIOSSELL_USERNAME",
  "AIOSSELL_PASSWORD",
  "AIOSSELL_PARTNER_ID",
  "AIOSSELL_CARD_KEY",
  "CHANNEL_MANAGER_URL",
  "FRONTEND_ORIGINS",
  "HOTEL_TIMEZONE",
  "WHATSAPP_ACCESS_TOKEN",
  "WHATSAPP_PHONE_NUMBER_ID",
  "WHATSAPP_VERIFY_TOKEN",
  "SERPER_API_KEY",
  "TAVILY_API_KEY",
];

function passedEnv(env) {
  const out = { PORT: String(PORT) };
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
  throw new Error("PMS container did not become ready: " + errorText(lastError), { cause: lastError });
}

export class PmsContainer extends DurableObject {
  async fetch() {
    return new Response("PMS container moved", { status: 410 });
  }
}

export class pms_PmsService extends DurableObject {
  async fetch() {
    return new Response("PMS container moved", { status: 410 });
  }
}

export class PmsService extends DurableObject {
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
    return env.PMS.getByName("leon-pms").fetch(request);
  },
  scheduled(_event, env, ctx) {
    ctx.waitUntil(env.PMS.getByName("leon-pms").fetch("https://api.inap.ai/health"));
  },
};
