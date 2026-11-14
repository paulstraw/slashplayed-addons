# Slashplayed addon catalog

The World of Warcraft addons [Slashplayed](https://github.com/paulstraw/slashplayed) can install and
update on an iPad, iPhone, or Vision Pro, published hourly as
[`addons.json`](https://paulstraw.github.io/slashplayed-addons/addons.json).

Nothing here hosts an addon. Each entry names where the addon's author publishes it, and the device
downloads the author's own file from there. The catalog only records what each file is: its version,
which games it's for, the folders it installs, what it needs, its size, and its SHA-256.

## Adding an addon

Add `addons/<id>.json`, where `<id>` is a short lowercase name, with the addon's name and a recipe:

```json
{"name": "Deadly Boss Mods", "recipe": {"type": "github-release", "repo": "DeadlyBossMods/DeadlyBossMods"}}
```

The recipes:

| `type` | Fields | Use for |
| --- | --- | --- |
| `github-release` | `repo`, and optionally `asset` (a regular expression for the zip's name) | Addons that attach zips to GitHub releases. The BigWigs packager's `release.json` says which games each zip is for; without one, the zip's TOCs do. `asset` picks one zip when several overlap. |
| `github-tag` | `repo` | Repos with tags but no release files. Only when the tag holds the whole addon: a `.pkgmeta` with `externals` or `move-folders`, or git submodules, means the packager adds files the tag doesn't have (its libraries, mostly), so such a repo is refused. |
| `tukui` | `slug` | ElvUI and Tukui, from `api.tukui.org`. |
| `wowinterface` | `id` (the number in the addon's WoWInterface URL) | Addons published on WoWInterface. Its MD5 is checked. |

Then check it builds:

```sh
GITHUB_TOKEN=$(gh auth token) python3 build.py --out /tmp/catalog --only <id>
```

A pull request runs the tests and the whole build without deploying.

An addon is offered for a game only when one of its TOCs (or its `release.json`) names an Interface
number in that game's current expansion (`FLAVORS` in `build.py`, also in the index as `flavors`). A
file built for an older expansion usually doesn't load, so it isn't offered. When a game moves to a
new expansion, change its range there.

## Not covered

Addons only on CurseForge or Wago can't be here: both need an API key, which Slashplayed doesn't use.
Checked on 2026-11-14:

- **Not found on GitHub or WoWInterface** (so likely CurseForge or Wago only): Auctionator, Baganator,
  Syndicator, Platynator, Chattynator, and BugGrabber.
- **Source that isn't the whole addon**: Details (`.pkgmeta` externals), Bagnon (git submodules).
- **No current release**: Altoholic (no tags), Hekili (last release for Retail 11.2), AtlasLootClassic
  (2023), Postal (WoWInterface, 2020).

## addons.json

```jsonc
{
  "schema": 1,
  "generated": "2026-11-14T04:17:00Z",
  "flavors": {"retail": [120000, 129999], "...": []},  // the ranges above
  "addons": [{
    "id": "grid2", "name": "Grid2", "notes": "…", "home": "https://github.com/michaelnpsp/Grid2",
    "recipe": {"type": "github-release", "repo": "michaelnpsp/Grid2"},
    "version": "4.1.11", "published": "2026-11-09",
    "stale": true,  // only when this run's recipe failed: the last good data, kept
    "files": [{
      "name": "Grid2-4.1.11.zip", "url": "https://github.com/…", "size": 846278, "sha256": "…",
      "flavors": ["retail"],          // Slashplayed's game ids; each game is in at most one file
      "interfaces": [120105],
      "folders": [{"name": "Grid2", "path": "Grid2/"}],  // each zip path prefix goes to AddOns/<name>
      "requires": []                  // required dependencies the zip doesn't hold
    }]
  }]
}
```

`schema` changes only when a change would break an app that reads the current one.

## Tests

```sh
python3 -m unittest discover -s tests
```

They use zips the tests make, never an addon's real files.
