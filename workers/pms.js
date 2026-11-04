import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const IDLE_MS = 60 * 60 * 1000;
const NAMES = [
  "DATABASE_URL",
  "MONGO_URL",
  "REDIS_URL",
  "JWT_SECRET",
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "AIOSSELL_USERNAME",
  "AIOSSELL_PASSWORD",
  "AIOSSELL_PARTNER_ID",
  "AIOSSELL_CARD_KEY",
  "CHANNEL_MANAGER_URL",
  "CM_SERVICE_SECRET",
  "FRONTEND_ORIGINS",
  "HOTEL_TIMEZONE",
  "WHATSAPP_ACCESS_TOKEN",
  "WHATSAPP_PHONE_NUMBER_ID",
  "WHATSAPP_PHONE_NUMBER",
  "WHATSAPP_VERIFY_TOKEN",
  "WHATSAPP_TEMPLATE_NAME",
  "WHATSAPP_TEMPLATE_LANGUAGE",
  "WHATSAPP_APP_SECRET",
  "SERPER_API_KEY",
  "TAVILY_API_KEY",
];

function databaseUrl(value, env) {
  const host = env.DATABASE_ORIGIN_HOST;
  const port = env.DATABASE_ORIGIN_PORT;
  const parsed = parsedDatabase(value && value.connectionString);
  const user = parsed.user || (typeof env.DATABASE_ORIGIN_USER === "string" ? env.DATABASE_ORIGIN_USER : value && value.user);
  const password = parsed.password || (value && value.password);
  const database = parsed.database || (value && value.database);
  const parts = [host, port, user, password, database];
  const ready = parts.every((part) => typeof part === "string" && part !== "");
  if (!ready) return "";
  console.log("database url", "userHasBranch", user.includes("."), "passwordLength", password.length, "fromConnectionString", Boolean(parsed.user));
  return [
    "postgres://",
    encodeURIComponent(user),
    ":",
    encodeURIComponent(password),
    "@",
    host,
    ":",
    port,
    "/",
    encodeURIComponent(database),
    "?sslmode=require",
  ].join("");
}

function parsedDatabase(connectionString) {
  if (typeof connectionString !== "string" || connectionString === "") return {};
  const parsed = new URL(connectionString);
  return {
    user: decodeURIComponent(parsed.username),
    password: decodeURIComponent(parsed.password),
    database: decodeURIComponent(parsed.pathname.replace(/^\//, "")),
  };
}

function passedEnv(env) {
  const out = { PORT: String(PORT) };
  for (const name of NAMES) {
    const value = env[name];
    if (typeof value === "string" && value !== "") out[name] = value;
    const url = databaseUrl(value, env);
    if (url) out[name] = url;
  }
  return out;
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
    const headers = new Headers(request.headers);
    const cookie = request.headers.get("cookie");
    if (cookie) headers.set("cookie", cookie);
    const init = { method: request.method, headers, redirect: "manual" };
    if (request.method !== "GET" && request.method !== "HEAD") init.body = request.body;
    const upstream = await this.ctx.container.getTcpPort(PORT).fetch(new Request(url, init));
    const responseHeaders = new Headers(upstream.headers);
    const setCookies = typeof upstream.headers.getSetCookie === "function" ? upstream.headers.getSetCookie() : [];
    for (const setCookie of setCookies) responseHeaders.append("set-cookie", setCookie);
    responseHeaders.delete("content-length");
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("transfer-encoding");
    const body = await upstream.arrayBuffer();
    return new Response(body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
  }

  async startAndWait() {
    const container = this.ctx.container;
    const stamp = "database-connstring";
    const booted = await this.ctx.storage.get("containerEnv");
    if (booted !== stamp && container.running) {
      await container.destroy("reload container env");
    }
    if (!container.running) {
      const env = passedEnv(this.env);
      console.log("starting container", Object.keys(env).join(","));
      container.start({
        enableInternet: true,
        env,
      });
      await this.ctx.storage.put("containerEnv", stamp);
    }
    await container.setInactivityTimeout(IDLE_MS);
  }
}

export default {
  fetch(request, env) {
    return env.PMS.getByName("leon-pms").fetch(request);
  },
};
