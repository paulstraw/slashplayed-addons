#!/usr/bin/env python3
"""Builds addons.json, the catalog Slashplayed's device app reads, from the entries in addons/.

Each entry names an addon and a recipe: where its author publishes it. This never builds or hosts an
addon's files: the index only points at the files the authors published (a GitHub release's assets, a
tag's zipball, Tukui's or WoWInterface's download), and the device downloads them from there. Each file
is downloaded here once per version, to read what it holds (its folders, their TOCs' Interface numbers
and dependencies) and to record its size and SHA-256, so the device can check what it gets.

Standard library only, so the workflow needs no installs:

    python3 build.py --out public [--previous URL-or-path] [--only ID ...]

GITHUB_TOKEN, if set, is sent to api.github.com (the workflow's token allows 1,000 requests an hour;
without one it's 60). --previous is the last published addons.json: an entry whose version and files
haven't changed is copied from it without downloading anything, and one whose recipe fails this run
keeps its last good data, marked stale, instead of dropping out of the catalog.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
SCHEMA = 1
USER_AGENT = "slashplayed-addons (+https://github.com/paulstraw/slashplayed-addons)"
MAX_ZIP = 200 * 1024 * 1024  # bigger than any addon; a recipe that finds something this big is wrong

# Each game Slashplayed plays (its flavors.plist ids) and the Interface numbers of its current
# expansion. A file counts for a game only when one of its TOCs, or its release.json, names an Interface
# in that range: an addon last released for an older expansion of the same game (Retail's 11.x before
# 12.0, say) usually doesn't load, so it isn't offered. Change a range when its game moves to a new
# expansion, as flavors.plist's toc changes with each patch.
FLAVORS = {
    "classic_era": range(11500, 12000),
    "forever": range(16000, 17000),
    "anniversary": range(20505, 20600),  # 2.5.5 on; TBC Classic (2021) was 2.5.1 to 2.5.4, 20501 to 20504
    "classic": range(50500, 50600),  # MoP Classic, "_classic_", not Classic Era
    "retail": range(120000, 130000),
}

# A folder name WoW would load and that's safe to make on the device: no separators, no dot names.
FOLDER = re.compile(r"^[A-Za-z0-9!][A-Za-z0-9 _.!+-]*$")
COLOR = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")


class RecipeError(Exception):
    pass


def fetch(url, *, accept=None, api=False, limit=MAX_ZIP, body=None):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if api and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(url, headers=headers, data=body)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise RecipeError(f"{url}: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RecipeError(f"{url}: {e.reason}") from None
    if len(data) > limit:
        raise RecipeError(f"{url}: more than {limit} bytes")
    return data


def github(path, **kwargs):
    return json.loads(fetch("https://api.github.com/" + path, accept="application/vnd.github+json", api=True, **kwargs))


def flavors_for(interfaces):
    return sorted(f for f, r in FLAVORS.items() if any(i in r for i in interfaces))


def toc_fields(data):
    """A TOC's ## lines, keys lowercased. Interface can be a comma list (one TOC for several games)."""
    text = data.decode("utf-8", "replace").lstrip("\ufeff")
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^##\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
        if m and m.group(1).lower() not in fields:
            fields[m.group(1).lower()] = m.group(2)
    return fields


def numbers(text):
    return [int(n) for n in re.findall(r"\d+", text or "")]


def read_zip(data, *, root_addon=False):
    """The addon folders in an addon zip, as {"name": folder in AddOns/, "path": its prefix in the zip},
    with every TOC's Interface numbers, the dependencies outside the zip, and the first Notes found.

    An addon zip holds one folder per addon, each with a TOC named after it (Folder/Folder.toc, or
    Folder/Folder_Mainline.toc and the like). A tag's zipball (root_addon) instead holds one folder,
    named after the repo and commit, which is either the addon itself (TOCs at its root: the folder
    is named after the shortest TOC name) or a set of addon folders. Top-level files (a README) are
    ignored. Any path that could land outside the folder it names is refused, as the device refuses it.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RecipeError("not a zip") from None
    names = []
    for info in z.infolist():
        n = info.filename
        if n.startswith("/") or "\\" in n or any(p in ("..", ".") for p in n.split("/")) or ":" in n:
            raise RecipeError(f"unsafe path in zip: {n!r}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise RecipeError(f"symlink in zip: {n!r}")
        names.append(n)

    base = ""
    if root_addon:
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) != 1:
            raise RecipeError("zipball without one top folder")
        base = tops.pop() + "/"
        names = [n for n in names if n.startswith(base)]

    def tocs_in(prefix):
        return [n for n in names if n.startswith(prefix) and n.endswith(".toc") and "/" not in n[len(prefix):]]

    folders = []
    root_tocs = tocs_in(base)
    if root_addon and root_tocs:
        stems = sorted((pathlib.PurePosixPath(t).stem for t in root_tocs), key=len)
        if not all(s.startswith(stems[0]) for s in stems):
            raise RecipeError(f"TOCs at the zipball's root name different addons: {stems}")
        folders.append({"name": stems[0], "path": base})
    else:
        for top in sorted({n[len(base):].split("/", 1)[0] for n in names if "/" in n[len(base):]}):
            prefix = base + top + "/"
            if any(pathlib.PurePosixPath(t).stem.startswith(top) for t in tocs_in(prefix)):
                folders.append({"name": top, "path": prefix})
    if not folders:
        raise RecipeError("no addon folders in zip")
    for f in folders:
        if not FOLDER.match(f["name"]):
            raise RecipeError(f"bad folder name {f['name']!r}")

    interfaces, deps, notes = set(), set(), None
    for f in folders:
        for toc in tocs_in(f["path"]):
            fields = toc_fields(z.read(toc))
            interfaces.update(numbers(fields.get("interface")))
            for key, value in fields.items():
                # WoW reads Dependencies, RequiredDeps, and any field starting with Dep as required.
                if key in ("dependencies", "requireddeps") or key.startswith("dep"):
                    deps.update(d.strip() for d in value.split(",") if d.strip())
            if notes is None and fields.get("notes"):
                notes = COLOR.sub("", fields["notes"]).strip() or None
    inside = {f["name"].lower() for f in folders}
    requires = sorted({d for d in deps if d.lower() not in inside}, key=str.lower)
    return {"folders": folders, "interfaces": sorted(interfaces), "requires": requires, "notes": notes}


# The recipes. Each returns (version, published date, home page, [candidate files]), where a candidate is
# {"url", "name", and optionally "interfaces" when the source says them (release.json)}.

def recipe_github_release(r):
    repo = r["repo"]
    release = github(f"repos/{repo}/releases/latest")
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    files = []
    if "release.json" in assets:
        # The BigWigs packager's list: each zip and the games (flavor, Interface) it's for. nolib zips
        # leave out the libraries the addon embeds, for people who install those separately.
        meta = json.loads(fetch(assets["release.json"], limit=1 << 20))
        for rel in meta.get("releases", []):
            if rel.get("nolib") or rel.get("filename") not in assets:
                continue
            files.append({"name": rel["filename"], "url": assets[rel["filename"]],
                          "interfaces": [m["interface"] for m in rel.get("metadata", []) if isinstance(m.get("interface"), int)]})
    else:
        for name, url in sorted(assets.items()):
            if name.lower().endswith(".zip") and "nolib" not in name.lower():
                files.append({"name": name, "url": url})
    # An entry's "asset" pattern picks among the zips, for a release whose zips overlap (TipTac's five
    # hold the same files, each with every game's TOC).
    if r.get("asset"):
        files = [f for f in files if re.fullmatch(r["asset"], f["name"])]
    if not files:
        raise RecipeError(f"{repo} {release.get('tag_name')}: no zip assets")
    return release["tag_name"], release["published_at"][:10], f"https://github.com/{repo}", files


def recipe_github_tag(r):
    """A repo that publishes tags but no release files: its newest tag's zipball, only when the source is
    the whole addon. A .pkgmeta with externals or moved folders, or git submodules, means the packager
    assembles the addon from more than the tag holds (its libraries, mostly), so the zipball would be
    missing pieces: such a recipe is refused rather than offered broken."""
    repo = r["repo"]
    owner, name = repo.split("/")
    query = {"query": "query($o:String!,$n:String!){repository(owner:$o,name:$n){refs(refPrefix:\"refs/tags/\",first:1,"
                      "orderBy:{field:TAG_COMMIT_DATE,direction:DESC}){nodes{name target{...on Commit{committedDate}"
                      "...on Tag{target{...on Commit{committedDate}}}}}}}}",
             "variables": {"o": owner, "n": name}}
    if not os.environ.get("GITHUB_TOKEN"):
        raise RecipeError("github-tag needs GITHUB_TOKEN (GraphQL)")
    reply = json.loads(fetch("https://api.github.com/graphql", api=True, body=json.dumps(query).encode()))
    nodes = (((reply.get("data") or {}).get("repository") or {}).get("refs") or {}).get("nodes") or []
    if not nodes:
        raise RecipeError(f"{repo}: no tags")
    tag = nodes[0]["name"]
    target = nodes[0]["target"]
    date = (target.get("committedDate") or (target.get("target") or {}).get("committedDate") or "")[:10]
    ref = urllib.parse.quote(tag, safe="")
    for path in (".gitmodules", ".pkgmeta", "pkgmeta.yaml", ".pkgmeta.yaml"):
        try:
            text = fetch(f"https://raw.githubusercontent.com/{repo}/{ref}/{path}", limit=1 << 20).decode("utf-8", "replace")
        except RecipeError:
            continue
        if path == ".gitmodules":
            raise RecipeError(f"{repo} {tag}: git submodules, which a zipball leaves out")
        if re.search(r"^\s*(externals|move-folders)\s*:", text, re.M):
            raise RecipeError(f"{repo} {tag}: {path} has externals or move-folders, so the zipball is incomplete")
    return tag, date, f"https://github.com/{repo}", [{"name": f"{name}-{tag}.zip", "root_addon": True,
                                                      "url": f"https://codeload.github.com/{repo}/zip/refs/tags/{ref}"}]


def recipe_tukui(r):
    a = json.loads(fetch(f"https://api.tukui.org/v1/addon/{urllib.parse.quote(r['slug'])}", limit=1 << 20))
    return a["version"], (a.get("last_update") or "")[:10], a.get("web_url") or "https://tukui.org", [
        {"name": f"{a['name']}-{a['version']}.zip", "url": a["url"]}]


def recipe_wowinterface(r):
    d = json.loads(fetch(f"https://api.mmoui.com/v4/game/WOW/filedetails/{int(r['id'])}.json", limit=1 << 20))[0]
    date = datetime.datetime.fromtimestamp(d["lastUpdate"] / 1000, datetime.timezone.utc).date().isoformat()
    return d["version"], date, f"https://www.wowinterface.com/downloads/info{int(r['id'])}", [
        {"name": d.get("fileName") or f"{r['id']}.zip", "url": d["downloadUri"], "md5": d.get("checksum")}]


RECIPES = {"github-release": recipe_github_release, "github-tag": recipe_github_tag,
           "tukui": recipe_tukui, "wowinterface": recipe_wowinterface}


def build_entry(addon_id, entry, previous):
    recipe = entry["recipe"]
    version, published, home, candidates = RECIPES[recipe["type"]](recipe)
    urls = [c["url"] for c in candidates]
    if previous and previous.get("recipe") == recipe and previous.get("version") == version \
            and [f["url"] for f in previous.get("files", [])] == urls:
        kept = dict(previous)
        kept.pop("stale", None)
        return kept, False
    files, notes = [], None
    for c in candidates:
        data = fetch(c["url"])
        if c.get("md5") and hashlib.md5(data).hexdigest() != c["md5"].lower():
            raise RecipeError(f"{c['name']}: MD5 isn't the one the source lists")
        held = read_zip(data, root_addon=c.get("root_addon", False))
        # release.json says which games each zip is for; otherwise its TOCs do.
        interfaces = sorted(set(c.get("interfaces") or held["interfaces"]))
        flavors = flavors_for(interfaces)
        notes = notes or held["notes"]
        if flavors:
            files.append({"name": c["name"], "url": c["url"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "flavors": flavors, "interfaces": interfaces, "folders": held["folders"], "requires": held["requires"]})
    if not files:
        raise RecipeError(f"{version}: no file for any game's current expansion")
    # The device installs the one file that lists its game, so each game goes to one file: the one made for
    # the fewest games, since a release with a universal zip and one per game (TipTac) means the per-game
    # one for that game. A tie between equally specific files is a release this can't read.
    for flavor in FLAVORS:
        claiming = sorted((len(f["flavors"]), i) for i, f in enumerate(files) if flavor in f["flavors"])
        if len(claiming) > 1 and claiming[0][0] == claiming[1][0]:
            raise RecipeError(f"{version}: {len(claiming)} files equally for {flavor}")
        for _, i in claiming[1:]:
            files[i]["won"] = files[i].get("won", set(files[i]["flavors"])) - {flavor}
    for f in files:
        f["flavors"] = sorted(f.pop("won", f["flavors"]))
    files = [f for f in files if f["flavors"]]
    out = {"id": addon_id, "name": entry["name"], "notes": entry.get("notes") or notes, "home": home,
           "recipe": recipe, "version": version, "published": published, "files": files}
    return {k: v for k, v in out.items() if v is not None}, True


def load_previous(where):
    if not where:
        return {}
    try:
        data = fetch(where, limit=64 << 20) if re.match(r"https?://", where) else pathlib.Path(where).read_bytes()
        return {a["id"]: a for a in json.loads(data).get("addons", [])}
    except (RecipeError, OSError, ValueError) as e:
        print(f"previous index: {e}; building every entry fresh", file=sys.stderr)
        return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--previous")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    previous = load_previous(args.previous)
    addons, failed, fresh, total = [], [], 0, 0
    for path in sorted((ROOT / "addons").glob("*.json")):
        addon_id = path.stem
        if args.only and addon_id not in args.only:
            continue
        entry = json.loads(path.read_text())
        total += 1
        try:
            built, new = build_entry(addon_id, entry, previous.get(addon_id))
            fresh += new
            addons.append(built)
            print(f"{addon_id}: {built['version']}{' (new)' if new else ''}, "
                  f"{', '.join(f['name'] + ' ' + '/'.join(f['flavors']) for f in built['files'])}")
        except (RecipeError, KeyError, ValueError) as e:
            failed.append(addon_id)
            print(f"::warning::{addon_id}: {e}", file=sys.stderr)
            if addon_id in previous:
                addons.append(dict(previous[addon_id], stale=True))

    # Half the catalog failing is the job's problem (a network outage, an API change), not the addons':
    # stop, so the last good index stays up rather than one with half its entries stale.
    if not addons or len(failed) * 2 > total:
        print(f"::error::{len(failed)} of {total} entries failed; not writing an index", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    index = {"schema": SCHEMA, "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "flavors": {f: [r.start, r.stop - 1] for f, r in FLAVORS.items()}, "addons": addons}
    (args.out / "addons.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(addons)} addons ({fresh} downloaded this run, {len(failed)} failed: {' '.join(failed) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
