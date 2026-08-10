import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class WorkspaceContractTests(unittest.TestCase):
    def test_gdext_revision_and_json_feature_are_exact(self) -> None:
        manifest = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn("637cef73172bba23850131acd8583b0c72ebf0c7", manifest)
        self.assertIn('features = ["api-custom-json"]', manifest)
        self.assertNotIn('features = ["api-custom"]', manifest)

    def test_exactly_one_extension_library_implementation_exists(self) -> None:
        rust_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in ROOT.glob("**/*.rs")
            if "target" not in path.parts
        )
        implementations = re.findall(r"impl\s+ExtensionLibrary\s+for\s+", rust_sources)
        self.assertEqual(len(implementations), 1)


if __name__ == "__main__":
    unittest.main()
