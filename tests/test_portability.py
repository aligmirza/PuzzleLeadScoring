"""Windows and macOS: every text file is read and written as UTF-8 (Windows would otherwise use cp1252 and crash
on quotes such as ’ or —)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TextFilesAreUtf8(unittest.TestCase):
    def test_every_text_open_sets_the_encoding(self):
        missing = []
        for p in sorted((ROOT / "enrich").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py")):
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                if name not in ("open", "read_text", "write_text") or any(k.arg == "encoding" for k in n.keywords):
                    continue
                mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else ""
                mode = next((k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode)
                if "b" in mode or (isinstance(f, ast.Attribute) and ast.unparse(f.value) == "z"):  # binary, zipfile
                    continue
                missing.append(f"{p.relative_to(ROOT)}:{n.lineno}")
        self.assertEqual(missing, [])
