# OpenClaw workbench extension

This directory preserves the OpenClaw workbench and its existing compilation,
query, risk review, diagnosis, queue, and trace deletion interactions. Its bundled
`core` and `prodebugger` implement the workbench independently of the repository's
original packages. The repository's original algorithms and entry points are not
modified by this extension.

Python 3.10+ is required. From the repository root:

```sh
python -m venv .venv
# Activate the environment, then:
python -m pip install -r extensions/openclaw/requirements.txt
python extensions/openclaw/run.py --seed-example
```

Open <http://127.0.0.1:8792/studio.html>. `--port` defaults to 8792;
`--data-dir` defaults to the repository's `artifacts/openclaw-workbench`, independent
of the current working directory. A plain launch starts an empty workbench.
`--seed-example` imports the repository's `examples/openclaw-id/trajectory.json` and its two policy
files once. Repeated launches keep existing records; a deleted example stays
deleted until restored in the UI. Import and automatic rule checks are local;
startup performs no model request or collection. Runtime output belongs in the
data directory, not in this extension's source files.

The public example is an exported, sanitized trajectory. Its saved analysis and
reading pages describe an existing result. Importing it performs local compilation
and automatic checks; it does **not** repeat the original OpenClaw task or rerun a
model review. The latest GUI is `studio.html`; the older audit, parsed, explorer,
and validation pages remain available. Historical case libraries, private
evaluation answers, session directories, and machine-specific walkthrough data
are not bundled. Empty historical catalog tabs are expected.

Online diagnosis and complete risk review require explicit model configuration.
For example, set `PRODEBUGGER_MODEL`, optionally `PRODEBUGGER_BASE_URL`, and the
credential variable `OPENAI_API_KEY` in your shell. `.env.example` is only a
template; neither it nor any existing local `.env` is read automatically. You can
instead pass `--config /path/to/model.json`:

```json
{
  "model": "your-model-id",
  "base_url": "https://your-provider.example/v1",
  "api_key_env": "OPENAI_API_KEY"
}
```

The JSON also accepts phase model names (`map_model`, `route_model`, `trace_model`,
`submit_model`, `manager_model`, `parse_model`) and an explicit `env_file` path
(relative to the JSON file). `PRODEBUGGER_*` environment settings override JSON.
Configuration never searches saved run summaries. Without a model or credentials,
submitted online jobs fail with a configuration message and preserve their inputs;
automatic results remain usable. API usage and provider charges occur only when
you explicitly submit an online job with a working configuration.

Optional local OTLP protobuf capture requires `requirements-otel.txt`. The
controlled local demo also requires Node 18+ and `npm install` inside
`extensions/openclaw` (see its `package.json`). Alternatively point
`PRODEBUGGER_OTEL_PACKAGE_JSON` at an explicitly chosen package with those SDK
dependencies, and optionally set `PRODEBUGGER_NODE`. No ancestor directories or
machine-specific OpenClaw installations are searched. Capture
and collection commands are preserved for explicit use; no capture runs on
startup. `safety_demo_capture.py` is also retained because the interactive demo
button and several import helpers rely on it.

Run offline regression checks from this directory with:

```sh
python -B -m unittest discover -s checks -v
```

Tests use synthetic fixtures and mocked model transports. Optional capture
integration checks skip when their Node/SDK dependencies are unavailable; a skip
does not verify collection. The portability tests cover standalone startup,
package isolation, explicit configuration, and idempotent example import.
