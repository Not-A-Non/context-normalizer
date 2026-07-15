from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from context_normalizer.cli import main
from context_normalizer.config import initialize_config
from context_normalizer.profiles import profile_manifest


class ConfigAndProfileCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "config"
        self.environment = patch.dict(
            os.environ, {"CONTEXT_NORMALIZER_HOME": str(self.home)}, clear=False
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(list(arguments))
        return code, output.getvalue(), error.getvalue()

    def test_catalog_queries_do_not_initialize_configuration(self) -> None:
        code, output, error = self.invoke("profiles", "list", "--format", "json")
        self.assertEqual((0, ""), (code, error))
        catalog = json.loads(output)
        self.assertEqual("software-writing-expansions", catalog["default"])
        self.assertEqual(4, len(catalog["profiles"]))
        code, output, error = self.invoke(
            "profiles", "show", "life-science-writing-expansions",
            "--format", "json",
        )
        self.assertEqual((0, ""), (code, error))
        self.assertEqual(
            profile_manifest("life-science-writing-expansions")["rule_count"],
            json.loads(output)["rule_count"],
        )
        self.assertFalse(self.home.exists())

    def test_config_list_is_read_only_and_reports_missing(self) -> None:
        code, output, error = self.invoke("config", "list", "--format", "json")
        self.assertEqual((0, ""), (code, error))
        self.assertTrue(all(not item["exists"] for item in json.loads(output)["files"]))
        self.assertFalse(self.home.exists())

    def test_config_show_and_profile_apply_are_machine_readable(self) -> None:
        initialize_config(register_installation=True)
        code, output, error = self.invoke(
            "config", "show", "cues", "--format", "json"
        )
        self.assertEqual((0, ""), (code, error))
        expected_cue_count = sum(
            len(profile_manifest(name)["cues"])
            for name in (
                "software-writing-expansions",
                "life-science-writing-expansions",
                "security-writing-expansions",
                "gpu-compiler",
            )
        )
        self.assertEqual(expected_cue_count, len(json.loads(output)["cues"]))
        code, active, error = self.invoke(
            "profiles", "active", "--format", "json"
        )
        self.assertEqual((0, ""), (code, error))
        digest = json.loads(active)["sha256"]
        code, output, error = self.invoke(
            "profiles", "apply", "life-science-writing-expansions",
            "--mode", "reset", "--expect-sha256", digest, "--yes",
        )
        self.assertEqual((0, ""), (code, error))
        receipt = json.loads(output)
        self.assertTrue(receipt["changed"])
        self.assertEqual(
            profile_manifest("life-science-writing-expansions")["rule_count"],
            receipt["rule_count"],
        )
        self.assertTrue((self.home / "rules.tsv.previous").is_file())
        self.assertTrue((self.home / "cues.txt.previous").is_file())

    def test_profile_apply_requires_explicit_confirmation(self) -> None:
        initialize_config(register_installation=True)
        code, _, error = self.invoke(
            "profiles", "apply", "gpu-compiler", "--mode", "merge"
        )
        self.assertEqual(2, code)
        self.assertIn("requires --yes", error)

    def test_init_records_core_installation_and_bidirectional_vocabulary(self) -> None:
        code, _, error = self.invoke("init")
        self.assertEqual((0, ""), (code, error))
        marker_path = self.home / "installation.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(1, marker["schema_version"])
        self.assertNotIn("clients", marker)
        self.assertTrue((self.home / "path-rules.tsv").is_file())

if __name__ == "__main__":
    unittest.main()
