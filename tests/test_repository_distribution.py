"""Verify publication scope before staging and reject local-only file selections."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import verify_repository_distribution as distribution


class RepositoryDistributionTests(unittest.TestCase):
    def candidates(self, root, tracked=(), additional=(), ignored=()):
        responses = {
            ("ls-files", "-z"): "\0".join(tracked).encode(),
            ("ls-files", "--others", "--exclude-standard", "-z"): "\0".join(additional).encode(),
            ("ls-files", "-ci", "--exclude-standard"): "\n".join(ignored).encode(),
        }
        with patch.object(distribution, "ROOT", root), patch.object(distribution, "git", side_effect=lambda *args: responses[args]):
            return distribution.publication_candidates()

    def create_file(self, root, name):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")

    def test_pending_removal_does_not_block_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_file(root, "finevolab/example.py")
            candidates, ignored = self.candidates(root, tracked=["upstream/README.md"],
                additional=["finevolab/example.py"], ignored=["upstream/README.md"])
            self.assertEqual(candidates, ["finevolab/example.py"])
            self.assertEqual(ignored, [])

    def test_existing_ignored_tracked_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_file(root, "local.txt")
            with self.assertRaisesRegex(ValueError, "remain tracked"):
                self.candidates(root, tracked=["local.txt"], ignored=["local.txt"])

    def test_untracked_financial_snapshots_are_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "data/raw/report.html"
            self.create_file(root, name)
            self.assertEqual(self.candidates(root, additional=[name])[0], [name])

    def test_provider_snapshots_and_private_configuration_are_rejected(self):
        for name, message in (("data/raw/fmp/response.json", "local-only"),
                              ("finrobot_equity/core/config/config.ini", "private configuration")):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.create_file(root, name)
                with self.assertRaisesRegex(ValueError, message):
                    self.candidates(root, additional=[name])
