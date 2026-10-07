/**
 * GENERATED FILE. Do not edit.
 *
 * The model list, copied out of `quackd/agent/providers/catalogue.py`, which is the only place
 * a model is ever added. Regenerate after editing that file:
 *
 *     python web/build_catalogue.py
 *
 * `tests/test_web.py` fails if this file and that one disagree, so the two cannot drift.
 *
 * Every cloud vendor quackd knows is here, including the ones this page cannot call: a vendor
 * whose API refuses a cross-origin preflight stays out of `PROVIDERS` in `providers.js` and is
 * named in `web/README.md` with the reason. The data stays whole either way.
 */

export const STATUS_ORDER = ["current", "legacy", "preview", "specialised", "open"];

export const CATALOGUE = {
  "anthropic": {
    "default": "claude-opus-5-5",
    "entries": [
      {
        "id": "claude-opus-5-5",
        "label": "Claude Opus 5.5",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": false
      },
      {
        "id": "claude-fable-5-1",
        "label": "Claude Fable 5.1",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": false
      },
      {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-fable-5",
        "label": "Claude Fable 5",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-opus-4-8",
        "label": "Claude Opus 4.8",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-opus-4-7",
        "label": "Claude Opus 4.7",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-opus-4-6",
        "label": "Claude Opus 4.6",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-opus-4-5",
        "label": "Claude Opus 4.5",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "claude-sonnet-4-5",
        "label": "Claude Sonnet 4.5",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "openai": {
    "default": "gpt-6-sol",
    "entries": [
      {
        "id": "gpt-6-sol",
        "label": "GPT-6 Sol",
        "status": "current",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-6-astra",
        "label": "GPT-6 Astra",
        "status": "current",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-6-luna",
        "label": "GPT-6 Luna",
        "status": "current",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.5",
        "label": "GPT-5.5",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.4",
        "label": "GPT-5.4",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.4-mini",
        "label": "GPT-5.4 mini",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.4-nano",
        "label": "GPT-5.4 nano",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.2",
        "label": "GPT-5.2",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.1",
        "label": "GPT-5.1",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-4.1",
        "label": "GPT-4.1",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-4.1-mini",
        "label": "GPT-4.1 mini",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-4o",
        "label": "GPT-4o",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-4o-mini",
        "label": "GPT-4o mini",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gpt-5.5-pro",
        "label": "GPT-5.5 Pro",
        "status": "specialised",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-5.4-pro",
        "label": "GPT-5.4 Pro",
        "status": "specialised",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-5.2-pro",
        "label": "GPT-5.2 Pro",
        "status": "specialised",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "gpt-5.3-codex",
        "label": "GPT-5.3 Codex",
        "status": "specialised",
        "vision": true,
        "api": "responses",
        "forced_tools": true
      },
      {
        "id": "chat-latest",
        "label": "ChatGPT Instant (moves with ChatGPT)",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "gemini": {
    "default": "gemini-3.8-flash",
    "entries": [
      {
        "id": "gemini-3.8-flash",
        "label": "Gemini 3.8 Flash",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash-Lite",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro (preview)",
        "status": "preview",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.1-pro-preview-customtools",
        "label": "Gemini 3.1 Pro, custom tools (preview)",
        "status": "preview",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3-flash-preview",
        "label": "Gemini 3 Flash (preview)",
        "status": "preview",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-robotics-er-2-preview",
        "label": "Gemini Robotics ER 2 (preview)",
        "status": "preview",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.6-flash",
        "label": "Gemini 3.6 Flash",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "grok": {
    "default": "grok-4.7",
    "entries": [
      {
        "id": "grok-4.7",
        "label": "Grok 4.7",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-4.6",
        "label": "Grok 4.6",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-4.5",
        "label": "Grok 4.5",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-4.3",
        "label": "Grok 4.3",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-4.20-0309-reasoning",
        "label": "Grok 4.20 (reasoning)",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-4.20-0309-non-reasoning",
        "label": "Grok 4.20 (non-reasoning)",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "grok-build-0.1",
        "label": "Grok Build 0.1",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "mistral": {
    "default": "mistral-medium-3-5",
    "entries": [
      {
        "id": "mistral-medium-3-5",
        "label": "Mistral Medium 3.5",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "mistral-large-2512",
        "label": "Mistral Large 3",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "mistral-small-2603",
        "label": "Mistral Small 4",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "ministral-14b-2512",
        "label": "Ministral 3 14B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "ministral-8b-2512",
        "label": "Ministral 3 8B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "ministral-3b-2512",
        "label": "Ministral 3 3B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "codestral-2508",
        "label": "Codestral 25.08",
        "status": "specialised",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "zai-glm-5-3",
        "label": "Z.ai GLM 5.3 (hosted by Mistral)",
        "status": "preview",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "zai-glm-5-2",
        "label": "Z.ai GLM 5.2 (hosted by Mistral)",
        "status": "preview",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "labs-leanstral-1-5",
        "label": "Leanstral 1.5 (Lean 4 proofs, Mistral labs)",
        "status": "preview",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "deepseek": {
    "default": "deepseek-flash",
    "entries": [
      {
        "id": "deepseek-flash",
        "label": "DeepSeek V4.1 Flash",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "cohere": {
    "default": "command-a-plus-05-2026",
    "entries": [
      {
        "id": "command-a-plus-05-2026",
        "label": "Command A+",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "command-a-03-2025",
        "label": "Command A",
        "status": "current",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "command-a-reasoning-08-2025",
        "label": "Command A Reasoning",
        "status": "specialised",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "north-mini-code-1-0",
        "label": "North Mini Code",
        "status": "specialised",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "command-r-plus-08-2024",
        "label": "Command R+",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "command-r-08-2024",
        "label": "Command R",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "command-r7b-12-2024",
        "label": "Command R7B",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "qwen": {
    "default": "qwen3.8-max",
    "entries": [
      {
        "id": "qwen3.8-max",
        "label": "Qwen3.8 Max",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.8-flash",
        "label": "Qwen3.8 Flash",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.7-plus",
        "label": "Qwen3.7 Plus",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.7-flash",
        "label": "Qwen3.7 Flash",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.7-max",
        "label": "Qwen3.7 Max",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.6-plus",
        "label": "Qwen3.6 Plus",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.6-flash",
        "label": "Qwen3.6 Flash",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-plus",
        "label": "Qwen3.5 Plus",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-flash",
        "label": "Qwen3.5 Flash",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen-max",
        "label": "Qwen Max",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen-plus",
        "label": "Qwen Plus",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen-flash",
        "label": "Qwen Flash",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3-coder-flash",
        "label": "Qwen3 Coder Flash",
        "status": "specialised",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.8-27b",
        "label": "Qwen3.8 27B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.8-2.4t-a95b",
        "label": "Qwen3.8 2.4T-A95B",
        "status": "open",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.6-35b-a3b",
        "label": "Qwen3.6 35B-A3B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.6-27b",
        "label": "Qwen3.6 27B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-397b-a17b",
        "label": "Qwen3.5 397B-A17B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-122b-a10b",
        "label": "Qwen3.5 122B-A10B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-27b",
        "label": "Qwen3.5 27B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "qwen3.5-35b-a3b",
        "label": "Qwen3.5 35B-A3B",
        "status": "open",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "kimi": {
    "default": "kimi-k3",
    "entries": [
      {
        "id": "kimi-k3",
        "label": "Kimi K3",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "kimi-k2.6",
        "label": "Kimi K2.6",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "kimi-k2.7-code",
        "label": "Kimi K2.7 Code",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "kimi-k2.7-code-highspeed",
        "label": "Kimi K2.7 Code (high speed)",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "glm": {
    "default": "glm-5.3",
    "entries": [
      {
        "id": "glm-5.3",
        "label": "GLM-5.3",
        "status": "current",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-5.3-flash",
        "label": "GLM-5.3 Flash",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-5.3-flashx",
        "label": "GLM-5.3 FlashX",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-5.2",
        "label": "GLM-5.2",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-5.1",
        "label": "GLM-5.1",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-5",
        "label": "GLM-5",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.7",
        "label": "GLM-4.7",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.7-flash",
        "label": "GLM-4.7 Flash",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.7-flashx",
        "label": "GLM-4.7 FlashX",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.6",
        "label": "GLM-4.6",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.5",
        "label": "GLM-4.5",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.5-x",
        "label": "GLM-4.5 X",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.5-air",
        "label": "GLM-4.5 Air",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.5-airx",
        "label": "GLM-4.5 AirX",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.5-flash",
        "label": "GLM-4.5 Flash",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4-32b-0414-128k",
        "label": "GLM-4 32B (128k)",
        "status": "legacy",
        "vision": false,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.6v",
        "label": "GLM-4.6V",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.6v-flash",
        "label": "GLM-4.6V Flash",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "glm-4.6v-flashx",
        "label": "GLM-4.6V FlashX",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "meta": {
    "default": "muse-spark-1.3",
    "entries": [
      {
        "id": "muse-spark-1.3",
        "label": "Muse Spark 1.3",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "muse-spark-1.2",
        "label": "Muse Spark 1.2",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "muse-spark-1.1",
        "label": "Muse Spark 1.1",
        "status": "legacy",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "muse-spark-1.3-contributor",
        "label": "Muse Spark 1.3 (contributor tier, Meta trains on your prompts)",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "muse-spark-1.2-contributor",
        "label": "Muse Spark 1.2 (contributor tier, Meta trains on your prompts)",
        "status": "specialised",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  },
  "openrouter": {
    "default": "openai/gpt-6-sol",
    "entries": [
      {
        "id": "openai/gpt-6-sol",
        "label": "GPT-6 Sol (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "openai/gpt-6-luna",
        "label": "GPT-6 Luna (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "anthropic/claude-sonnet-5.5",
        "label": "Claude Sonnet 5.5 (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": false
      },
      {
        "id": "anthropic/claude-opus-5.5",
        "label": "Claude Opus 5.5 (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": false
      },
      {
        "id": "google/gemini-3.8-flash",
        "label": "Gemini 3.8 Flash (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      },
      {
        "id": "x-ai/grok-4.7",
        "label": "Grok 4.7 (via OpenRouter)",
        "status": "current",
        "vision": true,
        "api": null,
        "forced_tools": true
      }
    ]
  }
};
