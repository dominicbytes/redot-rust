# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tests.binary_fixtures import elf_binary, pe_binary
from tools.package_release import PackageError, create_package


class PackageBinaryTargetTests(unittest.TestCase):
    def test_wrong_platform_binary_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target, data in [
                ("linux-x86_64", pe_binary("KERNEL32.dll")),
                ("windows-x86_64", elf_binary()),
            ]:
                with self.subTest(target=target):
                    binary = root / "fixture.bin"
                    binary.write_bytes(data)
                    output = root / target
                    with self.assertRaisesRegex(PackageError, "binary audit failed"):
                        create_package(Path(__file__).resolve().parents[1], target, binary, binary, output)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
