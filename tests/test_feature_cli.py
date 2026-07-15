from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from context_normalizer.cli import build_parser, main as cli_main
from context_normalizer.config import Rule
from context_normalizer.workspace import create_workspace


class FeatureCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "normalizer"
        self.source = self.root / "source"
        self.source.mkdir()
        self.environment = patch.dict(
            os.environ, {"CONTEXT_NORMALIZER_HOME": str(self.home)}, clear=False
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _write_configuration(self) -> None:
        self.home.mkdir(exist_ok=True)
        self.home.joinpath("rules.tsv").write_text(
            "kernel\truntime-boundary\n"
            "repro steps\treproduction steps\n"
            "remote access tool\tremote administration utility\n",
            encoding="utf-8",
        )
        self.home.joinpath("path-rules.tsv").write_text(
            "kernel\truntime-boundary\n"
            "repro steps\treproduction steps\n"
            "remote access tool\tremote administration utility\n",
            encoding="utf-8",
        )
        self.home.joinpath("cues.txt").write_text("", encoding="utf-8")
        self.home.joinpath("context.txt").write_text("", encoding="utf-8")
        self.home.joinpath("subagent-context.txt").write_text("", encoding="utf-8")

    def test_legacy_launcher_commands_are_absent(self) -> None:
        parser = build_parser()
        for command in ("run", "mode", "warnings", "hook-status", "disable"):
            with self.assertRaises(SystemExit):
                parser.parse_args([command])

    def test_capabilities_report_normalization_surfaces(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, cli_main(["capabilities"]))
        document = json.loads(output.getvalue())
        self.assertTrue(document["context_normalization"])
        self.assertTrue(document["workspace_normalization"])
        self.assertEqual(["codex", "pi"], document["host_bridges"])

    def test_bridge_normalizes_bidirectional_text_for_model_and_display(self) -> None:
        self._write_configuration()
        model = io.StringIO()
        with patch(
            "sys.stdin", io.StringIO('"kernelkernel remote access tool"')
        ), redirect_stdout(model):
            code = cli_main(["bridge", "normalize", "--direction", "model"])
        self.assertEqual(0, code)
        self.assertEqual(
            '"runtime-boundaryruntime-boundary remote administration utility"',
            model.getvalue(),
        )
        display = io.StringIO()
        with patch("sys.stdin", io.StringIO(model.getvalue())), redirect_stdout(display):
            code = cli_main(["bridge", "normalize", "--direction", "display"])
        self.assertEqual(0, code)
        self.assertEqual('"kernelkernel remote access tool"', display.getvalue())

    def test_bridge_normalizes_prompt_paths_payloads_and_completion(self) -> None:
        self._write_configuration()
        source_directory = self.source / "kernel" / "repro steps"
        source_directory.mkdir(parents=True)
        source_file = source_directory / "remote access tool.txt"
        source_file.write_text(
            "kernelkernel remote access tool\n", encoding="utf-8"
        )
        created = create_workspace(
            self.source,
            self.home / "workspaces",
            mode="filesystem",
            path_rules=[
                Rule("kernel", "runtime-boundary"),
                Rule("repro steps", "reproduction steps"),
                Rule("remote access tool", "remote administration utility"),
            ],
        )
        mirror = Path(created["mirror"])
        submitted = io.StringIO()
        previous = Path.cwd()
        try:
            os.chdir(mirror)
            with patch(
                "sys.stdin", io.StringIO("kernelkernel remote access tool")
            ), redirect_stdout(submitted):
                code = cli_main(["bridge", "submit"])
        finally:
            os.chdir(previous)
        self.assertEqual(0, code)
        self.assertEqual(
            "runtime-boundaryruntime-boundary remote administration utility",
            submitted.getvalue(),
        )
        mirror_file = (
            mirror
            / "runtime-boundary"
            / "reproduction steps"
            / "remote administration utility.txt"
        )
        self.assertEqual(
            "runtime-boundaryruntime-boundary remote administration utility\n",
            mirror_file.read_text(encoding="utf-8"),
        )
        output = (
            mirror
            / "runtime-boundary"
            / "reproduction steps"
            / "new remote administration utility.txt"
        )
        output.write_text(
            "remote administration utility runtime-boundaryruntime-boundary\n",
            encoding="utf-8",
        )
        receipt = io.StringIO()
        with redirect_stdout(receipt):
            self.assertEqual(
                0, cli_main(["bridge", "complete", "--workspace", str(mirror)])
            )
        self.assertEqual("applied", json.loads(receipt.getvalue())["status"])
        source_output = source_directory / "new remote access tool.txt"
        self.assertEqual(
            "remote access tool kernelkernel\n",
            source_output.read_text(encoding="utf-8"),
        )

    def test_workspace_create_uses_explicit_bidirectional_vocabulary(self) -> None:
        self._write_configuration()
        (self.source / "kernel").mkdir()
        (self.source / "kernel" / "value.txt").write_text(
            "kernel\n", encoding="utf-8"
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "workspace",
                    "create",
                    str(self.source),
                    "--mode",
                    "filesystem",
                    "--normalize-paths",
                    "--yes",
                ]
            )
        self.assertEqual(0, code)
        mirror = Path(json.loads(output.getvalue())["mirror"])
        normalized = mirror / "runtime-boundary" / "value.txt"
        self.assertEqual("runtime-boundary\n", normalized.read_text(encoding="utf-8"))

    def test_bridge_requires_a_normalized_workspace(self) -> None:
        self._write_configuration()
        with patch("sys.stdin", io.StringIO("kernel")):
            self.assertEqual(2, cli_main(["bridge", "submit", "--workspace", str(self.source)]))


if __name__ == "__main__":
    unittest.main()
