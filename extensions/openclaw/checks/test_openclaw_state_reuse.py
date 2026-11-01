"""An existing lab installation never bypasses current official trust checks."""
from argparse import Namespace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prodebugger import openclaw_collect_gateway as gateway


class StateReuseChecks(unittest.TestCase):
    def test_only_finished_compatible_state_is_reused_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "isolated-state"
            state.mkdir()
            original = base / "old-manifest.json"
            previous = {"schema": "openclaw-evaluation/v1", "data_origin": "controlled",
                        "runtime_version": "2026.9.9", "finished_at": "2026-10-20T19:37:08Z",
                        "plugin_trust_verified": True, "state_directory": str(state)}
            original.write_text(json.dumps(previous))
            before = original.read_bytes()
            selected, evidence = gateway.reuse_installed_state(base / "new-run", original, "2026.9.9")
            self.assertEqual(selected, state.resolve())
            self.assertEqual(evidence["sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(original.read_bytes(), before)
            for changed in ({"finished_at": None}, {"runtime_version": "2026.10.5"},
                            {"plugin_trust_verified": False}, {"data_origin": "production"},
                            {"state_directory": str(base / "missing")}):
                with self.subTest(changed=changed):
                    original.write_text(json.dumps({**previous, **changed}))
                    with self.assertRaises(ValueError):
                        gateway.reuse_installed_state(base / "new-run", original, "2026.9.9")

    def test_reuse_stops_on_failed_fresh_trust_inspection_without_enabling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"plugins": {"entries": {"diagnostics-otel": {"enabled": False}}}}
            gateway.write_json(root / "openclaw.json", config)
            manifest = {"runtime_version": "2026.9.9", "reused_install_source": {"path": "previous.json"}}
            args = Namespace(node=Path("node"), cli=Path("openclaw.mjs"), install_timeout=30)
            with patch.object(gateway, "run_setup_command") as install, patch.object(
                    gateway, "inspect_official_exporter", side_effect=RuntimeError("trust changed")) as inspect:
                with self.assertRaisesRegex(RuntimeError, "trust changed"):
                    gateway.install_official_exporter(args, root, {}, manifest)
            install.assert_not_called()
            self.assertEqual(inspect.call_args.args[-1], False)
            self.assertFalse(manifest["plugin_trust_verified"])
            self.assertFalse(json.loads((root / "openclaw.json").read_text())["plugins"]["entries"]["diagnostics-otel"]["enabled"])


if __name__ == "__main__":
    unittest.main()
