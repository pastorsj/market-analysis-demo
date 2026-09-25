# Operations

These commands run on a DGX Spark that already has the prepared runtime under
`/srv/market-shock`: model weights, scenario data, the event catalog, the vLLM
image, and OpenShell 0.0.116 binaries. Model and data provisioning is not
distributed in this repository.

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

## After a source or env change

The agent image and its OpenShell receipt are bound to the source. After you
change code or the env file, rebuild and re-prepare:

```bash
scripts/spark/build-runtime.sh                        # build web/agent/tools images, pull the pinned vLLM image, write the image receipt
./demo stop
python3 scripts/spark/openshell_config.py             # providers (secrets) + sandbox env + prepared policy
python3 scripts/spark/openshell_runtime.py prepare-receipt
./demo start --recreate-agent
./demo doctor
```

`build-runtime.sh` is the only step that uses the network for images. The GPU
base image for tools is built once, separately, from
`services/tools/base.Dockerfile` (the command is in that file's header).

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

It then starts `tools` and `model`, starts or recreates the OpenShell agent,
probes the remote endpoint when routing is on, and finally starts `web`.
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

This stops the OpenShell agent and the `web`, `tools`, and `model` containers. It
keeps models, data, state, traces, and networks. Do not run
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
| `OpenShell` verify or receipt failure | Env, policy, or image changed without re-prepare | Run `openshell_config.py` and `openshell_runtime.py prepare-receipt`, then `./demo start --recreate-agent` |
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

- Keep the application loopback-bound. The OpenShell gateway
  (`127.0.0.1:17671`) is a workstation-local control plane with no multi-user
  authentication. Never expose it.
- Credentials belong only in the env file and in endpoint-bound OpenShell
  providers. They must never go in images, the browser, reports, or logs.
- There is no unsandboxed agent fallback. If OpenShell cannot run the agent,
  the demo is down.
