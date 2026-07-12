from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from support import PACKAGE_ROOT, create_baseline, git, install_project, run


BIN = PACKAGE_ROOT / "payload/.codex-workflow/bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

from workflow_common import canonical_delivery, sha256_json  # noqa: E402
from workflow_paths import WorkflowPaths  # noqa: E402


class CanonicalDeliveryTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _stage_symlink(self, target: Path, relative: str, link_target: str) -> str:
        """Create a Git symlink entry without relying on Windows symlink privileges."""

        blob = run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=target,
            input_text=link_target,
        )
        self._assert_ok(blob)
        oid = blob.stdout.strip()
        self._assert_ok(
            run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{oid},{relative}"],
                cwd=target,
            )
        )
        return oid

    def _commit(self, target: Path, message: str) -> str:
        self._assert_ok(run(["git", "commit", "-m", message], cwd=target))
        return git(target, "rev-parse", "HEAD").stdout.strip()

    def _delivery_fixture(self, target: Path) -> tuple[str, str, str, str]:
        self._assert_ok(install_project(target))
        create_baseline(target)

        files = {
            "assets/payload.bin": b"\x00\xff\x10base\x00",
            "bin/tool.sh": b"#!/bin/sh\necho tool\n",
            "docs/guide.txt": b"base guide\n",
            "docs/rename-source.txt": b"rename me\n",
        }
        for relative, content in files.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self._assert_ok(
            run(
                ["git", "add", "--", *sorted(files)],
                cwd=target,
            )
        )
        old_link_oid = self._stage_symlink(target, "links/current", "../release-v1")
        base = self._commit(target, "canonical fixture base")

        # Explicitly enable Git's rename heuristic; the delivery algorithm must
        # still force a delete+add representation with --no-renames.
        self._assert_ok(run(["git", "config", "diff.renames", "true"], cwd=target))
        (target / "assets/payload.bin").write_bytes(b"\x00\xff\x10target\x01")
        (target / "docs/guide.txt").write_text("target guide\n", encoding="utf-8")
        self._assert_ok(
            run(
                ["git", "add", "--", "assets/payload.bin", "docs/guide.txt"],
                cwd=target,
            )
        )
        self._assert_ok(
            run(
                ["git", "update-index", "--chmod=+x", "--", "bin/tool.sh"],
                cwd=target,
            )
        )
        self._assert_ok(
            run(
                ["git", "mv", "docs/rename-source.txt", "docs/renamed-target.txt"],
                cwd=target,
            )
        )
        new_link_oid = self._stage_symlink(target, "links/current", "../release-v2")
        return base, self._commit(target, "canonical fixture target"), old_link_oid, new_link_oid

    def test_fixture_covers_text_binary_mode_symlink_and_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            base, result, old_link_oid, new_link_oid = self._delivery_fixture(target)
            delivery = canonical_delivery(WorkflowPaths.discover(target), base, result)

        entries = delivery["entries"]
        self.assertEqual(
            [(entry["status"], entry["new_path"]) for entry in entries],
            [
                ("M", "assets/payload.bin"),
                ("M", "bin/tool.sh"),
                ("M", "docs/guide.txt"),
                ("D", "docs/rename-source.txt"),
                ("A", "docs/renamed-target.txt"),
                ("M", "links/current"),
            ],
        )
        self.assertEqual(delivery["base_commit"], base)
        self.assertEqual(delivery["target_commit"], result)
        self.assertEqual(
            delivery["changed_paths"],
            [
                "assets/payload.bin",
                "bin/tool.sh",
                "docs/guide.txt",
                "docs/rename-source.txt",
                "docs/renamed-target.txt",
                "links/current",
            ],
        )
        self.assertTrue(all("\\" not in entry["new_path"] for entry in entries))

        by_path = {entry["new_path"]: entry for entry in entries}
        self.assertNotEqual(by_path["assets/payload.bin"]["old_oid"], by_path["assets/payload.bin"]["new_oid"])
        self.assertEqual(by_path["assets/payload.bin"]["old_mode"], "100644")
        self.assertEqual(by_path["assets/payload.bin"]["new_mode"], "100644")
        self.assertEqual(by_path["bin/tool.sh"]["old_oid"], by_path["bin/tool.sh"]["new_oid"])
        self.assertEqual(by_path["bin/tool.sh"]["old_mode"], "100644")
        self.assertEqual(by_path["bin/tool.sh"]["new_mode"], "100755")
        self.assertNotEqual(by_path["docs/guide.txt"]["old_oid"], by_path["docs/guide.txt"]["new_oid"])

        added = by_path["docs/renamed-target.txt"]
        deleted = by_path["docs/rename-source.txt"]
        self.assertTrue(set(added["old_oid"]) == {"0"})
        self.assertTrue(set(deleted["new_oid"]) == {"0"})
        self.assertEqual(added["new_path"], "docs/renamed-target.txt")
        self.assertEqual(deleted["old_path"], "docs/rename-source.txt")

        symlink = by_path["links/current"]
        self.assertEqual(symlink["old_mode"], "120000")
        self.assertEqual(symlink["new_mode"], "120000")
        self.assertEqual(symlink["old_oid"], old_link_oid)
        self.assertEqual(symlink["new_oid"], new_link_oid)
        self.assertEqual(symlink["old_symlink_target"], "../release-v1")
        self.assertEqual(symlink["new_symlink_target"], "../release-v2")

        self.assertEqual(
            delivery["patch_hash"],
            sha256_json({"algorithm": "codex-delta-v1", "entries": entries}),
        )
        self.assertEqual(
            delivery["delivery_hash"],
            sha256_json({"algorithm": "codex-delta-v1", "base_commit": base, "entries": entries}),
        )

    def test_merge_conflict_fixture_hashes_the_resolved_tree_not_parent_patches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            create_baseline(target)
            conflict = target / "src" / "conflict.txt"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("base\n", encoding="utf-8")
            self._assert_ok(run(["git", "add", "--", "src/conflict.txt"], cwd=target))
            base = self._commit(target, "conflict fixture base")
            primary = git(target, "branch", "--show-current").stdout.strip()

            self._assert_ok(run(["git", "switch", "-c", "feature-conflict"], cwd=target))
            conflict.write_text("feature change\n", encoding="utf-8")
            self._assert_ok(run(["git", "add", "--", "src/conflict.txt"], cwd=target))
            self._commit(target, "feature side")

            self._assert_ok(run(["git", "switch", primary], cwd=target))
            conflict.write_text("primary change\n", encoding="utf-8")
            self._assert_ok(run(["git", "add", "--", "src/conflict.txt"], cwd=target))
            self._commit(target, "primary side")
            conflicted = run(["git", "merge", "--no-ff", "feature-conflict"], cwd=target)
            self.assertNotEqual(conflicted.returncode, 0)
            conflict.write_text("resolved result\n", encoding="utf-8")
            self._assert_ok(run(["git", "add", "--", "src/conflict.txt"], cwd=target))
            merged = self._commit(target, "resolve conflict")

            self._assert_ok(run(["git", "switch", "-c", "direct-resolution", base], cwd=target))
            conflict.write_text("resolved result\n", encoding="utf-8")
            self._assert_ok(run(["git", "add", "--", "src/conflict.txt"], cwd=target))
            direct = self._commit(target, "direct resolved result")

            paths = WorkflowPaths.discover(target)
            merged_delivery = canonical_delivery(paths, base, merged)
            direct_delivery = canonical_delivery(paths, base, direct)

        self.assertNotEqual(merged, direct)
        self.assertEqual(merged_delivery["entries"], direct_delivery["entries"])
        self.assertEqual(merged_delivery["patch_hash"], direct_delivery["patch_hash"])
        self.assertEqual(merged_delivery["delivery_hash"], direct_delivery["delivery_hash"])
        self.assertEqual(merged_delivery["changed_paths"], ["src/conflict.txt"])
        entry = merged_delivery["entries"][0]
        self.assertEqual(entry["status"], "M")
        self.assertEqual(entry["old_path"], "src/conflict.txt")
        self.assertEqual(entry["new_path"], "src/conflict.txt")
        self.assertNotEqual(entry["old_oid"], entry["new_oid"])


if __name__ == "__main__":
    unittest.main()
