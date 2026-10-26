# ADR-0061: A Hugging Face Endpoint is a rented GPU quackd starts for you

**Status:** accepted · **Date:** 2026-10-25 · Amends [ADR-0048](0048-policies-are-the-arms-executor.md) (quackd may start the policy server, still in a process of its own, and `hf:NAME` is still typed on the flag and named nowhere else) · Implemented in `adapters/lerobot/src/quackd_lerobot/policy/endpoint.py`, `policy/endpoint_upstream_api.py`, the `/v1/health` route and `--token-env` in `policy/server.py`, the bearer in `policy/client.py`, `quackd policy endpoint` in `quackd/cli.py` and `hf:NAME` in `quackd/adapters/base.py` ([page](../guides/policies.md#on-a-hugging-face-endpoint), [the endpoint's table](../adapters/lerobot/README.md#the-endpoints-upstream-hugging-face-inference-endpoints))

## Context

SmolVLA and pi05 want a GPU, and the one way quackd offered to give them one was a machine of
your own reached through `ssh -L` ([ADR-0048](0048-policies-are-the-arms-executor.md)): rent it,
install Python 3.12 and the server's extra, start `quackd policy serve`, open a tunnel, and copy
the token across. On 2026-10-25 Rok asked for one vendor that serves many vision-language-action
policies, the way `--llm openrouter` reaches many models. Nobody hosts VLAs as a service: Hugging
Face's Inference Providers serve no robotics model, NVIDIA hosts no GR00T API, Physical
Intelligence's API is behind a waitlist, and the Gemini Robotics VLA is partner-only. What plays
that role is LeRobot's checkpoint format on the Hub, with Hugging Face's Inference Endpoints, a
GPU by the hour behind Hugging Face's own TLS and token, as the place to run it.

An Inference Endpoint runs a container of one's own choosing (`framework: custom`), from a public
image, with a command, arguments, environment variables and secrets, behind a proxy that wants
the account's token as `Authorization: Bearer`, and it scales to zero after a given idle time
and wakes on a request. Its API answers with an `https://<id>.endpoints.huggingface.cloud`
address once the endpoint is up. Everything here was read on the day from the API's own OpenAPI
document, Hugging Face's pages and huggingface_hub 1.29.0, and recorded with what is not written
down anywhere as UNVERIFIED (`endpoint_upstream_api.py`). Rok chose stubs only: no endpoint has
been created and nothing paid for.

## Decision

1. **`quackd policy endpoint up NAME --policy REPO@REVISION` starts the server for you, on a
   GPU rented from Hugging Face.** It runs the same `quackd policy serve`, in a process of its
   own on a machine of Hugging Face's, which is the part of ADR-0048 this amends: quackd may
   now start the server, never in the arm's process and never from `quackd run`. `list`, `pause`
   and `delete` complete it, by Hugging Face's own word for the state that bills nothing, pause.

2. **The container installs a release, not an image of quackd's.** It is uv's own image on
   Python 3.12, pinned by its digest, running `uvx` with this quackd's release of the server's
   extra from PyPI and an `--exclude-newer` an hour after that release was uploaded, so every
   start of the endpoint, from zero a month later as on its first, resolves the same torch. A
   quackd that is not a release on PyPI, a build from a checkout above all, is refused.

3. **What is created is fixed by quackd.** `authenticated` and nothing else, one GPU, one replica
   at most, since a session lives in one process, scaled to zero after `--idle-minutes`, 15 to
   720 and never none, the server's own health route, `GET /v1/health`, which needs no token
   because nothing says a readiness probe holds one, and quackd's tag. The instance, the vendor
   and the region are flags, AWS's `nvidia-l4` in `us-east-1` unless told, Rok's choice.

4. **The server's own token is the lock.** Hugging Face's configuration page says an
   `authenticated` endpoint admits anyone with a Hugging Face account, and nothing says whether
   its proxy passes the bearer on to the container. So quackd makes a token for the server, sends
   it only as the endpoint's secret, which the server reads with `--token-env`, and keeps it in
   this machine's record, `endpoints.json` beside the registry, written as the registry writes
   its tokens. The server says once in its log when a request arrives carrying `Authorization`,
   which makes the first real endpoint the measurement.

5. **The account's token goes where it belongs and nowhere else.** To huggingface.co and the API,
   for the account's business, never to PyPI or the price list, and as the bearer only to the one
   address this machine's `up` recorded, whose whole authority has to be an endpoint's, over
   https on port 443. A suffix is no lock, since anyone can start an endpoint under that domain,
   and no variable moves an address. Nor is the record a lock: it outlives an endpoint deleted on
   huggingface.co, and a project's `.env` could name a registry with one of its own in it. So a
   run reads a record only from a registry the shell or `--registry-dir` names, never one a
   `.env` named, reads the endpoint from the API with the account's token first, and sends the
   bearer only when the endpoint by that name carries the tags `up` gave it, one of them made
   from the record's own token, at the recorded address, and `up`, `pause` and `delete` leave
   alone one that does not. A gated checkpoint is fetched with a Hub token `--hub-token-from`
   names, which may not be the account's own.

6. **`up` refuses before anything bills, and asks before it does.** It checks the flags as the
   server would, that this quackd is a release, that the instance is a GPU on offer and its price
   on the day, the quota, that the checkpoint and its pins exist and are not gated or private
   without a Hub token, and that the checkpoint's config is one the server serves. Then it asks,
   with the price, and with no terminal it needs `--yes`. It waits for the endpoint to serve, and
   pauses one that fails, does not serve in time, or whose server has no GPU, quoting its log.

7. **`hf:NAME` is still typed on the flag.** `--policy-url hf:NAME` on `run`, `preflight`,
   `serve-mcp` and `policy check` names the endpoint, the record says where it is and holds its
   token, and nothing else names a server, a variable least of all. `QUACKD_POLICY_TOKEN` and
   `~/.quackd/policy.token`, which are other servers', are never read for one. A run's record keeps
   the server's address and adds the `endpoint` it was reached by.

## Consequences

- **quackd has never created a Hugging Face Endpoint.** Every part of this has run against the
  test suite's stand-ins for the API, the proxy and the container on 127.0.0.1, the container
  serving `scripted:sweep` in place of the checkpoint. A first real endpoint, an ACT on a T4 for
  a few cents, settles what the stand-ins cannot: whether the proxy passes `Authorization` on,
  whether an endpoint pulls the image from ghcr.io, what the proxy answers while a replica starts
  and when it is paused, and whether torch finds the GPU. PLAN.md carries it.
- A cold start takes minutes, since the container installs torch and fetches the checkpoint as
  it starts, and a run that reaches an endpoint at zero is told it is not serving rather than
  held: start it with `up` first.
- A laptop closed while `up` waits leaves the endpoint starting and billing until it idles to
  zero, `--idle-minutes` later. Ctrl-C says so, and `pause` stops the bill at once.
- The endpoint mounts the checkpoint at `/repository`, and the server never reads it: the
  pipeline fetches what it loads through its own checks, so the download happens twice.
- [ADR-0048](0048-policies-are-the-arms-executor.md) carries a note pointing here.

## Why not

**huggingface_hub as a dependency.** It has `create_inference_endpoint`, and the arm's process
would gain it, httpx and its configuration, `HF_ENDPOINT` among it, which moves where the token
goes. The routes quackd needs are a dozen or so, and the standard library reaches them with
every bound the policy client already holds.

**A quackd image on ghcr.io.** One more artifact to build, sign and keep current for every
release, on a registry Hugging Face's custom container page does not name. A release on PyPI,
installed by `uvx` into uv's own image pinned by digest, is what a person installs anyway.

**Hugging Face's `handler.py` toolkit.** It runs a repository's own Python in the inference
container, which is code the checkpoint brings with it, and quackd runs none. The server reads
before it builds, and serves its own protocol, which the arm's client already checks.

**Inference Providers.** Hugging Face's serverless inference serves no robotics model: the Hub's
listing of robotics models served by any provider was empty on the day, read again as this was
written.

**A public endpoint, or more than one replica.** Public would put no account between anybody and
a server whose answers move an arm. A second replica would send a session's steps to a process
that never saw its reset, since a session lives in one process.
