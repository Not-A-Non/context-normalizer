from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from context_normalizer import workspace as ws


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "real-source"
        self.managed = self.root / "aliases"
        self.source.mkdir()
        self.managed.mkdir()
        (self.source / "keep.txt").write_text("one\n", encoding="utf-8")
        (self.source / "remove.txt").write_text("remove\n", encoding="utf-8")

    def create(self, **kwargs):
        result = ws.create_workspace(
            self.source, self.managed, mode="filesystem", **kwargs
        )
        return Path(result["mirror"]), result

    def test_create_is_neutral_and_content_addressed(self) -> None:
        mirror, result = self.create()
        self.assertRegex(mirror.name, r"^workspace-[0-9a-f]{12}$")
        self.assertEqual("one\n", (mirror / "keep.txt").read_text())
        self.assertTrue(ws.verify_workspace(mirror)["base_verified"])
        self.assertTrue(ws.status_workspace(mirror)["clean"])
        marker = json.loads((mirror / ws.MARKER).read_text())
        state = Path(marker["state"])
        manifest = json.loads((state / "base" / "manifest.json").read_text())
        blob = state / "base" / "blobs" / manifest["files"]["keep.txt"]["sha256"]
        self.assertEqual((self.source / "keep.txt").read_bytes(), blob.read_bytes())
        self.assertEqual("filesystem", result["mode"])

    def test_invalid_or_nested_roots_are_rejected(self) -> None:
        with self.assertRaises(ws.WorkspaceError):
            ws.create_workspace(self.source, self.source / "managed", mode="filesystem")
        with self.assertRaises(ws.WorkspaceError):
            ws.create_workspace(
                self.source, self.managed, name="meaningful-name", mode="filesystem"
            )

    def test_source_add_modify_delete_syncs_to_mirror(self) -> None:
        mirror, _ = self.create()
        (self.source / "keep.txt").write_text("two\n", encoding="utf-8")
        (self.source / "add.txt").write_text("add\n", encoding="utf-8")
        (self.source / "remove.txt").unlink()
        plan = ws.plan_workspace(mirror, direction="to-mirror")
        self.assertFalse(plan["conflicts"])
        self.assertEqual(
            {"add", "modify", "delete"}, {item["kind"] for item in plan["operations"]}
        )
        ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertEqual("two\n", (mirror / "keep.txt").read_text())
        self.assertEqual("add\n", (mirror / "add.txt").read_text())
        self.assertFalse((mirror / "remove.txt").exists())
        self.assertTrue(ws.status_workspace(mirror)["clean"])

    def test_empty_directories_are_preserved(self) -> None:
        mirror, _ = self.create()
        (self.source / "empty" / "nested").mkdir(parents=True)
        plan = ws.plan_workspace(mirror, direction="to-mirror")
        self.assertEqual(
            ["mkdir", "mkdir"], [item["kind"] for item in plan["operations"]]
        )
        ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertTrue((mirror / "empty" / "nested").is_dir())

    def test_mirror_change_syncs_back(self) -> None:
        mirror, _ = self.create()
        (mirror / "keep.txt").write_text("mirror\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertEqual("mirror\n", (self.source / "keep.txt").read_text())
        self.assertTrue(ws.status_workspace(mirror)["clean"])

    def test_translated_filesystem_mirror_round_trips_paths_and_text_payloads(
        self,
    ) -> None:
        real = self.source / "kernel" / "program.exe"
        real.parent.mkdir()
        real.write_text("kernelkernel kernel base\n", encoding="utf-8")
        mirror, result = self.create(
            path_rules=[{"source": "kernel", "normalized": "runtime boundary"}]
        )
        self.assertTrue(result["path_normalization"])
        filtered = mirror / "runtime boundary" / "program.exe"
        self.assertTrue(filtered.is_file())
        self.assertFalse((mirror / "kernel").exists())
        self.assertEqual(
            "runtime boundaryruntime boundary runtime boundary base\n",
            filtered.read_text(encoding="utf-8"),
        )

        (mirror / "runtime boundary" / "new.txt").write_text(
            "runtime boundaryruntime boundary runtime boundary new\n",
            encoding="utf-8",
        )
        back = ws.plan_workspace(mirror, direction="back")
        self.assertIn(
            "runtime boundary/new.txt", [item["path"] for item in back["operations"]]
        )
        ws.apply_plan(mirror, back, expected_sha256=back["plan_sha256"])
        self.assertEqual(
            "kernelkernel kernel new\n",
            (self.source / "kernel" / "new.txt").read_text(encoding="utf-8"),
        )

        (self.source / "kernel" / "program.exe").write_text(
            "kernel changed\n", encoding="utf-8"
        )
        forward = ws.plan_workspace(mirror, direction="to-mirror")
        self.assertIn(
            "runtime boundary/program.exe",
            [item["path"] for item in forward["operations"]],
        )
        ws.apply_plan(mirror, forward, expected_sha256=forward["plan_sha256"])
        self.assertEqual(
            "runtime boundary changed\n", filtered.read_text(encoding="utf-8")
        )
        self.assertTrue(ws.status_workspace(mirror)["clean"])

    def test_translated_filesystem_mirror_leaves_binary_payload_unchanged(self) -> None:
        payload = b"\x00kernel\xffruntime boundary\x00"
        (self.source / "payload.bin").write_bytes(payload)
        mirror, _ = self.create(
            path_rules=[{"source": "kernel", "normalized": "runtime boundary"}]
        )
        self.assertEqual(payload, (mirror / "payload.bin").read_bytes())

    def test_translated_mirror_rejects_path_collisions(self) -> None:
        (self.source / "kernel").mkdir()
        (self.source / "kernel" / "a.txt").write_text("kernel\n", encoding="utf-8")
        (self.source / "filter").mkdir()
        (self.source / "filter" / "a.txt").write_text("filter\n", encoding="utf-8")
        # The round-trip guard rejects the pre-existing "filter" directory
        # before the manifest-level collision check can fire; both fail closed.
        with self.assertRaisesRegex(ws.WorkspaceError, "does not round-trip"):
            self.create(path_rules=[{"source": "kernel", "normalized": "filter"}])

    def test_translated_mirror_rejects_natural_normalized_payload(self) -> None:
        (self.source / "notes.txt").write_text(
            "runtime boundary is public wording\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ws.WorkspaceError, "does not round-trip"):
            self.create(
                path_rules=[{"source": "kernel", "normalized": "runtime boundary"}]
            )

    def test_translated_mirror_rejects_ambiguous_reverse_rules(self) -> None:
        with self.assertRaisesRegex(
            ws.WorkspaceError, "ambiguous bidirectional normalized vocabulary"
        ):
            self.create(
                path_rules=[
                    {"source": "kernel", "normalized": "shared"},
                    {"source": "shader", "normalized": "shared"},
                ]
            )

    def test_scan_cache_tracks_changes_and_verify_bypasses_it(self) -> None:
        mirror, _ = self.create()
        state = Path(json.loads((mirror / ws.MARKER).read_text())["state"])
        cache = state / "cache" / "source-scan.json"
        self.assertTrue(ws.status_workspace(mirror)["clean"])
        self.assertTrue(cache.is_file())
        cached = json.loads(cache.read_text(encoding="utf-8"))
        self.assertIn("keep.txt", cached["files"])
        # status trusts a (size, mtime) hit; verify must re-hash and see truth.
        info = (self.source / "keep.txt").stat()
        cached["files"]["keep.txt"] = {
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "sha256": "0" * 64,
        }
        cache.write_text(json.dumps(cached), encoding="utf-8")
        self.assertTrue(ws.status_workspace(mirror)["source_changed"])
        self.assertTrue(ws.verify_workspace(mirror)["clean"])
        # A real edit gets a new mtime, so the poisoned entry is discarded.
        (self.source / "keep.txt").write_text("two\n", encoding="utf-8")
        status = ws.status_workspace(mirror)
        self.assertTrue(status["source_changed"])
        plan = ws.plan_workspace(mirror, direction="to-mirror")
        ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertTrue(ws.status_workspace(mirror)["clean"])

    def test_translated_scan_populates_identity_cache(self) -> None:
        (self.source / "kernel.txt").write_bytes(b"kernel data\n")
        mirror, _ = self.create(
            path_rules=[{"source": "kernel", "normalized": "runtime boundary"}]
        )
        state = Path(json.loads((mirror / ws.MARKER).read_text())["state"])
        translation_cache = state / "cache" / "source-translation.json"
        self.assertTrue(ws.status_workspace(mirror)["clean"])
        self.assertTrue(translation_cache.is_file())
        cached = json.loads(translation_cache.read_text(encoding="utf-8"))
        raw_sha = hashlib.sha256(b"kernel data\n").hexdigest()
        self.assertEqual(
            hashlib.sha256(b"runtime boundary data\n").hexdigest(),
            cached["by_source"][raw_sha]["sha256"],
        )

    def test_modify_modify_conflict_aborts_whole_plan(self) -> None:
        mirror, _ = self.create()
        (self.source / "keep.txt").write_text("source\n", encoding="utf-8")
        (mirror / "keep.txt").write_text("mirror\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        self.assertEqual("keep.txt", plan["conflicts"][0]["path"])
        self.assertEqual(
            plan["conflicts"], ws.conflicts_workspace(mirror, direction="back")
        )
        with self.assertRaises(ws.WorkspaceConflict):
            ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertEqual("source\n", (self.source / "keep.txt").read_text())

    def test_stale_plan_is_rejected(self) -> None:
        mirror, _ = self.create()
        (mirror / "keep.txt").write_text("first\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        (mirror / "keep.txt").write_text("second\n", encoding="utf-8")
        with self.assertRaisesRegex(ws.WorkspaceError, "stale plan"):
            ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertEqual("one\n", (self.source / "keep.txt").read_text())

    def test_untrusted_plan_path_escape_is_rejected(self) -> None:
        mirror, _ = self.create()
        (mirror / "keep.txt").write_text("mirror\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        plan["operations"][0]["path"] = "../escape.txt"
        plan.pop("plan_sha256")
        plan["plan_sha256"] = hashlib.sha256(ws._canonical(plan)).hexdigest()
        with self.assertRaisesRegex(ws.WorkspaceError, "canonical plan"):
            ws.apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        self.assertFalse((self.root / "escape.txt").exists())

    def test_symlink_source_is_rejected(self) -> None:
        target = self.root / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        try:
            (self.source / "link.txt").symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaisesRegex(ws.WorkspaceError, "links/reparse"):
            self.create()

    def test_crash_can_roll_back(self) -> None:
        mirror, _ = self.create()
        (mirror / "keep.txt").write_text("changed\n", encoding="utf-8")
        (mirror / "new.txt").write_text("new\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        with self.assertRaises(ws.SimulatedCrash):
            ws.apply_plan(
                mirror,
                plan,
                expected_sha256=plan["plan_sha256"],
                fault_after_operations=1,
            )
        ws.recover_workspace(mirror, action="rollback")
        self.assertEqual("one\n", (self.source / "keep.txt").read_text())
        self.assertFalse((self.source / "new.txt").exists())

    def test_crash_can_resume_from_staged_files(self) -> None:
        mirror, _ = self.create()
        (mirror / "keep.txt").write_text("changed\n", encoding="utf-8")
        (mirror / "new.txt").write_text("new\n", encoding="utf-8")
        plan = ws.plan_workspace(mirror, direction="back")
        with self.assertRaises(ws.SimulatedCrash):
            ws.apply_plan(
                mirror,
                plan,
                expected_sha256=plan["plan_sha256"],
                fault_after_operations=1,
            )
        # Change the live desired file; recovery must use the immutable staging copy.
        (mirror / "new.txt").write_text("later mutation\n", encoding="utf-8")
        ws.recover_workspace(mirror, action="resume")
        self.assertEqual("new\n", (self.source / "new.txt").read_text())
        self.assertEqual("later mutation\n", (mirror / "new.txt").read_text())
        self.assertFalse(ws.status_workspace(mirror)["converged"])

    def test_alias_exec_and_hash_mismatch(self) -> None:
        executable = Path(shutil.which("cmd.exe") or sys.executable).resolve()
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        mutable = self.root / "mutable-tool"
        mutable.write_bytes(b"first")
        mutable_digest = hashlib.sha256(mutable.read_bytes()).hexdigest()
        fixed_argv = (
            ["/d", "/c", "echo"]
            if executable.name.casefold() == "cmd.exe"
            else ["-c", "import sys; print(sys.argv[1])"]
        )
        mirror, _ = self.create(
            command_aliases={
                "python": {
                    "executable": str(executable),
                    "sha256": digest,
                    "fixed_argv": fixed_argv,
                },
                "mutable": {
                    "executable": str(mutable),
                    "sha256": mutable_digest,
                    "fixed_argv": [],
                },
            }
        )
        completed = ws.exec_alias(mirror, "python", ["hello"])
        self.assertEqual(0, completed.returncode)
        self.assertEqual("hello", completed.stdout.strip())
        mutable.write_bytes(b"second")
        with self.assertRaisesRegex(ws.WorkspaceError, "hash changed"):
            ws.exec_alias(mirror, "mutable")

    def test_close_and_cleanup_never_remove_source(self) -> None:
        mirror, result = self.create()
        closed = ws.close_workspace(mirror, archive=True)
        self.assertTrue(closed["closed"])
        self.assertFalse(mirror.exists())
        self.assertTrue((self.source / "keep.txt").exists())
        self.assertEqual(
            [result["workspace_id"]],
            ws.cleanup_workspaces(self.managed, [result["workspace_id"]]),
        )
        self.assertTrue(self.source.exists())
        self.assertEqual("one\n", (self.source / "keep.txt").read_text())

    @unittest.skipUnless(shutil.which("git"), "git unavailable")
    def test_git_detached_worktree_mode(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.source), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.source), "commit", "-qm", "fixture"], check=True
        )
        result = ws.create_workspace(self.source, self.managed, mode="git")
        mirror = Path(result["mirror"])
        self.assertEqual("git", result["mode"])
        self.assertTrue((mirror / ".git").is_file())
        self.assertTrue(ws.status_workspace(mirror)["clean"])
        ws.close_workspace(mirror)
        self.assertTrue((self.source / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
