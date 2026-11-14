"""build.py's zip reading, game ranges, and file choice, on zips made here (no addon's real files)."""

import io
import pathlib
import sys
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import build  # noqa: E402


def make_zip(files, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


class ReadZip(unittest.TestCase):
    def test_folders_interfaces_and_outside_dependencies(self):
        held = build.read_zip(make_zip({
            "README.md": "ignored",
            "Foo/Foo.toc": "## Interface: 120105\n## Notes: |cff00ff00Does foo|r\n## RequiredDeps: Foo_Lib, Bar\n",
            "Foo/Foo_Vanilla.toc": "## Interface: 11509\n",
            "Foo_Lib/Foo_Lib.toc": "## Interface: 120105, 11509\n## DepsExtra: Baz\n",
            "Libs/readme.txt": "a folder with no TOC named after it isn't an addon",
        }))
        self.assertEqual([f["name"] for f in held["folders"]], ["Foo", "Foo_Lib"])
        self.assertEqual(held["interfaces"], [11509, 120105])
        # Foo_Lib is in the zip, so only Bar and Baz are needed from elsewhere.
        self.assertEqual(held["requires"], ["Bar", "Baz"])
        self.assertEqual(held["notes"], "Does foo")

    def test_zipball_whose_root_is_the_addon(self):
        held = build.read_zip(make_zip({
            "angry-keystones-0.33.2/AngryKeystones.toc": "## Interface: 120100\n",
            "angry-keystones-0.33.2/AngryKeystones_Mists.toc": "## Interface: 50504\n",
            "angry-keystones-0.33.2/Core.lua": "",
        }), root_addon=True)
        self.assertEqual(held["folders"], [{"name": "AngryKeystones", "path": "angry-keystones-0.33.2/"}])

    def test_unsafe_entries_are_refused(self):
        # Each next to a good addon, so it's the bad entry that's refused, not an empty zip.
        good = {"Ok/Ok.toc": "## Interface: 120105\n"}
        for bad in ("../Foo/Foo.toc", "/Foo/Foo.toc", "Foo/./Foo.toc", "Foo\\..\\Foo.toc"):
            with self.assertRaisesRegex(build.RecipeError, "unsafe path", msg=bad):
                build.read_zip(make_zip({**good, bad: ""}))
        with self.assertRaisesRegex(build.RecipeError, "symlink"):
            build.read_zip(make_zip(good, symlink="Ok/link"))


class Ranges(unittest.TestCase):
    def test_edges(self):
        # TBC Classic's last (20504) isn't Anniversary's 2.5.5; Retail 11.x isn't Midnight's 12.0.
        self.assertEqual(build.flavors_for([20504, 110002]), [])
        self.assertEqual(build.flavors_for([20505]), ["anniversary"])
        self.assertEqual(build.flavors_for([120000]), ["retail"])
        self.assertEqual(build.flavors_for([16001, 11509, 50504]), ["classic", "classic_era", "forever"])


class FileChoice(unittest.TestCase):
    def build_with(self, zips):
        """build_entry on a release of these zips, through a recipe that serves them from memory."""
        build.RECIPES["test"] = lambda r: ("1.0", "2026-11-14", "https://example.invalid",
                                           [{"name": n, "url": n} for n in zips])
        fetch, build.fetch = build.fetch, lambda url, **_: zips[url]
        try:
            entry, _ = build.build_entry("foo", {"name": "Foo", "recipe": {"type": "test"}}, None)
        finally:
            build.fetch = fetch
            del build.RECIPES["test"]
        return {f["name"]: f["flavors"] for f in entry["files"]}

    def test_per_game_zip_wins_its_game_from_a_universal_one(self):
        universal = make_zip({"Foo/Foo.toc": "## Interface: 120105, 11509, 50504\n"})
        vanilla = make_zip({"Foo/Foo.toc": "## Interface: 11509\n"})
        self.assertEqual(self.build_with({"Foo.zip": universal, "Foo-Vanilla.zip": vanilla}),
                         {"Foo.zip": ["classic", "retail"], "Foo-Vanilla.zip": ["classic_era"]})

    def test_equally_specific_zips_are_refused(self):
        same = make_zip({"Foo/Foo.toc": "## Interface: 120105\n"})
        with self.assertRaises(build.RecipeError):
            self.build_with({"Foo-a.zip": same, "Foo-b.zip": same})


if __name__ == "__main__":
    unittest.main()
