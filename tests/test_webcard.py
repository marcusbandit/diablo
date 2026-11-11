import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestWebcard(unittest.TestCase):
    def test_it_parses_and_keeps_pages_away_from_local_files(self):
        source = (ROOT / "presence" / "webcard.py").read_text()
        ast.parse(source)
        self.assertIn("set_allow_file_access_from_file_urls(False)", source)
        self.assertIn("set_allow_universal_access_from_file_urls(False)", source)
        self.assertIn("load_html(read_page(page), None)", source)

    def test_shell_hands_it_the_spot_and_the_page(self):
        shell = (ROOT / "presence" / "shell.qml").read_text()
        self.assertIn('Quickshell.shellPath("webcard.py")', shell)
        self.assertIn("panel.pageArea", shell)
        self.assertIn("c.pageSlot", shell)


if __name__ == "__main__":
    unittest.main()
