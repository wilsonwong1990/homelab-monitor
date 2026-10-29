import importlib.util
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("next_version", ROOT / "scripts/next-version.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
choose_version = module.choose_version
A, B = "a" * 40, "b" * 40


class VersionTests(unittest.TestCase):
    def test_initial_and_semantic_patch(self):
        self.assertEqual(choose_version("", A), ("v0.1.0", "new"))
        self.assertEqual(choose_version("\n", A), ("v0.1.0", "new"))
        remote = f"{B}\trefs/tags/v0.9.9\n{B}\trefs/tags/v0.10.0\n{B}\trefs/tags/v9.0.0-rc1\n"
        self.assertEqual(choose_version(remote, A), ("v0.10.1", "new"))

    def test_annotated_tags_are_peeled_and_reused(self):
        remote = f"{B}\trefs/tags/v1.2.3\n{A}\trefs/tags/v1.2.3^{{}}\n"
        self.assertEqual(choose_version(remote, A), ("v1.2.3", "existing"))
        self.assertEqual(choose_version(remote, B), ("v1.2.4", "new"))
        with self.assertRaises(ValueError):
            choose_version(remote, "main")

    def test_real_remote_push_rerun_and_concurrent_merges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, repo = root / "origin.git", root / "work"

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(repo), *args], stderr=subprocess.PIPE, text=True
                ).strip()

            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            git("config", "user.name", "Release test")
            git("config", "user.email", "ci@example.invalid")
            git("remote", "add", "origin", str(remote))
            commits = []
            for number in range(3):
                git("commit", "--allow-empty", "-qm", f"test {number}")
                commits.append(git("rev-parse", "HEAD"))

            def tag(commit):
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/tag-release.sh"), commit],
                    cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            with ThreadPoolExecutor(max_workers=2) as pool:
                versions = list(pool.map(tag, commits[:2]))
            self.assertEqual(set(versions), {"v0.1.0", "v0.1.1"})
            self.assertEqual(tag(commits[0]), versions[0])
            self.assertEqual(tag(commits[1]), versions[1])
            self.assertEqual(tag(commits[2]), "v0.1.2")
            refs = git("ls-remote", "--tags", "origin")
            self.assertEqual(len(refs.splitlines()), 3)
            for commit, version in zip(commits[:2], versions):
                self.assertIn(f"{commit}\trefs/tags/{version}", refs)
