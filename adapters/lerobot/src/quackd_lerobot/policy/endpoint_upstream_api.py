"""The only file in quackd allowed to spell a Hugging Face Inference Endpoints name (ADR-0022).

`quackd policy endpoint` starts a policy server on a Hugging Face Inference Endpoint, and a run
reaches it with `--policy-url hf:NAME` (`endpoint.py`). Every constant here is tagged VERIFIED,
read on the day from the source linked, or UNVERIFIED, an assumption of ours with what quackd
does about it. The sources are the API's own OpenAPI document (version 2.0.0 on the day),
Hugging Face's pages about it, huggingface_hub at the commit its v1.29.0 tag names (the version
`uv.lock` holds, and the wheel and the tag were compared file by file for every file cited),
uv's own documentation for the image the container runs, and PyPI's for the release a container
installs. `docs/adapters/lerobot/README.md` is the human-readable version, and
`tests/test_upstream_api.py` proves UNVERIFIED names are only reachable from the files that
live with them.

**quackd has never created an Inference Endpoint.** Everything here was read, nothing was paid
for, and `quackd policy endpoint` has run against the test suite's stand-ins on 127.0.0.1 alone
(`tests/fake_hugging_face.py`). The UNVERIFIED rows are what only a first real endpoint can
settle, and each is designed for at its worst reading: quackd's own token on the server is the
lock whatever the proxy in front does.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

API_DOC = "https://api.endpoints.huggingface.cloud/openapi.json"
API_VERSION = "2.0.0"
"""The OpenAPI document's own `info.version`, on the day it was read."""
DOCS = "https://huggingface.co/docs/inference-endpoints"
HUB_REPO = "https://github.com/huggingface/huggingface_hub"
PIN = "4237d95c603db491cb1070898c74c97e4d7c2582"  # the v1.29.0 tag
HUB_VERSION = "1.29.0"
READ_ON = "2026-10-25"


def hub_src(path: str, line: int | None = None) -> str:
    return f"{HUB_REPO}/blob/{PIN}/src/huggingface_hub/{path}" + (f"#L{line}" if line else "")


def doc(page: str) -> str:
    return f"{DOCS}/{page}"


_AUTH = "utils/_auth.py"
_CONSTANTS = "constants.py"
_ENDPOINTS = "_inference_endpoints.py"
_API = "hf_api.py"

# ── the API: its routes and what it takes ───────────────────────────────────────────────

PROVIDER_LISTING = UpstreamRef(
    "GET /v2/provider",
    "VERIFIED",
    API_DOC,
    "the one route of the API's that names no security scheme (`security: []`, `[PUBLIC]` in its "
    "summary): every other route wants the account's token as `Authorization: Bearer` (the "
    "`token` scheme, `http` `bearer`). It lists each vendor, region and compute with its "
    "`pricePerHour` in USD, its `accelerator` (`cpu`, `gpu`, `neuron`), `instanceType`, "
    "`instanceSize`, `gpuMemoryGb`, `status` and a `quota`, which is zero for every compute when "
    "no account asks. quackd reads it with no token, for the accelerator of the instance asked "
    "for and the price the confirmation quotes, read on the day and never kept",
)
PROVIDER_PATH = "/v2/provider"
ENDPOINT_ROUTES = UpstreamRef(
    "POST and GET /v2/endpoint/{namespace}; GET and DELETE /v2/endpoint/{namespace}/{name}; POST "
    "/v2/endpoint/{namespace}/{name}/pause, /resume and /scale-to-zero; GET "
    "/v2/endpoint/{namespace}/{name}/logs",
    "VERIFIED",
    API_DOC,
    "create, list, get, delete, pause, resume, scale to zero and read the logs of an endpoint, "
    "each with the token. The list takes `tags` (comma separated), `limit` (20 unless told) and a "
    "`cursor`, and answers `items` and `nextCursor`; the logs take `tail`, the most lines to "
    "return. quackd lists by its own tag, `quackd-policy`, and asks for the last lines of a log "
    "only to quote them when it pauses an endpoint that failed",
)
ENDPOINT_PATH = "/v2/endpoint"
QUOTAS = UpstreamRef(
    "GET /v2/provider/quotas/{namespace}",
    "VERIFIED",
    API_DOC,
    "`vendors`, each with `quotas` of `instanceType`, `maxAccelerators` and `usedAccelerators`. "
    "quackd refuses before it creates an endpoint on an instance type the account has no room "
    "left on (QUOTA_LISTING)",
)
QUOTAS_PATH = "/v2/provider/quotas"
ENDPOINT_NAME = UpstreamRef(
    "lower-case letters, digits and '-', 32 characters at most",
    "VERIFIED",
    API_DOC,
    "the `name` of an `Endpoint`: 'Must only contains lowercase alphanumeric characters or '-' "
    "and have a length of 32 characters maximum'. It is the NAME of `--policy-url hf:NAME`, and "
    "quackd refuses any other before it asks Hugging Face anything",
)
NAME_PATTERN = r"^[a-z0-9-]{1,32}$"
ENDPOINT_TYPES = UpstreamRef(
    '"public", "authenticated", "private"',
    "VERIFIED",
    API_DOC,
    "`EndpointType`, an endpoint's visibility. huggingface_hub keeps a fourth, "
    f'`"protected"`, as deprecated for `"authenticated"` ({hub_src(_ENDPOINTS, 88)}). quackd '
    "creates `authenticated` and nothing else: `public` would put no account between anybody "
    "and the server, and what `private` admits is read two ways (PRIVATE_TYPE)",
)
TYPES = ("public", "authenticated", "private")
CREATED_TYPE = "authenticated"
AUTHENTICATED_ADMITS = UpstreamRef(
    "Authenticated: anyone with a Hugging Face account",
    "VERIFIED",
    doc("guides/configuration"),
    "the page's words for the option: 'Anyone with a Hugging Face account can access it, using "
    "their personal HF access tokens'. So the proxy in front of an `authenticated` endpoint keeps "
    "out only somebody with no account, and the lock on a policy whose answers move an arm is "
    "quackd's own token, which the server wants on every route but its health route. It goes "
    "only into the endpoint's secrets and this machine's record of the endpoint",
)
ENDPOINT_STATES = UpstreamRef(
    '"pending", "initializing", "updating", "updateFailed", "running", "paused", "failed", '
    '"scaledToZero"',
    "VERIFIED",
    API_DOC,
    "`EndpointState`, as `status.state` says it, and as huggingface_hub's "
    f"`InferenceEndpointStatus` spells it ({hub_src(_ENDPOINTS, 75)}). `quackd policy endpoint up` "
    "waits through the first two, pauses an endpoint that reaches `failed`, wakes one that "
    "`scaledToZero` with a request, and resumes one that is `paused`",
)
STATES = (
    "pending",
    "initializing",
    "updating",
    "updateFailed",
    "running",
    "paused",
    "failed",
    "scaledToZero",
)
CREATE_BODY = UpstreamRef(
    "Endpoint: name, type, provider {vendor, region}, compute {accelerator, instanceType, "
    "instanceSize, scaling {minReplica, maxReplica, scaleToZeroTimeout}}, model {repository, "
    "revision, framework, image {custom {url, port, healthRoute}}, env, secrets, command, "
    "args}, tags",
    "VERIFIED",
    API_DOC,
    "the body `POST /v2/endpoint/{namespace}` takes, `name`, `type`, `provider`, `compute` and "
    "`model` required, and the shape huggingface_hub builds for it "
    f"({hub_src(_API, 9551)}). `framework` is `custom` for a container of one's own, `command` "
    "replaces the image's entrypoint and `args` are passed to it. quackd sends one replica at "
    "most, scaled to zero when idle, the uv image, `uvx` installing this quackd's release, and "
    "`quackd policy serve` behind the proxy's TLS",
)
SECRETS_AT_REST = UpstreamRef(
    "model.secrets: secret environment variables, encrypted at rest",
    "VERIFIED",
    API_DOC,
    "where the server's token goes, as `QUACKD_POLICY_TOKEN`, which `quackd policy serve "
    "--token-env` reads, and a Hub token for a gated checkpoint as `HF_TOKEN` when one is asked "
    "for. The account's own token never goes into an endpoint",
)
SCALE_TO_ZERO = UpstreamRef(
    "compute.scaling.scaleToZeroTimeout: minutes of inactivity before scaling to zero, with "
    "minReplica 0",
    "VERIFIED",
    doc("guides/autoscaling"),
    "the API's `EndpointScaling` says the minutes, and that it needs `minReplica` 0 "
    f"({API_DOC}). The page offers a timeout from 15 minutes to 12 hours, and a request to an "
    "endpoint at zero starts a replica: a cold start. The API says 15 minutes is the default and "
    "the page says an hour, so quackd always sends one, `--idle-minutes`, 15 unless told, in "
    "that range, and never none",
)
IDLE_MINUTES = (15, 720)
BILLING = UpstreamRef(
    "billed while initializing and running, by the minute",
    "VERIFIED",
    doc("support/pricing"),
    "'A user or organization account will be charged for the compute resources used while "
    "successfully deployed Endpoints (ready to serve) are initializing and in a running state', "
    "and 'the actual cost is billed per minute'. So an endpoint paused or scaled to zero bills "
    "nothing, and one waited for bills from the moment it starts initializing. A paused endpoint "
    "does not count against the account's quota and a scaled-to-zero one does "
    f"({doc('guides/foundations')})",
)
STATUS_URL = UpstreamRef(
    "status.url: https://<id>.endpoints.huggingface.cloud",
    "VERIFIED",
    API_DOC,
    "where an endpoint is reached, `EndpointStatus.url`, documented with the example "
    "`https://68a0f3b5c6ee5c9dcc0e0a01.endpoints.huggingface.cloud`. It is what "
    "`client.ENDPOINT_AUTHORITY` takes, and the one address the account's token goes to as a "
    "bearer, once this machine's `up` has recorded it",
)
HEALTH_PROBE = UpstreamRef(
    "a readiness probe asks the container's health route every second",
    "VERIFIED",
    doc("engines/custom_container"),
    "'a readiness probe will ping an endpoint every second on its /health route', the route the "
    "`healthRoute` of a custom image names. quackd names `/v1/health`, the one route of its "
    "server's that needs no token, because nothing says the probe holds one",
)
RESUME_RUNNING = UpstreamRef(
    "POST .../resume on a running endpoint answers 400 'already running'",
    "VERIFIED",
    hub_src(_API, 10039),
    "huggingface_hub takes that answer as no error and reads the endpoint again, and so does "
    "`quackd policy endpoint up`",
)
WAIT_POLL = UpstreamRef(
    "InferenceEndpoint.wait(timeout=None, refresh_every=5)",
    "VERIFIED",
    hub_src(_ENDPOINTS, 245),
    "huggingface_hub reads an endpoint every 5 s until it is `running` with a `url`, then asks "
    "its health route there with the account's token and takes a 200 as ready (line 285). "
    "`quackd policy endpoint up` waits the same way, and the health route it asks is the "
    "server's own `/v1/health`",
)
POLL_S = 5.0

# ── the account, its token and the Hub ──────────────────────────────────────────────────

TOKEN_LOOKUP = UpstreamRef(
    "HF_TOKEN, then HUGGING_FACE_HUB_TOKEN, then the file HF_TOKEN_PATH names, "
    "$HF_HOME/token by default",
    "VERIFIED",
    hub_src(_AUTH, 145),
    "where huggingface_hub finds the account's token (`get_token`, line 58): either variable, "
    "an empty one counting as unset, then the file (line 150), whose default lives in "
    "`HF_HOME`, itself `$XDG_CACHE_HOME/huggingface` or `~/.cache/huggingface` by default "
    f"({hub_src(_CONSTANTS, 158)}, and line 247 for the file). A token is taken with its line "
    "breaks and the space around it removed (line 449). quackd reads those three and nothing "
    "more: no OIDC exchange, no Colab vault, and never `HF_ENDPOINT`, which moves where "
    "huggingface_hub sends the token (line 69 of constants.py), since no variable moves where "
    "quackd sends it",
)
TOKEN_VARIABLES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
TOKEN_PATH_VARIABLE = "HF_TOKEN_PATH"
HOME_VARIABLE = "HF_HOME"
WHOAMI = UpstreamRef(
    "GET https://huggingface.co/api/whoami-v2",
    "VERIFIED",
    hub_src(_API, 2373),
    "the account a token belongs to: its `name`, and a `type` of `user` for a person, which is the "
    f"namespace an endpoint is created in unless one is named ({hub_src(_API, 10125)}). The "
    "route is rate limited, strictly on purpose, so `up` asks it once",
)
WHOAMI_PATH = "/api/whoami-v2"
MODEL_INFO = UpstreamRef(
    "GET https://huggingface.co/api/models/{repo}/revision/{revision}",
    "VERIFIED",
    hub_src(_API, 3323),
    "a repository at a revision, with `private` true or false and `gated` false, `auto` or "
    "`manual`, read on the day for lerobot/smolvla_base (public) and google/paligemma-3b-pt-224 "
    "(manual). An endpoint fetches its checkpoint with a token of its own or none, so `up` "
    "refuses a checkpoint or a `--pin` that is gated or private unless `--hub-token-from` names "
    "a token for it",
)
RAW_FILE = UpstreamRef(
    "GET https://huggingface.co/{repo}/raw/{revision}/config.json",
    "VERIFIED",
    "https://huggingface.co/lerobot/smolvla_base/raw/d9f33c94a60fb382c90dea2164c96845bd955e28/"
    "config.json",
    "one file of a repository at a revision, as text: 200 for a public one, and 401 for a gated "
    "one asked with no access, read on the day. `up` reads the checkpoint's `config.json` this "
    "way and checks it as the server would on a GPU, before an endpoint exists to fail on it",
)

# ── what a container installs, and what it runs on ──────────────────────────────────────

PYPI_RELEASE = UpstreamRef(
    "GET https://pypi.org/pypi/{project}/{version}/json: urls[].upload_time_iso_8601",
    "VERIFIED",
    "https://docs.pypi.org/api/json/",
    "a release and every file of it with the time it was uploaded. `up` refuses a quackd that is "
    "not a release on PyPI, a build from a checkout among them, since the container installs "
    "the release by its version, and sets the container's `--exclude-newer` an hour after the "
    "last file of quackd's and quackd-lerobot's was uploaded",
)
EXCLUDE_NEWER = UpstreamRef(
    "uvx --exclude-newer: an RFC 3339 timestamp, compared with each file's upload time",
    "VERIFIED",
    "https://docs.astral.sh/uv/reference/cli/",
    "'Limit candidate packages to those that were uploaded prior to the given date', each "
    "distribution artifact by its own upload time. So every start of an endpoint, from its "
    "first to one after it scaled to zero a month later, resolves the same torch and the same "
    "transformers as the first did",
)
UV_IMAGE = UpstreamRef(
    "ghcr.io/astral-sh/uv:{major}.{minor}.{patch}-python3.12-trixie-slim",
    "VERIFIED",
    "https://docs.astral.sh/uv/guides/integration/docker/",
    "uv's image on Python 3.12's slim Debian, with uv and uvx on the path, 'published with uv "
    "version tags as ghcr.io/astral-sh/uv:{major}.{minor}.{patch}-{base}'. Debian's tags are "
    "trixie since uv 0.10.0, and no bookworm tag has been published since 0.9.30, read in the "
    "registry's own tag list. `IMAGE` is uv 0.13.5's, the newest on the day, by the digest "
    "ghcr.io's registry answered for that tag",
)
UV_VERSION = "0.13.5"
IMAGE = (
    "ghcr.io/astral-sh/uv:0.13.5-python3.12-trixie-slim"
    "@sha256:9b8477a5f2e2f96fa99eaecb7680c1c4c9f527926430f3b630bfb2c3641f8f20"
)
IMAGE_FALLBACK = (
    "docker.io/astral/uv:0.13.5-python3.12-trixie-slim"
    "@sha256:9b8477a5f2e2f96fa99eaecb7680c1c4c9f527926430f3b630bfb2c3641f8f20"
)
"""The same image, by the same digest, from Docker Hub, where Astral publishes it as `astral/uv`
too: Docker Hub's registry answered that digest for that tag on the day. Hugging Face's page on
custom containers names Docker Hub among the registries an endpoint can reach, and not ghcr.io,
so this is the one to move to on the day an endpoint will not pull from ghcr.io (GHCR_PULLS).
Only the registry differs, so the command stays as it is. It is written down here and used
nowhere until then."""

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

AUTHORIZATION_FORWARDED = UpstreamRef(
    "AUTHORIZATION_FORWARDED",
    "UNVERIFIED",
    doc("guides/security"),
    "whether the proxy passes a client's `Authorization` header, the account's token, on to the "
    "container. Nothing Hugging Face publishes says. quackd's server never reads that header, "
    "warns once in its log when one arrives (`server.PolicyServer.saw_authorization`), so the "
    "first real endpoint's log says, and wants its own token on every route whatever the "
    "proxy does",
)
GHCR_PULLS = UpstreamRef(
    "GHCR_PULLS",
    "UNVERIFIED",
    doc("engines/custom_container"),
    "that an endpoint pulls a public image from ghcr.io with no credentials, named by its tag and "
    "its digest. huggingface_hub's own example of a custom image is one on ghcr.io "
    f"({hub_src(_API, 9508)}), and the page names Docker Hub, Amazon ECR, Azure ACR and Google "
    "GCR as registries an endpoint can reach, and not ghcr.io. The API takes `credentials` for "
    "a registry that wants them. quackd sends none, and keeps `IMAGE_FALLBACK`, the same image "
    "by the same digest on Docker Hub, for the day ghcr.io will not do",
)
PROXY_WHILE_STARTING = UpstreamRef(
    "PROXY_WHILE_STARTING",
    "UNVERIFIED",
    doc("guides/autoscaling"),
    "what the proxy answers while a replica starts. The autoscaling guide says 503, and an older "
    f"page of the same name says 502 Bad Gateway ({doc('autoscaling')}). quackd's client says "
    "500, 502, 503 and 504 that are not its server's own refusals all as an endpoint that is not "
    "serving, and sends no `X-Scale-Up-Timeout`, so a request is never held for the minutes a "
    "cold start takes",
)
PRIVATE_TYPE = UpstreamRef(
    "PRIVATE_TYPE",
    "UNVERIFIED",
    doc("guides/configuration"),
    "what an endpoint of type `private` admits. The configuration page calls Private the default "
    "and says it is reached by the account and its organisation alone, each with a personal "
    "token, where an older page says a Private endpoint is reached only through a cloud's "
    f"private link inside its region and never from the internet ({doc('security')}). quackd "
    "creates `authenticated`, which the configuration page says any account may reach, and "
    "relies on its own token on the server, which no account holds. Whether `private` would "
    "keep every other account out of a laptop's reach is for a first real endpoint to try",
)
PAUSED_ANSWERS = UpstreamRef(
    "PAUSED_ANSWERS",
    "UNVERIFIED",
    doc("guides/foundations"),
    "what the proxy answers for a paused endpoint, and for a deleted one, at the address it had. "
    "Nothing says. quackd's client says any 5xx as an endpoint that is not serving and a 404 as "
    "one that is gone, and `quackd policy endpoint list` reads the state from the API",
)
LOGS_BODY = UpstreamRef(
    "LOGS_BODY",
    "UNVERIFIED",
    API_DOC,
    "what `GET .../logs?tail=N` answers. The OpenAPI document gives it no body, where the newer "
    "`/v3` logs answer a JSON list of entries each with a `line`. quackd reads the `/v2` answer "
    "as such a list when it is one and as text when it is not, bounded, scrubbed of both tokens "
    "and cut to a few lines, only to quote it when it pauses an endpoint that failed",
)
QUOTA_LISTING = UpstreamRef(
    "QUOTA_LISTING",
    "UNVERIFIED",
    API_DOC,
    "whether the quota listing names every instance type an account may use, or only those it "
    "has quota on. quackd refuses before it creates an endpoint on a type the listing names "
    "with no room left, and leaves a type it does not name to Hugging Face's own refusal",
)
ADDRESS_KEPT = UpstreamRef(
    "ADDRESS_KEPT",
    "UNVERIFIED",
    API_DOC,
    "that an endpoint keeps the `status.url` it was given for its life, through a pause, a resume "
    "and a scale to zero. The API's document calls it the URL the endpoint is reachable at and "
    "says nothing of when it changes, and its pause and resume routes say nothing of it. quackd "
    "records the address the first time Hugging Face gives one, and takes an endpoint by that "
    "name at another address for another endpoint, one made after it was deleted: `up`, "
    "`pause` and `delete` leave that one as it is, and `delete --forget` forgets the record. "
    "Were Hugging Face to move an endpoint, `up` would refuse its own the same way, which a "
    "first real pause and resume settles",
)
GPU_IN_CONTAINER = UpstreamRef(
    "GPU_IN_CONTAINER",
    "UNVERIFIED",
    doc("engines/custom_container"),
    "that torch installed from PyPI in quackd's container finds the endpoint's GPU. A CUDA build "
    "newer than the host's driver would leave torch on the CPU. `up` asks the server, once it "
    "answers, whether it has a GPU (`/v1/policy`'s `gpu`), and pauses an endpoint that has "
    "none, quoting its log",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
