"""Portable entry point for the preserved OpenClaw workbench."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys

EXTENSION = Path(__file__).resolve().parent
REPOSITORY = EXTENSION.parents[1]


def bootstrap():
    """Resolve the extension's own modules even when launched from repo root."""
    for name in ("prodebugger", "core", "compile_run", "record", "profiles", "session", "views"):
        module = sys.modules.get(name)
        path = getattr(module, "__file__", None)
        if path and not Path(path).resolve().is_relative_to(EXTENSION):
            raise RuntimeError(f"{name} was already loaded outside this extension; use a fresh Python process")
    for path in (EXTENSION, EXTENSION / "core"):
        text = str(path)
        if text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)
    package = importlib.import_module("prodebugger")
    if not Path(package.__file__).resolve().is_relative_to(EXTENSION):
        raise RuntimeError("OpenClaw must use its bundled prodebugger package")


def seed_example(store, path=None):
    """Compile/import the one public example once; never start model/capture work."""
    folder = Path(path) if path else REPOSITORY / "examples" / "openclaw-id"
    load = lambda name: json.loads((folder / name).read_text(encoding="utf-8-sig"))
    source = load("trajectory.json")
    case_id = "openclaw-id"
    saved = next((case for case in store.index["cases"] if case["id"] == case_id), None)
    if saved:
        from prodebugger.record_bridge import digest, read_json
        if digest(read_json(store._resolve(saved["source_path"]))) != digest(source):
            raise ValueError("The public example changed; use a new --data-dir to import it")
        return {"case_id": case_id, "imported": False}
    if any(case["id"] == case_id for case in store.index.get("deleted_cases", [])):
        return {"case_id": case_id, "imported": False, "deleted": True}
    risk_path = folder / "risk-policy.json"
    store.add_source(source, load("policy.json"), title="OpenClaw · public ID example",
                     case_id=case_id, risk_policy=load(risk_path.name) if risk_path.is_file() else None)
    return {"case_id": case_id, "imported": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--data-dir", type=Path, default=REPOSITORY / "artifacts" / "openclaw-workbench")
    parser.add_argument("--config", type=Path, help="Explicit model JSON; relative env_file paths resolve beside it")
    parser.add_argument("--seed-example", action="store_true", help="Idempotently import the public example without model calls")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    bootstrap()
    if args.config:
        os.environ["PRODEBUGGER_DIAGNOSIS_CONFIG"] = str(args.config.expanduser().resolve())
    # Validate explicit configuration before binding the listener. No API calls.
    from prodebugger.diagnosis_jobs import model_configuration
    _, configuration = model_configuration()
    from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore

    class WorkbenchHandler(Handler):
        def do_GET(self):
            if self.path == "/":
                self.path = "/studio.html"
            return super().do_GET()

    store = SafetyStore(args.data_dir)
    if args.seed_example:
        print(json.dumps({"seed": seed_example(store)}, ensure_ascii=False), flush=True)
    server = SafetyHTTPServer(("127.0.0.1", args.port), WorkbenchHandler)
    server.store = store
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}/studio.html",
                      "data_dir": str(store.folder), "model": configuration}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for manager in (store._diagnoses, store._risk_jobs):
            if manager is not None:
                manager.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
