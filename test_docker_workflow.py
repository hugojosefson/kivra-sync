"""Test Docker arguments with a synthetic CLI, without a daemon."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class DockerWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = Path(__file__).parent / "scripts/export-local.sh"
        self.environment = {
            **os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "KIVRA_EXPORT_DIR": str(self.root / "synthetic export"),
            "TEST_ROOTLESS": "0", "TEST_ARGUMENTS": str(self.root / "arguments"),
        }

    def invoke(self, *arguments):
        # Bash functions work on noexec temporary mounts.
        setup = r"""
docker() {
    if [[ "${1}" == "info" ]]; then
        if [[ "${TEST_ROOTLESS}" == "1" ]]; then
            printf '%s\n' '["name=rootless"]'
        else
            printf '%s\n' '[]'
        fi
    else
        printf '%s\0' "${@}" > "${TEST_ARGUMENTS}"
    fi
}
exec() { "${@}"; }
source "${1}" "${@:2}"
"""
        subprocess.run(["bash", "-c", setup, "synthetic-workflow", str(self.script), *arguments],
                       env=self.environment, check=True, capture_output=True)
        return [value.decode() for value in
                (self.root / "arguments").read_bytes().split(b"\0")[:-1]]

    def test_rootful_export_uses_host_uid_and_private_directory(self):
        args = self.invoke()
        self.assertEqual(args[args.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertIn("-it", args)
        self.assertEqual(args[args.index("--log-driver") + 1], "none")
        self.assertIn("--read-only", args)
        self.assertEqual((self.root / "synthetic export").stat().st_mode & 0o777, 0o700)

    def test_rootless_export_uses_mapped_host_user(self):
        self.environment["TEST_ROOTLESS"] = "1"
        args = self.invoke()
        self.assertEqual(args[args.index("--user") + 1], "0:0")

    def test_verification_has_no_network_and_mounts_data_read_only(self):
        (self.root / "synthetic export").mkdir()
        args = self.invoke("--verify-only")
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertNotIn("-it", args)
        self.assertTrue(args[args.index("-v") + 1].endswith(":/data:ro"))
        self.assertEqual(args[-1], "--verify-only")


if __name__ == "__main__":
    unittest.main()
