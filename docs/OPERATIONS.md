# Operations

These commands run on a DGX Spark that already has the prepared runtime under
`/srv/market-shock`: model weights, scenario data, the event catalog, the vLLM
image, and the OpenShell 0.1.2 runtime. That runtime is the `openshell` and
`openshell-gateway` release binaries in `/srv/market-shock/openshell/0.1.2/`
plus the local `ghcr.io/nvidia/openshell/supervisor:0.1.2` and
`ghcr.io/nvidia/openshell/sandbox:0.1.2` images. Model and data provisioning is
not distributed in this repository.

All operator commands read `COMPOSE_ENV_FILE` if it is set, otherwise `.env`,
otherwise `.env.spark.example`.

## Configure

Copy `.env.spark.example` to `.env` (or point `COMPOSE_ENV_FILE` at a private
file) and set:

| Variable | Meaning |
| --- | --- |
| `REMOTE_ROUTING_ENABLED` | `true` to allow research. Default `false`. |
| `NVIDIA_BASE_URL`, `NVIDIA_INFERENCE_API_KEY` | Endpoint serving the Luna judge and Nemotron 3 Ultra. Required when routing is on. |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_PROJECT_URL`, `LANGSMITH_ENDPOINT` | Optional LangSmith export of Relay traces |

Model IDs, ports, and service URLs are fixed in
`services/agent/src/market_agent/config.py` and `compose.yaml`.

## Prepare the OpenShell gateway (once per host)

The application runs its own OpenShell gateway, separate from any personal
OpenShell install. Install the pinned release binaries from verified release
tarballs into `/srv/market-shock/openshell/0.1.2/`, then run:

```bash
python3 scripts/spark/openshell_infrastructure.py
```

The script validates `scripts/spark/openshell/gateway.toml` with
`openshell-gateway config preflight`. It installs and enables the systemd user
unit `market-shock-openshell.service`, which follows the upstream packaged unit:
preflight, then idempotent `generate-certs` for local mTLS, then the gateway on
`127.0.0.1:17671`. It then registers the CLI as `market-shock` with the client
certificate. Gateway state, TLS material, and the credential key-encryption key
live under `/srv/market-shock/openshell`. Back them up together, and never
delete `state/openshell/gateway/credentials/` while providers exist.

To keep the gateway running after you log out, enable linger once with
`sudo loginctl enable-linger $USER`.

## After a source or env change

The agent image and its OpenShell receipt are bound to the source. After you
change code or the env file, rebuild and re-prepare:

```bash
scripts/spark/build-runtime.sh                        # build web/agent/tools images, pull the pinned vLLM image, write the image receipt
./demo stop
python3 scripts/spark/openshell_config.py             # provider profiles + providers (secrets) + sandbox env
python3 scripts/spark/openshell_runtime.py prepare-receipt
./demo start --recreate-agent
./demo doctor
```

`build-runtime.sh` is the only step that uses the network for images. The GPU
base image for tools is built once, separately, from
`services/tools/base.Dockerfile` (the command is in that file's header).

## After a change to `data/events/catalog.yaml`

The browser's curated events come from a content-addressed artifact under
`/srv/market-shock/events/artifacts/`. `current` points to the published one.
`doctor` fails `EVENT_CATALOG` until the artifact matches the repository
catalog. Build it in the data-prep image, which has the scenario readers, then
publish it on the host:

```bash
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v /srv/market-shock:/srv/market-shock:ro -v /srv/market-shock/events:/srv/market-shock/events \
  -v "$PWD:/workspace:ro" -w /workspace --entrypoint /opt/market-prep-venv/bin/python \
  market-shock/market-prep:phase9 scripts/spark/event_publication.py build \
  --repository-root /workspace --scenario-root /srv/market-shock/scenario --events-root /srv/market-shock/events
python3 scripts/spark/event_publication.py publish --repository-root "$PWD" \
  --scenario-root /srv/market-shock/scenario --events-root /srv/market-shock/events \
  --artifact /srv/market-shock/events/artifacts/<artifact printed by build>
./demo stop && ./demo start --recreate-agent   # the agent loads the catalog at startup
```

`build` qualifies every event against the bound scenario and never overwrites
an existing artifact. `publish` re-verifies the binding before it atomically
swaps `current`. It prints the prior target, which `event_publication.py
restore` accepts for a rollback.

## After a tools image or qualifier change

`reports/data/phase-09-qualification.json` is bound to the tools image, the
runtime image receipt, and `scripts/data/qualify_data.py` itself. `doctor`
fails `PHASE09_REPORT` after any of them changes. Re-qualify with `tools`
running. The qualifier refuses to run anywhere except the internal `backend`
network, where it reaches `tools` by name and has no default route:

```bash
docker run --rm --pull never --network market-shock_backend --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v /srv/market-shock:/srv/market-shock:ro -v /srv/market-shock/reports/data:/srv/market-shock/reports/data \
  -v "$PWD:/workspace:ro" -w /workspace --entrypoint /opt/market-prep-venv/bin/python \
  market-shock/market-prep:phase9 -m scripts.data.qualify_data
```

It compares 100 seeded GPU tool results with an independent DuckDB control.
It publishes the report even when the run fails, so copy the current report
aside first if you want to keep it.

## Start

```bash
./demo start                    # same host boot
./demo start --recreate-agent   # after a host reboot or an OpenShell re-prepare; requires ./demo stop first
```

`./demo` with no command also runs `start`. Before starting anything, startup
checks the following. It never builds, pulls, or downloads.

- the retention budget;
- the model, scenario, and image receipts;
- that the images match the current source tree;
- that Compose defines exactly `web`, `tools`, and `model`, and that only `web`
  publishes `127.0.0.1:3000`;
- the OpenShell receipt.

It then starts `tools` and `model`, starts the gateway unit, starts or
recreates the OpenShell agent, forwards its API to the `web` bridge with a
tracked `openshell forward start --background`, probes the remote endpoint when
routing is on, and finally starts `web`.
Startup ends with one of:

```text
READY FOR DEMO: http://localhost:3000
PREPARED, RESEARCH DISABLED: http://localhost:3000
```

Most of a cold start is vLLM loading Lightning.

## Status and doctor

```bash
./demo status    # short public view: artifacts, containers, OpenShell, readiness
./demo doctor    # full check; never prints credentials
```

`doctor` checks the following:

- the host is `aarch64` with a GB10 GPU;
- containers can use the GPU;
- the manifests, event catalog binding, and data qualification report are
  present and valid;
- the images and the OpenShell receipt are local and match;
- the retention budgets are within limits.

If the application is running, it also checks the following, then prints a
summary of the prepared data:

- the three Compose services;
- the OpenShell sandbox;
- tools health;
- agent status;
- when routing is on, a `/models` request to the remote endpoint from inside
  the sandbox (no inference).

## Stop

```bash
./demo stop
```

This stops the agent's port forward, the OpenShell agent, and the `web`,
`tools`, and `model` containers. The gateway unit keeps running. It keeps
models, data, state, traces, and networks. Do not run
`docker compose down -v`. Before powering off the host, run `./demo stop`, then
`sudo systemctl poweroff`.

## Remote routing off

With `REMOTE_ROUTING_ENABLED=false`:

- Startup prints `PREPARED, RESEARCH DISABLED`, and `status` exits non-zero
  with the same message. `doctor` reports `WARN AGENT_STATUS` and passes.
- The agent builds no graph. `/api/status` returns `ready: false` with a message
  that research needs the remote routing endpoint. Investigation, follow-up, and
  retry requests return 503.
- The dashboard and curated events still load.

This is deliberate. Switchyard's judge reviews every step, so the app cannot run
a local-only investigation, and it refuses the request instead of silently
skipping routing.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `explicit agent recreation requires a confirmed stopped runtime` | `--recreate-agent` while running | `./demo stop`, then start again |
| `runtime images are stale for the current source tree` | Source changed since `build-runtime.sh` | Follow "After a source or env change" |
| `OpenShell` verify or receipt failure | Env, policy, gateway config, OpenShell binaries or images, or the agent image changed without re-prepare | Run `openshell_config.py` and `openshell_runtime.py prepare-receipt`, then `./demo start --recreate-agent` |
| `OpenShell gateway is not answering` | Gateway unit failed: preflight, certificates, or Docker | `systemctl --user status market-shock-openshell`; `journalctl --user -u market-shock-openshell` |
| Status `unavailable: tools` while `tools` is healthy | The sandbox policy denied an MCP request (for example, a new tool or method not in `policy.yaml`) | `openshell logs market-agent --since 5m --source sandbox` with the app's XDG directories; add the tool to `policy.yaml`, re-prepare, recreate |
| `remote inference admission failed` | Sandbox cannot reach or authenticate to the endpoint | `./demo stop`, check the host network and key, then `./demo start --recreate-agent`. If it repeats, take the demo out of service. |
| `host port 3000 is occupied` | Another process holds the port | Stop that process; only Compose `web` may use it |
| Status `unavailable: tools` / `model` | Tools startup probe failed or vLLM still loading | Wait for vLLM, then `./demo doctor`; inspect diagnostics |
| Turn fails `judge_verdict_invalid` | Judge returned an unreadable verdict | Use **Retry**; the step is not silently kept |
| Turn fails `identity_mismatch` | Provider answered as a different model | Do not substitute models; check the endpoint |
| Turn fails `timeout`, `provider_error`, `context_length_exceeded` | Model call failed (remote connection errors are retried once) | Use **Retry**; check endpoint and `model` health |
| Local model stalls: `model` logs show ~1 token/s, `Running: 2 reqs`, growing `Waiting`/`Deferred`, GPU idle | A large `maxLength` in a tool or `Answer` schema (compiled to a `{1,N}` repetition) makes xgrammar decoding CPU-bound under `tool_choice="required"`; abandoned requests keep running because the OpenShell proxy does not pass client disconnects to vLLM | `docker restart market-shock-model-1`; keep large length bounds out of tool schemas (validate after generation) |
| Turn fails `uncited_answer` or `no_answer` | Answer ignored the tool evidence, or no structured answer | Use **Retry** or rephrase |
| 409 `Another investigation is running` | One turn runs at a time | Wait, or cancel the running turn |

When startup fails, it writes redacted diagnostics to
`/srv/market-shock/reports/diagnostics` (`scripts/spark/collect-diagnostics.sh`).
Do not share raw provider logs, rendered Compose config, request headers, or the
env file.

## Security reminders

- Keep the application loopback-bound. `tools` and `model` publish only on
  `127.0.0.1`, for the OpenShell supervisor. The OpenShell gateway
  (`127.0.0.1:17671`, mTLS) is a single-user, workstation-local control plane
  with no multi-user authentication. Never expose it.
- Credentials belong only in the env file and in endpoint-bound OpenShell
  providers. They must never go in images, the browser, reports, or logs.
- There is no unsandboxed agent fallback. If OpenShell cannot run the agent,
  the demo is down.
