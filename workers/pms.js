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

function imageKey(image) {
  if (typeof image === "string") return image;
  if (image && typeof image.digest === "string") return image.digest;
  if (image && typeof image.tag === "string") return image.tag;
  try {
    return JSON.stringify(image);
  } catch (error) {
    return "current";
  }
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
  const port = container.getTcpPort(PORT);
  let lastError;
  for (let attempt = 0; attempt < 15; attempt++) {
    try {
      await probe(port);
      return;
    } catch (error) {
      lastError = error;
      console.log("health probe failed", error && error.message ? error.message : String(error));
      await scheduler.wait(1000);
    }
  }
  const detail = lastError && lastError.message ? lastError.message : String(lastError);
  throw new Error("PMS container did not become ready: " + detail, { cause: lastError });
}

export class PmsContainer extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = undefined;
    const container = ctx.container;
    if (container && container.running) {
      void ctx.blockConcurrencyWhile(() => container.setInactivityTimeout(IDLE_MS));
    }
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

  startContainer(container) {
    container.start({
      image: container.images.base,
      instance: "standard-1",
      enableInternet: true,
      env: passedEnv(this.env),
    });
  }

  async startAndWait() {
    const container = this.ctx.container;
    const key = imageKey(container.images.base);
    const previous = await this.ctx.storage.get("image");
    if (container.running && previous !== key) {
      console.log("stopping container for image change");
      await container.destroy();
    }
    if (!container.running) {
      this.startContainer(container);
      await this.ctx.storage.put("image", key);
    }
    await container.setInactivityTimeout(IDLE_MS);
    try {
      await waitHealthy(container);
    } catch (error) {
      console.log("restarting container", error && error.message ? error.message : String(error));
      await container.destroy();
      this.startContainer(container);
      await this.ctx.storage.put("image", key);
      await waitHealthy(container);
    }
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
