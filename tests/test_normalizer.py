from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from context_normalizer.cli import main as cli_main
from context_normalizer.config import (
    Rule,
    initialize_config,
    load_path_rules,
    parse_rules_text,
)
from context_normalizer.lifecycle import purge_installation, read_installation_marker
from context_normalizer.normalize import normalize_text, translate_reversible_text
from context_normalizer.workspace import create_workspace


class NormalizerTests(unittest.TestCase):
    def test_custom_vocabulary_normalizes_context(self) -> None:
        result, audit = normalize_text(
            "Run the benchmark campaign once.",
            [Rule("benchmark campaign", "performance evaluation")],
            context="Local work.",
        )
        self.assertIn("performance evaluation", result)
        self.assertEqual(1, audit["normalizations"][0]["count"])
        self.assertTrue(all(audit["invariant_checks"].values()))

    def test_protected_content_is_exact(self) -> None:
        source = (
            '`HIP VMM` "benchmark campaign" C:\\b\\kernel.glsl '
            "https://example.invalid/benchmark-campaign "
            "3db593ebcf1b4264b6551ff3cdb28623121b715f774c7674ef45b9f8b09a4a38"
        )
        result, audit = normalize_text(
            source,
            [Rule("benchmark campaign", "performance evaluation")],
            context=None,
        )
        self.assertEqual(source, result)
        self.assertEqual(5, audit["protected_span_count"])

    def test_normalization_is_single_pass(self) -> None:
        result, _ = normalize_text(
            "alpha beta",
            [Rule("alpha", "beta"), Rule("beta", "gamma")],
            context=None,
        )
        self.assertEqual("beta gamma", result)

    def test_adjacent_vocabulary_is_normalized(self) -> None:
        result, audit = normalize_text(
            "kernelkernel",
            [Rule("kernel", "runtime-boundary")],
            context=None,
        )
        self.assertEqual("runtime-boundaryruntime-boundary", result)
        self.assertEqual(
            [{"source": "kernel", "normalized": "runtime-boundary", "count": 2}],
            audit["normalizations"],
        )

    def test_embedded_substring_is_preserved(self) -> None:
        result, _ = normalize_text(
            "reagent agent agentagent",
            [Rule("agent", "clinical study agent")],
            context=None,
        )
        self.assertEqual(
            "reagent clinical study agent clinical study agentclinical study agent",
            result,
        )

    def test_longest_vocabulary_entry_wins(self) -> None:
        result, _ = normalize_text(
            "benchmark campaign",
            [
                Rule("campaign", "evaluation"),
                Rule("benchmark campaign", "performance evaluation"),
            ],
            context=None,
        )
        self.assertEqual("performance evaluation", result)

    def test_semantic_identifiers_are_invariant(self) -> None:
        with self.assertRaisesRegex(ValueError, "semantic invariant"):
            normalize_text(
                "target gfx1151 and Q6_0",
                [Rule("gfx1151", "gfx1150")],
                context=None,
            )

    def test_reversible_vocabulary_normalizes_quoted_and_adjacent_text(self) -> None:
        rules = [
            Rule("kernel", "runtime-boundary"),
            Rule("remote access tool", "remote administration utility"),
        ]
        source = '"kernelkernel remote access tool"'
        model = translate_reversible_text(source, rules)
        self.assertEqual(
            '"runtime-boundaryruntime-boundary remote administration utility"',
            model,
        )
        self.assertEqual(source, translate_reversible_text(model, rules, reverse=True))

    def test_tiling_substring_inside_word_is_preserved(self) -> None:
        result, audit = normalize_text("banana", [Rule("na", "XX")], context=None)
        self.assertEqual("banana", result)
        self.assertEqual([], audit["normalizations"])

    def test_apostrophes_do_not_suppress_normalization(self) -> None:
        result, audit = normalize_text(
            "don't skip repro steps, it's important",
            [Rule("repro steps", "reproduction steps")],
            context=None,
        )
        self.assertEqual("don't skip reproduction steps, it's important", result)
        self.assertEqual(1, audit["normalizations"][0]["count"])

    def test_unusual_casefold_vocabulary_does_not_crash(self) -> None:
        result, _ = normalize_text("i İ stays", [Rule("İ", "X")], context=None)
        self.assertEqual("i X stays", result)

    def test_uniform_casing_is_preserved(self) -> None:
        rules = [Rule("MyProj", "acme")]
        self.assertEqual(
            "ACME and acme and Acme and acme",
            translate_reversible_text("MYPROJ and MyProj and Myproj and myproj", rules),
        )
        self.assertEqual(
            "MYPROJ and MyProj and MyProj",
            translate_reversible_text("ACME and acme and Acme", rules, reverse=True),
        )

    def test_sentinel_lookalike_input_is_not_corrupted(self) -> None:
        source = "0 `benchmark campaign` benchmark campaign"
        result, _ = normalize_text(
            source,
            [Rule("benchmark campaign", "performance evaluation")],
            context=None,
        )
        self.assertEqual("0 `benchmark campaign` performance evaluation", result)

    def test_cli_normalize_preserves_crlf_and_hashes_input_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"benchmark campaign\r\nplain line\r\n"
            (root / "in.txt").write_bytes(payload)
            (root / "rules.tsv").write_text(
                "benchmark campaign\tperformance evaluation\n", encoding="utf-8"
            )
            status = cli_main(
                [
                    "normalize",
                    str(root / "in.txt"),
                    "--output",
                    str(root / "out.txt"),
                    "--audit",
                    str(root / "audit.json"),
                    "--rules",
                    str(root / "rules.tsv"),
                    "--no-context",
                ]
            )
            self.assertEqual(0, status)
            self.assertEqual(
                b"performance evaluation\r\nplain line\r\n",
                (root / "out.txt").read_bytes(),
            )
            audit = json.loads((root / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), audit["original_sha256"]
            )

    def test_rules_reject_exotic_line_separators(self) -> None:
        # U+2028 inside a field must fail closed, not silently truncate the
        # rule and fabricate a phantom rule from the remainder.
        text = "benchmark campaign\tperformance evaluation\n"
        with self.assertRaisesRegex(ValueError, "unsupported line separator"):
            parse_rules_text(text)
        self.assertEqual(
            [Rule("benchmark campaign", "performance evaluation")],
            parse_rules_text("benchmark campaign\tperformance evaluation\r\n"),
        )

    def test_reversible_round_trip_property(self) -> None:
        rules = [
            Rule("MyProj", "acme"),
            Rule("kernel", "runtime-boundary"),
        ]
        samples = [
            "MyProj uses the kernel",
            "MYPROJ AND KERNEL",
            "Kernel first, then MyProj",
            "no vocabulary here at all",
            "don't touch MyProj's kernel, it's fine",
            "kernelkernel MyProj kernel",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                forward = translate_reversible_text(sample, rules)
                self.assertEqual(
                    sample, translate_reversible_text(forward, rules, reverse=True)
                )


class LifecycleTests(unittest.TestCase):
    def test_installation_initializes_explicit_bidirectional_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": directory}):
                initialize_config(register_installation=True)
                self.assertEqual([], load_path_rules())
                marker = read_installation_marker()
                self.assertEqual(1, marker["schema_version"])
                self.assertNotIn("clients", marker)

    def test_purge_preserves_unowned_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "normalizer"
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": str(config)}):
                initialize_config(register_installation=True)
                unowned = config / "operator-notes.txt"
                unowned.write_text("keep", encoding="utf-8")
                result = purge_installation()
                self.assertFalse(result["config_directory_removed"])
                self.assertEqual("keep", unowned.read_text(encoding="utf-8"))
                self.assertFalse((config / "installation.json").exists())

    def test_purge_removes_bidirectional_vocabulary_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "normalizer"
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": str(config)}):
                initialize_config(register_installation=True)
                with redirect_stdout(StringIO()):
                    self.assertEqual(
                        0,
                        cli_main(
                            [
                                "vocabulary",
                                "add",
                                "source name",
                                "normalized name",
                                "--bidirectional",
                            ]
                        ),
                    )
                self.assertTrue((config / "path-rules.tsv.previous").is_file())
                result = purge_installation()
                self.assertTrue(result["config_directory_removed"])

    def test_purge_removes_clean_workspace_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "normalizer"
            source = root / "source"
            source.mkdir()
            (source / "payload.txt").write_text("unchanged", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": str(config)}):
                initialize_config(register_installation=True)
                created = create_workspace(
                    source, config / "workspaces", mode="filesystem"
                )
                result = purge_installation()
                self.assertTrue(result["config_directory_removed"])
                self.assertFalse(Path(created["mirror"]).exists())
                self.assertEqual(
                    "unchanged", (source / "payload.txt").read_text(encoding="utf-8")
                )

    def test_purge_fails_closed_for_dirty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "normalizer"
            source = root / "source"
            source.mkdir()
            (source / "payload.txt").write_text("base", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": str(config)}):
                initialize_config(register_installation=True)
                created = create_workspace(
                    source, config / "workspaces", mode="filesystem"
                )
                mirror = Path(created["mirror"])
                (mirror / "payload.txt").write_text("unmerged", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unmerged changes"):
                    purge_installation()
                self.assertTrue((config / "installation.json").exists())
                self.assertEqual("unmerged", (mirror / "payload.txt").read_text())


class VocabularyCliTests(unittest.TestCase):
    def _run(self, arguments: list[str]) -> tuple[int, str]:
        output = StringIO()
        with redirect_stdout(output):
            status = cli_main(arguments)
        return status, output.getvalue()

    def test_vocabulary_list_is_identified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": directory}):
                status, output = self._run(["vocabulary", "list", "--format", "json"])
                self.assertEqual(0, status)
                document = json.loads(output)
                self.assertEqual(len(document["rules"]), document["count"])
                self.assertRegex(document["sha256"], r"^[0-9a-f]{64}$")
                self.assertIn("normalized", document["rules"][0])
                status, output = self._run(["vocabulary", "list", "--format", "table"])
                self.assertEqual(0, status)
                self.assertIn("NORMALIZED", output.splitlines()[0])

    def test_bidirectional_vocabulary_can_be_tuned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": directory}):
                initialize_config()
                self.assertEqual(
                    0,
                    self._run(
                        [
                            "vocabulary",
                            "add",
                            "kernel",
                            "runtime-boundary",
                            "--bidirectional",
                        ]
                    )[0],
                )
                status, output = self._run(
                    [
                        "vocabulary",
                        "list",
                        "--bidirectional",
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(0, status)
                self.assertEqual(
                    "runtime-boundary", json.loads(output)["rules"][0]["normalized"]
                )

    def test_vocabulary_update_and_remove_preserve_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": directory}):
                initialize_config()
                self.assertEqual(
                    0, self._run(["vocabulary", "add", "custom term", "clear term"])[0]
                )
                self.assertEqual(
                    0,
                    self._run(
                        [
                            "vocabulary",
                            "add",
                            "custom term",
                            "new clear term",
                            "--update",
                        ]
                    )[0],
                )
                self.assertEqual(
                    0, self._run(["vocabulary", "remove", "custom term"])[0]
                )
                self.assertTrue(Path(directory, "rules.tsv.previous").exists())


if __name__ == "__main__":
    unittest.main()
