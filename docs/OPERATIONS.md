# Operations

The public repository contains the application source and the prepared-runtime
lifecycle. Model weights, private credentials, exhaustive qualification assets,
and provisioning receipts are intentionally not distributed.

The commands below therefore apply to a DGX Spark that has already been
provisioned from this exact source and has its immutable runtime beneath
`/srv/market-shock`.

## Start

Connect the supplied power adapter, display, input devices, and the approved
network before starting the workstation. Log in, then run:

```bash
cd market-analysis-demo
./demo start --recreate-agent
./demo doctor
```

Open <http://localhost:3000> only after startup prints:

```text
READY FOR DEMO: http://localhost:3000
```

The start path uses `--no-build --pull never`. It validates the source-bound
images, local models, event catalog, scenario, retention limits, and OpenShell
deployment before reporting readiness.

After a full host reboot, explicit agent recreation refreshes the prepared
OpenShell sandbox network namespace while preserving mounted investigation state
and traces. For another start during the same host boot, normal `./demo` startup
is sufficient.

## Status

```bash
./demo status
./demo doctor
```

`status` is a concise public view. `doctor` checks the host, GB10 GPU, prepared
artifacts, local generation, tools, OpenShell sandbox, and the configured
Switchyard route without printing provider credentials. It also makes one
bounded, authenticated, non-inference `/models` request from inside the sandbox
to prove DNS, TLS, authentication, and endpoint admission.

The page can appear before the full agent is ready after a machine restart.
Trust the startup and doctor results, not the presence of an HTTP page alone.

## Stop and transport

Stop the application before shutting down the operating system:

```bash
./demo stop
sudo systemctl poweroff
```

Wait until the workstation is fully off before disconnecting power. Do not use
`docker compose down -v`; the prepared data, encrypted state, traces, and local
images are part of the demo runtime.

On the measured conference unit, a normal cold recovery takes approximately
four to six minutes from power-on to a verified UI. Most of that time is local
Nemotron model initialization. Reserve ten minutes at a venue for login,
display, and network checks.

## Network expectations

Startup is offline with respect to artifacts: it does not build, pull, download,
or acquire data. Live investigations still require the approved inference
endpoint because Luna evaluates every Deep Agent reasoning call and Nemotron 3 Ultra may be
selected. LangSmith is optional observability; the inference endpoint is not.

Prefer a tested wired network or dedicated connection over captive conference
Wi-Fi. If the endpoint is unavailable, stop new live questions and use a clearly
labeled saved investigation rather than presenting it as a fresh run.

## Common failures

| Symptom | Meaning | Response |
| --- | --- | --- |
| No approved analysis model | The required route is unavailable | Restore the approved endpoint, then run `./demo doctor` |
| Analysis model connection failed | Provider transport failed | Check the venue network and retry once after readiness returns |
| Local generation timeout | The local model did not complete its bounded canary | Stop new work and inspect the model service; do not loop restarts |
| `REMOTE_PROVIDER` failed | The sandbox could not reach or authenticate to the approved inference endpoint | Run `./demo stop`, confirm host network readiness, then run `./demo start --recreate-agent` once; if it repeats, take the demo out of service |
| Evidence tool unavailable | The typed evidence service is not healthy | Run `./demo doctor` and inspect the safe diagnostics |
| Identity or receipt mismatch | Source, image, model, or data drift was detected | Do not bypass the check or substitute another model |

Startup writes bounded, redacted diagnostics under
`/srv/market-shock/reports/diagnostics`. Do not paste raw provider logs,
rendered Compose configuration, request headers, or `.env` into an issue.
