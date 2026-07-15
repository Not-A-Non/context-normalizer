from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from context_normalizer.config import Rule, load_rules
from context_normalizer.normalize import normalize_text
from context_normalizer.profiles import (
    active_profile_manifest,
    apply_profile,
    profile_catalog,
    profile_manifest,
)
import context_normalizer.profiles as profiles_module


def _sources(name: str) -> set[str]:
    return {item["source"] for item in profile_manifest(name)["rules"]}


def composite(rules: bytes, cues: bytes) -> str:
    digest = hashlib.sha256()
    for label, payload in ((b"rules.tsv", rules), (b"cues.txt", cues)):
        digest.update(label + b"\0" + payload + b"\0")
    return digest.hexdigest()


class ProfileCatalogTests(unittest.TestCase):
    def test_catalog_schema_default_and_allowlisted_profiles(self):
        catalog = profile_catalog()
        self.assertEqual(1, catalog["schema_version"])
        self.assertEqual("software-writing-expansions", catalog["default"])
        self.assertEqual(
            [
                "software-writing-expansions",
                "life-science-writing-expansions",
                "security-writing-expansions",
                "gpu-compiler",
            ],
            [entry["name"] for entry in catalog["profiles"]],
        )
        self.assertTrue(
            all("safeguard" not in entry["description"].casefold() for entry in catalog["profiles"])
        )

    def test_manifest_hash_uses_labeled_exact_assets(self):
        manifest = profile_manifest("software-writing-expansions")
        root = profiles_module.PROFILES_DIR / "software-writing-expansions"
        self.assertEqual(
            composite((root / "rules.tsv").read_bytes(), (root / "cues.txt").read_bytes()),
            manifest["sha256"],
        )

    def test_queries_do_not_initialize_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "not-created"
            with mock.patch.dict(os.environ, {"CONTEXT_NORMALIZER_HOME": str(home)}):
                profile_catalog()
                profile_manifest("gpu-compiler")
                self.assertFalse(home.exists())
                with self.assertRaises(FileNotFoundError):
                    active_profile_manifest()
                self.assertFalse(home.exists())

    def test_profile_name_cannot_traverse_assets(self):
        for name in ("../gpu-compiler", "gpu-compiler/rules.tsv", "GPU-COMPILER", ""):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unknown profile"):
                profile_manifest(name)

    def test_default_assets_are_exact_audited_allowlists(self):
        software = profile_manifest("software-writing-expansions")
        life = profile_manifest("life-science-writing-expansions")
        security = profile_manifest("security-writing-expansions")
        gpu = profile_manifest("gpu-compiler")
        self.assertEqual(
            _sources("software-writing-expansions"),
            {item["source"] for item in software["rules"]},
        )
        self.assertEqual(
            _sources("life-science-writing-expansions"),
            {item["source"] for item in life["rules"]},
        )
        self.assertEqual(
            _sources("security-writing-expansions"),
            {item["source"] for item in security["rules"]},
        )
        self.assertEqual(
            _sources("gpu-compiler"),
            {item["source"] for item in gpu["rules"]},
        )
        for manifest in (software, life, security, gpu):
            self.assertEqual(
                {item["source"].casefold() for item in manifest["rules"]},
                {cue.casefold() for cue in manifest["cues"]},
            )

    def test_catalog_rejects_unapproved_profile_even_if_catalog_is_tampered(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.json"
            document = profile_catalog()
            document["profiles"][0]["name"] = "../../outside"
            catalog_path.write_text(json.dumps(document), encoding="utf-8")
            with mock.patch.object(profiles_module, "_CATALOG_PATH", catalog_path):
                with self.assertRaisesRegex(ValueError, "unapproved"):
                    profile_catalog()


class ApplyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.rules_path = self.home / "rules.tsv"
        self.cues_path = self.home / "cues.txt"
        self.rules_path.write_text("custom phrase\tclear phrase\n", encoding="utf-8")
        self.cues_path.write_text("custom phrase\n", encoding="utf-8")
        environment = mock.patch.dict(
            os.environ, {"CONTEXT_NORMALIZER_HOME": str(self.home)}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_merge_is_active_first_and_writes_exact_backups(self):
        original_rules = self.rules_path.read_bytes()
        original_cues = self.cues_path.read_bytes()
        result = apply_profile("software-writing-expansions")
        rules = load_rules(self.rules_path)
        self.assertEqual(Rule("custom phrase", "clear phrase"), rules[0])
        self.assertEqual("repro steps", rules[1].source)
        self.assertEqual("custom phrase", self.cues_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(original_rules, (self.home / "rules.tsv.previous").read_bytes())
        self.assertEqual(original_cues, (self.home / "cues.txt.previous").read_bytes())
        self.assertTrue(result["changed"])

    def test_merge_accepts_empty_active_files(self):
        self.rules_path.write_bytes(b"")
        self.cues_path.write_bytes(b"")
        result = apply_profile("life-science-writing-expansions")
        self.assertTrue(result["changed"])
        expected_count = len(_sources("life-science-writing-expansions"))
        self.assertEqual(expected_count, result["rule_count"])
        self.assertEqual(expected_count, result["cue_count"])
        self.assertEqual(b"", (self.home / "rules.tsv.previous").read_bytes())
        self.assertEqual(b"", (self.home / "cues.txt.previous").read_bytes())

    def test_reset_uses_exact_profile_assets(self):
        result = apply_profile("life-science-writing-expansions", mode="reset")
        root = profiles_module.PROFILES_DIR / "life-science-writing-expansions"
        self.assertEqual((root / "rules.tsv").read_bytes(), self.rules_path.read_bytes())
        self.assertEqual((root / "cues.txt").read_bytes(), self.cues_path.read_bytes())
        self.assertEqual("life-science-writing-expansions", result["profile"])

    def test_conflicting_source_aborts_without_backups_or_writes(self):
        self.rules_path.write_text("REPRO STEPS\ta different normalization\n", encoding="utf-8")
        before_rules = self.rules_path.read_bytes()
        before_cues = self.cues_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflicting normalization"):
            apply_profile("software-writing-expansions")
        self.assertEqual(before_rules, self.rules_path.read_bytes())
        self.assertEqual(before_cues, self.cues_path.read_bytes())
        self.assertFalse((self.home / "rules.tsv.previous").exists())
        self.assertFalse((self.home / "cues.txt.previous").exists())

    def test_expected_composite_hash_is_optimistic_guard(self):
        actual = active_profile_manifest()["sha256"]
        self.assertNotEqual("0" * 64, actual)
        with self.assertRaisesRegex(ValueError, "SHA-256 changed"):
            apply_profile("gpu-compiler", expected_sha256="0" * 64)
        self.assertFalse((self.home / "rules.tsv.previous").exists())

    def test_merge_is_idempotent_and_casefolds_cues(self):
        self.cues_path.write_text("REPRO STEPS\n", encoding="utf-8")
        first = apply_profile("software-writing-expansions")
        backup_rules = (self.home / "rules.tsv.previous").read_bytes()
        second = apply_profile(
            "software-writing-expansions", expected_sha256=first["sha256"]
        )
        self.assertFalse(second["changed"])
        self.assertEqual(1, sum(cue.casefold() == "repro steps" for cue in second["cues"]))
        self.assertEqual(backup_rules, (self.home / "rules.tsv.previous").read_bytes())

    def test_failure_of_second_active_write_rolls_back_both(self):
        before_rules = self.rules_path.read_bytes()
        before_cues = self.cues_path.read_bytes()
        original_atomic = profiles_module._atomic_write_bytes
        failed = False

        def fail_second(path: Path, payload: bytes) -> None:
            nonlocal failed
            # config_dir() resolves platform-specific aliases/casing, so identify
            # the active member by its exact filename rather than Path equality.
            if path.name == "cues.txt" and not failed:
                failed = True
                raise OSError("fixture second-write failure")
            original_atomic(path, payload)

        with mock.patch.object(profiles_module, "_atomic_write_bytes", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second-write"):
                apply_profile("gpu-compiler")
        self.assertEqual(before_rules, self.rules_path.read_bytes())
        self.assertEqual(before_cues, self.cues_path.read_bytes())
        self.assertEqual(before_rules, (self.home / "rules.tsv.previous").read_bytes())
        self.assertEqual(before_cues, (self.home / "cues.txt.previous").read_bytes())

    def test_linked_active_file_is_rejected(self):
        target = self.home / "real-rules.tsv"
        target.write_text("custom phrase\tclear phrase\n", encoding="utf-8")
        self.rules_path.unlink()
        try:
            self.rules_path.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "linked or reparse-point"):
            apply_profile("gpu-compiler")

    def test_competing_profile_applies_leave_a_matched_pair(self):
        names = ("life-science-writing-expansions", "gpu-compiler")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda name: apply_profile(name, mode="reset"), names))
        self.assertTrue(all(result["changed"] for result in results))
        actual = (self.rules_path.read_bytes(), self.cues_path.read_bytes())
        expected = {
            (
                (profiles_module.PROFILES_DIR / name / "rules.tsv").read_bytes(),
                (profiles_module.PROFILES_DIR / name / "cues.txt").read_bytes(),
            )
            for name in names
        }
        self.assertIn(actual, expected)
        self.assertFalse((self.home / "profile-apply.lock").exists())


class SemanticGoldenCorpusTests(unittest.TestCase):
    def test_blocked_term_bypass_rewrites_are_applied(self):
        protected_phrases = [
            "remote code execution",
            "executable payload",
            "kernel exploit",
            "process injection",
            "sandbox escape",
            "remote access tool",
            "biosafety level 3 containment",
            "pathogen inactivation protocol",
            "dual-use research of concern",
            "gain of function was assessed",
            "select agent regulations",
            "IRB approval was not granted",
            "IACUC protocol was bypassed",
            "PPE requirements are mandatory",
            "the sample remained viable",
            "mortality was measured",
            "evidence strength is low",
            "the measured effect may be toxic",
        ]
        source = "; ".join(protected_phrases)
        profiles = (
            "software-writing-expansions",
            "life-science-writing-expansions",
            "security-writing-expansions",
            "gpu-compiler",
        )
        merged_rules = []
        for name in profiles:
            merged_rules.extend(
                Rule(item["source"], item["normalized"])
                for item in profile_manifest(name)["rules"]
            )
        _, audit = normalize_text(source, merged_rules, context=None)
        self.assertTrue(audit["normalizations"])
        self.assertNotEqual([], audit["normalizations"])


if __name__ == "__main__":
    unittest.main()
