/**
 * Bring your own key. The page talks to the model; nothing talks to us.
 *
 * quackd hosts no server for this demo. Your key is read from an input, kept in a variable
 * for the life of the tab, and sent straight to the vendor you picked from your own
 * browser. It is never stored, never logged and never proxied, which also means every
 * request is billed to you and visible to you in your vendor's dashboard. If you would
 * rather it never leave the machine at all, run a local model: Ollama and any
 * OpenAI-compatible server (llama.cpp, vLLM, LM Studio) are here too, and need no key.
 *
 * Each provider does the same job: hand the model a system prompt, the turns so far and a
 * list of tools, and return exactly one tool call. Where a vendor can be told to call a
 * tool it is told to; where it cannot, a missing call ends the run rather than being
 * guessed at. `toolChoice` below is that difference, per vendor, and it is copied from the
 * provider classes in `quackd/agent/providers/`: Mistral spells it `any`, Cohere documents
 * no such parameter at all and is therefore asked rather than told, and the rest take
 * `auto` or `required`. A row can say more than its vendor does: a Claude row the catalogue
 * marks `forced_tools: false` is asked with `auto`, through OpenRouter as through Anthropic.
 *
 * Twelve cloud vendors are in the catalogue and eleven are offered here. Anthropic, OpenAI and
 * Gemini each have a client of their own; Grok, Mistral, DeepSeek, Cohere, Qwen, Kimi, Meta
 * and OpenRouter are all OpenAI-shaped, so they are the same client with a different base URL.
 * GLM is the one that is missing: Z.ai answers a CORS preflight with no
 * `Access-Control-Allow-*` headers, so a browser refuses the call before it is made. It stays
 * on the CLI, it stays in `catalogue.js`, and `web/README.md` says so. The model list itself is
 * generated from Python by `web/build_catalogue.py` and is never edited here.
 *
 * OpenAI is the one vendor here with two APIs that can do that job. Chat Completions is
 * asked first, and a model that refuses function tools there is moved to Responses for the
 * rest of the run, on the strength of what the 400 said. `quackd/agent/providers/openai.py`
 * makes the same move for the CLI, and the two have to keep agreeing. Where the catalogue
 * already knows a model is Responses-only, `startingApi` says so up front and the failed
 * call is never paid for.
 */

import { CATALOGUE } from "./catalogue.js";

/**
 * The vendors this page can call, which is not the same list as the CLI's.
 *
 * No `models` and no `defaultModel` on a cloud entry: both live in `catalogue.js`, generated
 * from the Python that is the single source of truth for model names. Local keeps its own,
 * because a local server serves whatever you pulled and quackd has no list for it.
 */
/**
 * Vendors quackd can drive from a terminal and this page cannot, with the reason and the day it
 * was measured.
 *
 * Written down rather than inferred from which keys are missing below, because "absent" and
 * "forgotten" look identical in a diff. A test holds this map to exactly the set of catalogue
 * vendors `PROVIDERS` leaves out, so dropping a vendor from the page without saying why fails
 * the suite, and re-adding one without deleting its excuse fails it too.
 */
export const NOT_FROM_A_BROWSER = {
  glm: "Z.ai answers a CORS preflight with no Access-Control-Allow-* header, measured 2026-09-12",
};

export const PROVIDERS = {
  anthropic: {
    label: "Anthropic (Claude)",
    keyPlaceholder: "sk-ant-...",
    keyUrl: "https://console.anthropic.com/settings/keys",
    needsKey: true,
  },
  openai: {
    label: "OpenAI",
    keyPlaceholder: "sk-...",
    keyUrl: "https://platform.openai.com/api-keys",
    baseUrl: "https://api.openai.com/v1",
    toolChoice: "required",
    needsKey: true,
  },
  gemini: {
    label: "Google (Gemini)",
    keyPlaceholder: "AIza...",
    keyUrl: "https://aistudio.google.com/apikey",
    needsKey: true,
  },
  grok: {
    label: "xAI (Grok)",
    keyPlaceholder: "xai-...",
    keyUrl: "https://console.x.ai/",
    baseUrl: "https://api.x.ai/v1",
    toolChoice: "required",
    needsKey: true,
  },
  mistral: {
    label: "Mistral",
    keyPlaceholder: "your Mistral key",
    keyUrl: "https://console.mistral.ai/api-keys",
    baseUrl: "https://api.mistral.ai/v1",
    // Mistral's guide documents `any` as its word for forcing a call; its spec lists
    // `required` as well, so this is its documented value rather than the only one.
    toolChoice: "any",
    needsKey: true,
  },
  deepseek: {
    label: "DeepSeek",
    keyPlaceholder: "sk-...",
    keyUrl: "https://platform.deepseek.com/api_keys",
    baseUrl: "https://api.deepseek.com",
    toolChoice: "required",
    // DeepSeek thinks by default, and thinking mode refuses `required` and wants every earlier
    // turn's reasoning sent back, which this page never keeps. So thinking goes off, the way
    // `quackd/agent/providers/deepseek.py` turns it off for the CLI.
    extraBody: { thinking: { type: "disabled" } },
    needsKey: true,
  },
  cohere: {
    label: "Cohere",
    keyPlaceholder: "your Cohere key",
    keyUrl: "https://dashboard.cohere.com/api-keys",
    baseUrl: "https://api.cohere.ai/compatibility/v1",
    // The one vendor here that cannot be told to call a tool, only asked: its compatibility
    // layer documents no `tool_choice` at all, so the field is left out of the body entirely.
    toolChoice: null,
    needsKey: true,
  },
  qwen: {
    label: "Alibaba (Qwen)",
    keyPlaceholder: "sk-...",
    keyUrl: "https://modelstudio.console.alibabacloud.com/",
    baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    toolChoice: "auto",
    needsKey: true,
  },
  kimi: {
    label: "Moonshot (Kimi)",
    keyPlaceholder: "sk-...",
    // the API host is still api.moonshot.ai, but the console moved to platform.kimi.ai
    keyUrl: "https://platform.kimi.ai/console/api-keys",
    baseUrl: "https://api.moonshot.ai/v1",
    toolChoice: "auto",
    needsKey: true,
  },
  meta: {
    label: "Meta (Muse Spark)",
    keyPlaceholder: "your Meta Model API key",
    baseUrl: "https://api.meta.ai/v1",
    toolChoice: "auto",
    needsKey: true,
    note:
      "The Meta Model API, which replaced the hosted Llama API in July 2026. The key is the " +
      "one Meta's own examples export as MODEL_API_KEY. Two Muse Spark models are " +
      "contributor tier: cheaper, and Meta trains on what you send them.",
  },
  openrouter: {
    label: "OpenRouter",
    keyPlaceholder: "sk-or-v1-...",
    keyUrl: "https://openrouter.ai/settings/keys",
    baseUrl: "https://openrouter.ai/api/v1",
    // `quackd/agent/providers/openrouter.py`, in JavaScript: one call insisted on except on the
    // Claude rows that refuse a forced one, OpenRouter told to route only to an endpoint that
    // honours every parameter sent, quackd named in the two headers OpenRouter credits an app
    // by, no move to Responses, and a model's `reasoning_details` handed back on the turn that
    // made the call. The dropdown holds the six rows the catalogue carries and no others.
    toolChoice: "required",
    extraBody: { provider: { require_parameters: true } },
    headers: { "HTTP-Referer": "https://github.com/rokbenko/quackd", "X-OpenRouter-Title": "quackd" },
    responses: false,
    replaysReasoning: true,
    relaysUpstreamErrors: true,
    needsKey: true,
    note:
      "A router: what you send reaches OpenRouter and the provider it picks for the model. " +
      "No OpenRouter model has answered a real quackd request, from this page or the CLI.",
  },
  local: {
    label: "Local (Ollama or any OpenAI-compatible server)",
    keyPlaceholder: "not needed",
    defaultModel: "qwen3:8b",
    baseUrl: "http://localhost:11434/v1",
    toolChoice: "required",
    needsKey: false,
    note:
      "Ollama must be told to accept this page: run it with OLLAMA_ORIGINS=* (or your own " +
      "origin). Any OpenAI-compatible server works the same way; change the base URL.",
  },
};

class ProviderError extends Error {}

function oneCall(name, args) {
  return { name, arguments: args ?? {} };
}

async function readError(response, relaysUpstreamErrors = false) {
  let detail = "";
  try {
    const body = await response.json();
    detail = body?.error?.message ?? JSON.stringify(body).slice(0, 300);
    // OpenRouter relays an upstream failure as "Provider returned error" and keeps the
    // provider's own words beside it, which is usually where the reason is. Read only where a
    // vendor is known to do that, so no other vendor's message changes.
    const raw = relaysUpstreamErrors ? body?.error?.metadata?.raw : null;
    if (raw) detail += `: ${String(raw).slice(0, 300)}`;
  } catch {
    detail = (await response.text().catch(() => "")).slice(0, 300);
  }
  return detail;
}

/** Arguments arrive as a JSON string. A model that writes a broken one gets an empty object,
 *  which `checkParams` then refuses by name rather than the tab dying on a SyntaxError. */
function parseArguments(raw) {
  try {
    return JSON.parse(raw || "{}");
  } catch {
    return {};
  }
}

/**
 * Is this the 400 that refuses function tools on Chat Completions and names Responses?
 *
 * Matched on what the API says rather than on a model name, because the list of models that
 * behave this way is not ours to keep and gets longer. Observed on `gpt-6-astra`:
 *
 *     Function tools with reasoning_effort are not supported for gpt-6-astra in
 *     /v1/chat/completions. To use function tools, use /v1/responses or set
 *     reasoning_effort to 'none'.
 *
 * Both halves are required so an unrelated 400 that happens to name one of the two does not
 * move a run onto a different API. The other remedy that message offers is a dead end on the
 * model that produced it: `reasoning_effort: "none"` comes back as unsupported for that model,
 * and the tools are refused at every effort it does support. So the fix is the API.
 *
 * This is `_wants_the_responses_api` in `quackd/agent/providers/openai.py`, in JavaScript.
 */
export function wantsTheResponsesApi(status, detail) {
  if (status !== 400) return false;
  const text = String(detail).toLowerCase();
  return text.includes("function tools") && text.includes("responses");
}

/**
 * Is this the 400 a Claude model answers a forced tool call with?
 *
 * Claude Opus 5.5 and Claude Fable 5.1 refuse `tool_choice` `any`, in a sentence that opens
 * with the parameter and says the type is not supported. Both halves are matched, because a
 * 400 about a tool the request never declared also opens with `tool_choice`, and asking again
 * with `auto` would not fix that one. This is `_refuses_forced_tools` in
 * `quackd/agent/providers/anthropic.py`, in JavaScript.
 */
export function refusesForcedTools(status, detail) {
  if (status !== 400) return false;
  const text = String(detail);
  return /^\s*tool_choice\b/.test(text) && text.includes("not supported");
}

/** Anthropic: one tool call per turn, forced where the model accepts that and allowed where
 *  it does not. The page sends no `thinking` parameter, which on Claude Opus 5 and later means
 *  the model's own default, adaptive, rather than none. */
function anthropic({ key, model }) {
  // Held across steps like the OpenAI client's `api`. The catalogue marks the models known to
  // refuse a forced call so they never pay a failed request to say so; a model it does not
  // mark is asked once, and the 400 moves it to `auto` for the rest of the run.
  let forced = CATALOGUE.anthropic?.entries.find((entry) => entry.id === model)?.forced_tools !== false;
  return {
    name: "anthropic",
    model,
    async step({ system, history, observation, tools, signal = null }) {
      const messages = [];
      for (const turn of history) {
        messages.push({ role: "user", content: turn.observation });
        messages.push({
          role: "assistant",
          content: [{ type: "tool_use", id: `t${messages.length}`, name: turn.call.name, input: turn.call.arguments ?? {} }],
        });
        messages.push({
          role: "user",
          content: [{ type: "tool_result", tool_use_id: `t${messages.length - 1}`, content: "ok" }],
        });
      }
      messages.push({ role: "user", content: observation });
      // At most two passes: the only `continue` moves a forced call to `auto`, and the guard
      // that reaches it cannot fire once `forced` is false, so a model that refuses `auto` as
      // well is thrown rather than asked forever.
      for (;;) {
        const response = await fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          signal,   // an aborted run must not keep a request alive, or keep billing for it
          headers: {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            // Anthropic blocks browser calls unless the caller says it meant it. It did.
            "anthropic-dangerous-direct-browser-access": "true",
          },
          body: JSON.stringify({
            model,
            max_tokens: 1024,
            system,
            messages,
            tools: tools.map((t) => ({ name: t.name, description: t.description, input_schema: t.input_schema })),
            tool_choice: { type: forced ? "any" : "auto", disable_parallel_tool_use: true },
          }),
        });
        if (response.ok) {
          const body = await response.json();
          const call = (body.content ?? []).find((block) => block.type === "tool_use");
          if (!call) throw new ProviderError("Claude answered without calling a tool");
          return oneCall(call.name, call.input);
        }
        // Read once. A Response body cannot be consumed twice, and both uses below need it.
        const detail = await readError(response);
        if (forced && refusesForcedTools(response.status, detail)) {
          forced = false;
          continue;
        }
        throw new ProviderError(`Anthropic said ${response.status}: ${detail}`);
      }
    },
  };
}

/** OpenAI and every OpenAI-compatible server, including Ollama's.
 *
 * Two APIs behind one interface. Chat Completions is the default, and the only one a local
 * server speaks. A model that refuses function tools there names `/v1/responses` in the 400,
 * and every verb here is a function tool, so that refusal is not a degraded path, it is no
 * path: the run moves to Responses and stays. Staying is the point. Retrying chat each turn
 * would pay a failed call per step against the visitor's own key.
 */
function openaiCompatible({
  key, model, baseUrl, label, toolChoice = "required", api: startApi, extraBody = null,
  provider = null, headers: ownHeaders = null, responses = true, replaysReasoning = false,
  relaysUpstreamErrors = false,
}) {
  const root = baseUrl.replace(/\/$/, "");
  // "chat" or "responses". Held across steps, so a model that has refused chat once is never
  // asked again for the life of this provider, which is the life of the run. It starts at
  // whatever the caller knew — see `startingApi` — and defaults to chat, which is the only
  // API a local server speaks and the one every other vendor here answers on.
  let api = startApi === "responses" ? "responses" : "chat";

  // A catalogue row can refuse what its vendor's entry asks: the Claude rows marked
  // `forced_tools: false` are asked with `auto`, through OpenRouter as through Anthropic.
  const refusesForced =
    CATALOGUE[provider]?.entries.find((entry) => entry.id === model)?.forced_tools === false;
  const asked = refusesForced && toolChoice !== null ? "auto" : toolChoice;

  // `null` means the vendor documents no `tool_choice` at all, and an unknown field is a 400
  // on some gateways, so the key is left out of the body rather than sent as null.
  // A vendor's own extra fields go underneath what quackd sends, so none of them can replace
  // the model, the turns or the tools.
  const insist = (body) => {
    const merged = extraBody ? { ...extraBody, ...body } : body;
    return asked === null ? merged : { ...merged, tool_choice: asked };
  };

  // A replayed call and its result have to quote the same handle. It is invented here rather
  // than echoed from the vendor, and both renderers key it off the turn index, so the pair
  // cannot drift apart.
  const callId = (index) => `t${index}`;

  function chatBody({ system, history, observation, tools }) {
    const messages = [{ role: "system", content: system }];
    history.forEach((turn, index) => {
      messages.push({ role: "user", content: turn.observation });
      const assistant = {
        role: "assistant",
        content: null,
        tool_calls: [{
          id: callId(index),
          type: "function",
          function: { name: turn.call.name, arguments: JSON.stringify(turn.call.arguments ?? {}) },
        }],
      };
      // Back on the turn that made the call, exactly as it came: OpenRouter asks for a model's
      // reasoning unmodified, and Gemini's thought signatures ride in it.
      if (turn.call.replay) assistant.reasoning_details = turn.call.replay;
      messages.push(assistant);
      messages.push({ role: "tool", tool_call_id: callId(index), content: "ok" });
    });
    messages.push({ role: "user", content: observation });
    return insist({
      model,
      messages,
      tools: tools.map((t) => ({
        type: "function",
        function: { name: t.name, description: t.description, parameters: t.input_schema },
      })),
    });
  }

  /** The same turn in the shapes Responses uses. It agrees with Chat Completions on almost no
   *  field name: tools go flat, the system prompt becomes `instructions`, and a turn is three
   *  items keyed by `call_id` rather than one message carrying a `tool_calls` array. */
  function responsesBody({ system, history, observation, tools }) {
    const input = [];
    history.forEach((turn, index) => {
      input.push({ role: "user", content: [{ type: "input_text", text: turn.observation }] });
      input.push({
        type: "function_call",
        call_id: callId(index),
        name: turn.call.name,
        arguments: JSON.stringify(turn.call.arguments ?? {}),
      });
      input.push({ type: "function_call_output", call_id: callId(index), output: "ok" });
    });
    input.push({ role: "user", content: [{ type: "input_text", text: observation }] });
    return insist({
      model,
      instructions: system,
      input,
      tools: tools.map((t) => ({
        type: "function",
        name: t.name,
        description: t.description,
        parameters: t.input_schema,
      })),
    });
  }

  function fromChat(body) {
    // OpenRouter can answer 200 with an error and no choices at all, which is not a model that
    // declined to call a tool, and must not read as one. Only where the vendor is known to.
    if (relaysUpstreamErrors && body.error) {
      const detail = body.error.metadata?.raw ? `: ${String(body.error.metadata.raw).slice(0, 300)}` : "";
      throw new ProviderError(`${label} said ${body.error.code ?? "error"}: ${body.error.message ?? ""}${detail}`);
    }
    const message = body.choices?.[0]?.message;
    const call = message?.tool_calls?.[0];
    if (!call) throw new ProviderError(`${label} answered without calling a tool`);
    const made = oneCall(call.function.name, parseArguments(call.function.arguments));
    // Kept only where a vendor asks for it back, so every other vendor's call is what it was.
    const details = message.reasoning_details;
    return replaysReasoning && Array.isArray(details) && details.length ? { ...made, replay: details } : made;
  }

  function fromResponses(body) {
    // `output` is a flat list of items rather than one message: reasoning, then any calls.
    const call = (body.output ?? []).find((item) => item.type === "function_call");
    if (!call) throw new ProviderError(`${label} answered without calling a tool`);
    return oneCall(call.name, parseArguments(call.arguments));
  }

  return {
    name: label,
    model,
    async step({ system, history, observation, tools, signal = null }) {
      const turn = { system, history, observation, tools };
      const headers = { "content-type": "application/json", ...(ownHeaders ?? {}) };
      if (key) headers.authorization = `Bearer ${key}`;
      // At most two passes: the only `continue` moves chat to responses, and the guard that
      // reaches it cannot fire from responses, so a genuine Responses failure is thrown
      // rather than retried forever.
      for (;;) {
        const onResponses = api === "responses";
        const response = await fetch(`${root}/${onResponses ? "responses" : "chat/completions"}`, {
          method: "POST",
          signal,   // an aborted run must not keep a request alive, or keep billing for it
          headers,
          body: JSON.stringify(onResponses ? responsesBody(turn) : chatBody(turn)),
        });
        if (response.ok) {
          const body = await response.json();
          return onResponses ? fromResponses(body) : fromChat(body);
        }
        // Read once. A Response body cannot be consumed twice, and both uses below need it.
        const detail = await readError(response, relaysUpstreamErrors);
        if (responses && !onResponses && wantsTheResponsesApi(response.status, detail)) {
          api = "responses";
          continue;
        }
        throw new ProviderError(`${label} said ${response.status}: ${detail}`);
      }
    },
  };
}

/** Gemini, whose tool schema is JSON Schema without the parts it does not accept. */
function gemini({ key, model }) {
  const clean = (schema) => {
    const copy = { ...schema };
    delete copy.additionalProperties;
    if (copy.properties) {
      copy.properties = Object.fromEntries(
        Object.entries(copy.properties).map(([k, v]) => {
          const p = { ...v };
          delete p.minimum; delete p.maximum; delete p.maxLength;
          return [k, p];
        }));
    }
    return copy;
  };
  return {
    name: "gemini",
    model,
    async step({ system, history, observation, tools, signal = null }) {
      const contents = [];
      for (const turn of history) {
        contents.push({ role: "user", parts: [{ text: turn.observation }] });
        contents.push({ role: "model", parts: [{ functionCall: { name: turn.call.name, args: turn.call.arguments ?? {} } }] });
        contents.push({ role: "user", parts: [{ functionResponse: { name: turn.call.name, response: { result: "ok" } } }] });
      }
      contents.push({ role: "user", parts: [{ text: observation }] });
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;
      const response = await fetch(url, {
        method: "POST",
        signal,   // an aborted run must not keep a request alive, or keep billing for it
        headers: { "content-type": "application/json", "x-goog-api-key": key },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: system }] },
          contents,
          tools: [{ functionDeclarations: tools.map((t) => ({ name: t.name, description: t.description, parameters: clean(t.input_schema) })) }],
          toolConfig: { functionCallingConfig: { mode: "ANY" } },
        }),
      });
      if (!response.ok) throw new ProviderError(`Gemini said ${response.status}: ${await readError(response)}`);
      const body = await response.json();
      const parts = body.candidates?.[0]?.content?.parts ?? [];
      const call = parts.find((p) => p.functionCall)?.functionCall;
      if (!call) throw new ProviderError("Gemini answered without calling a tool");
      return oneCall(call.name, call.args);
    },
  };
}

/**
 * Which API a run should open on, for a model the catalogue already knows.
 *
 * Some OpenAI models will not take function tools on Chat Completions at all. The 400 they
 * answer with is read below and the run moves, but it pays a call to learn what the catalogue
 * already records, so the page asks this first and skips it. Everything else starts on Chat
 * Completions, including any id the catalogue has never heard of: this is a hint, not a
 * gate, and a model the visitor typed or a vendor shipped this morning must still reach the
 * vendor and get the vendor's own answer.
 */
export function startingApi(provider, model) {
  const known = CATALOGUE[provider]?.entries.find((entry) => entry.id === model);
  return known?.api === "responses" ? "responses" : "chat";
}

export function makeProvider({ provider, key, model, baseUrl, api }) {
  const spec = PROVIDERS[provider];
  if (!spec) throw new ProviderError(`unknown provider ${provider}`);
  if (spec.needsKey && !key) throw new ProviderError(`${spec.label} needs your API key`);
  // The default comes from the catalogue for a cloud vendor and from the spec for Local. The
  // id itself is NOT checked against the catalogue: a model quackd has not heard of has to
  // reach the vendor and come back in the vendor's own words, which is the only answer that
  // can be right about a list this page does not own.
  const chosen = model || CATALOGUE[provider]?.default || spec.defaultModel;
  // Where the caller said nothing, the catalogue decides, so a caller who reaches for
  // `makeProvider` directly gets the same answer the page does. `quackd/agent/providers/
  // openai.py` resolves it in the same order for the same reason.
  const startsOn = api || startingApi(provider, chosen);
  if (provider === "anthropic") return anthropic({ key, model: chosen });
  if (provider === "gemini") return gemini({ key, model: chosen });
  // Every other cloud vendor is OpenAI-shaped, so it is one client with a different base URL,
  // a different label on its errors and whatever `tool_choice` that vendor accepts. Local is
  // the same client again, with the only base URL on the page a visitor may type.
  if (spec.needsKey) {
    return openaiCompatible({
      key,
      model: chosen,
      baseUrl: spec.baseUrl,
      label: spec.label,
      toolChoice: spec.toolChoice,
      api: startsOn,
      extraBody: spec.extraBody ?? null,
      provider,
      headers: spec.headers ?? null,
      responses: spec.responses !== false,
      replaysReasoning: spec.replaysReasoning === true,
      relaysUpstreamErrors: spec.relaysUpstreamErrors === true,
    });
  }
  return openaiCompatible({
    key: key || "",
    model: chosen,
    baseUrl: localBaseUrl(baseUrl || spec.baseUrl),
    label: "your local server",
    toolChoice: spec.toolChoice,
  });
}

/**
 * A base URL a key may safely be sent to.
 *
 * This box is free text, and whatever is in the key field goes to it as a bearer token. https
 * is fine anywhere; plain http only to this machine, where nothing leaves it. Anything else is
 * refused by name, so a typo or a paste cannot quietly forward a key to a stranger. The same
 * rule is what a `connect-src` policy could express if the host were knowable in advance.
 */
export function localBaseUrl(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new ProviderError(`"${raw}" is not a URL. Try http://localhost:11434/v1`);
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(url.hostname);
  if (url.protocol === "https:" || (url.protocol === "http:" && loopback)) {
    return raw;
  }
  throw new ProviderError(
    `refusing to send a key to ${url.host} over ${url.protocol.replace(":", "")}. ` +
      "Use https, or http only for a server on this machine."
  );
}
