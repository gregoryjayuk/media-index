#!/usr/bin/env python3
"""
media-index — roll-up reports from the walk database.

The reports are ROLLED UP: films by title, music by album, everything else by
collection/series. Never a line per book or track.

Usage:  ./report.py <media.db> <outdir> [--config=<file>]

Collection reports (your own named path prefixes, rolled up by series) come from
the "collection_reports" list in the config file: --config, else
media-index.local.json next to this script if it exists. See
media-index.example.json. Without one, those reports are simply not written.
"""

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

LOCAL_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media-index.local.json")


def load_config(path=None):
    """Read the JSON config: the --config file, else media-index.local.json beside
    this script if present, else nothing."""
    path = path or (LOCAL_CONFIG if os.path.exists(LOCAL_CONFIG) else None)
    if not path:
        return {}
    with open(os.path.expanduser(path)) as f:
        return json.load(f)

VIDEO = {"mkv", "mp4", "avi", "m4v", "mov", "wmv", "mpg", "mpeg", "ts", "iso", "vob", "flv", "divx", "rmvb", "webm"}
AUDIO = {"mp3", "flac", "m4a", "ogg", "wav", "wma", "aac", "ape", "opus", "m4b", "aiff"}
BOOK = {"epub", "mobi", "azw", "azw3", "pdf", "djvu", "chm", "cbr", "cbz", "fb2", "lit", "prc", "doc", "docx", "txt", "rtf"}
IMAGE = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "raw", "cr2", "nef", "heic", "webp"}
ARCHIVE = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "r00", "r01"}

SEASON_RE = re.compile(r"(?:^|[^a-z0-9])s(?:eason)?[ ._-]?(\d{1,2})(?:[^0-9]|$)", re.I)
EPISODE_RE = re.compile(r"s(\d{1,2})[ ._-]?e(\d{1,3})", re.I)
YEAR_RE = re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)")
QUALITY_RE = re.compile(r"\b(2160p|1080p|720p|480p|cam|ts|telesync|dvdrip|bdrip|brrip|bluray|webrip|web-dl|hdtv|xvid|divx)\b", re.I)


def build_dirsizes(conn):
    """Recursive size/count per directory, computed once in one pass.

    A `LIKE d.path || '/%'` join is O(files x dirs) and never finishes at this
    scale. Instead each file's size is added to every ancestor directory by
    walking its parent chain in memory.
    """
    if conn.execute("select count(*) from sqlite_master where name='dirsizes'").fetchone()[0]:
        return
    conn.executescript("""
        CREATE TABLE dirsizes (
            drive TEXT, path TEXT, name TEXT, depth INTEGER,
            files INTEGER, bytes INTEGER);
    """)
    roots = {d: r for d, r in conn.execute("select drive, root from runs")}
    files = defaultdict(int)
    total = defaultdict(int)
    for drive, parent, size in conn.execute("select drive, parent, size from files"):
        root = roots[drive]
        cur = parent
        while True:
            key = (drive, cur)
            files[key] += 1
            total[key] += size
            if cur == root or len(cur) <= len(root):
                break
            cur = os.path.dirname(cur)
    rootdepth = {d: r.rstrip("/").count("/") for d, r in roots.items()}
    conn.executemany(
        "INSERT INTO dirsizes VALUES (?,?,?,?,?,?)",
        [(d, p, os.path.basename(p), p.rstrip("/").count("/") - rootdepth[d], files[(d, p)], total[(d, p)])
         for (d, p) in files],
    )
    conn.executescript(
        "CREATE INDEX i_ds_bytes ON dirsizes(bytes);"
        "CREATE INDEX i_ds_depth ON dirsizes(depth);"
        "CREATE INDEX i_ds_path  ON dirsizes(path);"
        "CREATE INDEX IF NOT EXISTS i_files_path ON files(path);"
    )
    conn.commit()


def gb(n):
    return f"{n / 1e9:,.1f} GB" if n < 1e12 else f"{n / 1e12:,.2f} TB"


def q(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def h(f, title, level=2):
    f.write(f"\n{'#' * level} {title}\n\n")


def clean_title(name):
    """Strip extension, quality tags and separators from a media filename."""
    base = os.path.splitext(name)[0]
    base = QUALITY_RE.split(base)[0]
    base = re.sub(r"[._]+", " ", base)
    base = re.sub(r"\s*[\[(].*?[\])]\s*", " ", base)
    return re.sub(r"\s+", " ", base).strip(" -")


def section_overview(conn, f):
    h(f, "Overview — every drive, every top-level collection", 2)
    rows = q(conn, "select drive, files, dirs, bytes, errors, finished-started from runs order by bytes desc")
    f.write("| Drive | Files | Dirs | Size | Errors | Walk time |\n|---|--:|--:|--:|--:|--:|\n")
    for d, nf, nd, nb, ne, secs in rows:
        f.write(f"| {d} | {nf:,} | {nd:,} | {gb(nb)} | {ne} | {secs}s |\n")
    total = sum(r[3] for r in rows)
    f.write(f"| **TOTAL** | **{sum(r[1] for r in rows):,}** | | **{gb(total)}** | | |\n")

    h(f, "Top-level collections by size", 3)
    f.write("| Drive | Collection | Files | Size |\n|---|---|--:|--:|\n")
    for drive, name, nf, nb in q(conn, """
        select f.drive, substr(f.path, length(r.root)+2,
               case when instr(substr(f.path, length(r.root)+2), '/')=0
                    then length(f.path)
                    else instr(substr(f.path, length(r.root)+2), '/')-1 end) as top,
               count(*), sum(f.size)
        from files f join runs r on r.drive=f.drive
        group by f.drive, top having sum(f.size) > 5e9 order by sum(f.size) desc"""):
        f.write(f"| {drive} | `{name}` | {nf:,} | {gb(nb)} |\n")


def section_by_type(conn, f):
    h(f, "What all of it actually is, by file type", 2)
    buckets = [("Video", VIDEO), ("Audio", AUDIO), ("Books/docs", BOOK), ("Images", IMAGE), ("Archives", ARCHIVE)]
    f.write("| Type | Files | Size |\n|---|--:|--:|\n")
    claimed = set()
    for label, exts in buckets:
        claimed |= exts
        marks = ",".join("?" * len(exts))
        nf, nb = q(conn, f"select count(*), coalesce(sum(size),0) from files where ext in ({marks})", tuple(exts))[0]
        f.write(f"| {label} | {nf:,} | {gb(nb)} |\n")
    marks = ",".join("?" * len(claimed))
    nf, nb = q(conn, f"select count(*), coalesce(sum(size),0) from files where ext not in ({marks})", tuple(claimed))[0]
    f.write(f"| Everything else | {nf:,} | {gb(nb)} |\n")


def collection(conn, f, title, patterns, note=""):
    """Roll a set of path prefixes up to series/album/instructor level."""
    h(f, title, 2)
    if note:
        f.write(f"{note}\n")
    # Range comparison, not LIKE: SQLite can't use a BINARY index for LIKE
    # (it is case-insensitive by default), and a scan per collection is slow.
    prefixes = [p.rstrip("%") for p in patterns]
    where = " or ".join("(f.path >= ? and f.path < ?)" for _ in prefixes)
    args = tuple(x for p in prefixes for x in (p, p + "￿"))
    rows = q(conn, f"select f.drive, f.path, f.name, f.ext, f.size from files f where {where}", args)
    if not rows:
        f.write("_Nothing matched._\n")
        return
    total = sum(r[4] for r in rows)
    f.write(f"\n**Total: {gb(total)} across {len(rows):,} files**\n\n")

    groups = defaultdict(lambda: [0, 0])
    for drive, path, name, ext, size in rows:
        for pat in patterns:
            root = pat.rstrip("%").rstrip("/")
            if path.startswith(root):
                rest = path[len(root):].strip("/").split("/")
                key = (drive, root.split("/")[-1], rest[0] if len(rest) > 1 else "(loose files)")
                break
        else:
            key = (drive, "?", "?")
        groups[key][0] += 1
        groups[key][1] += size

    f.write("| Drive | Under | Series / collection | Files | Size |\n|---|---|---|--:|--:|\n")
    for (drive, root, name), (nf, nb) in sorted(groups.items(), key=lambda kv: -kv[1][1]):
        f.write(f"| {drive} | `{root}` | {name} | {nf:,} | {gb(nb)} |\n")


def section_films(conn, f):
    h(f, "Films", 2)
    marks = ",".join("?" * len(VIDEO))
    rows = q(conn, f"""select drive, path, name, size from files
                       where ext in ({marks}) and size > 200e6
                       order by size desc""", tuple(VIDEO))
    tv, films = [], []
    for drive, path, name, size in rows:
        (tv if EPISODE_RE.search(name) or SEASON_RE.search(path) else films).append((drive, path, name, size))
    f.write(f"**{len(films):,} likely film files, {gb(sum(r[3] for r in films))}** "
            f"(video >200 MB with no season/episode marker)\n")

    byname = defaultdict(list)
    for drive, path, name, size in films:
        byname[clean_title(name).lower()].append((drive, path, name, size))

    h(f, "Possible duplicate titles (same cleaned title, more than one copy)", 3)
    dupes = {k: v for k, v in byname.items() if len(v) > 1 and k}
    if dupes:
        f.write("| Title | Copies | Total | Locations |\n|---|--:|--:|---|\n")
        for k, v in sorted(dupes.items(), key=lambda kv: -sum(x[3] for x in kv[1]))[:60]:
            locs = "<br>".join(f"`{p}` ({gb(s)})" for _, p, _, s in v[:4])
            f.write(f"| {k[:60]} | {len(v)} | {gb(sum(x[3] for x in v))} | {locs} |\n")
        f.write(f"\n_{len(dupes)} duplicated titles in total._\n")
    else:
        f.write("_None found._\n")

    h(f, "Low-quality / suspicious films (feature-length name, small file)", 3)
    poor = [r for r in films if r[3] < 900e6 or QUALITY_RE.search(r[2]) and
            re.search(r"\b(cam|ts|telesync|480p|xvid|divx)\b", r[2], re.I)]
    f.write(f"**{len(poor):,} files, {gb(sum(r[3] for r in poor))}** — prune candidates\n\n")
    f.write("| File | Size | Path |\n|---|--:|---|\n")
    for drive, path, name, size in sorted(poor, key=lambda r: r[3])[:80]:
        f.write(f"| {name[:60]} | {gb(size)} | `{os.path.dirname(path)}` |\n")


def section_tv(conn, f):
    h(f, "TV — series, seasons and gaps", 2)
    marks = ",".join("?" * len(VIDEO))
    rows = q(conn, f"select drive, path, name, size from files where ext in ({marks}) and size > 50e6", tuple(VIDEO))
    series = defaultdict(lambda: defaultdict(list))
    sizes = defaultdict(int)
    for drive, path, name, size in rows:
        m = EPISODE_RE.search(name)
        if not m:
            continue
        show = clean_title(EPISODE_RE.split(name)[0]) or os.path.basename(os.path.dirname(path))
        key = (drive, show.strip().title())
        series[key][int(m.group(1))].append(int(m.group(2)))
        sizes[key] += size
    if not series:
        f.write("_No episode-numbered video found on the indexed drives._\n")
        return
    f.write(f"**{len(series)} series, {gb(sum(sizes.values()))}**\n\n")
    f.write("| Drive | Series | Seasons | Episodes | Size | Gaps |\n|---|---|---|--:|--:|---|\n")
    for (drive, show), seasons in sorted(series.items(), key=lambda kv: -sizes[kv[0]]):
        eps = sum(len(v) for v in seasons.values())
        gaps = []
        smin, smax = min(seasons), max(seasons)
        # A series whose lowest season is not 1 is missing its start — the
        # commonest real gap, and invisible if you only look between min and max.
        if smin > 1:
            gaps.append(f"**no S{1:02d}" + (f"–S{smin - 1:02d}**" if smin > 2 else "**"))
        missing_seasons = [s for s in range(smin, smax + 1) if s not in seasons]
        if missing_seasons:
            gaps.append("missing S" + ",S".join(f"{s:02d}" for s in missing_seasons))
        for s in sorted(seasons):
            e = sorted(set(seasons[s]))
            holes = [n for n in range(1, max(e) + 1) if n not in e]
            if holes:
                gaps.append(f"S{s:02d} missing E{','.join(str(x) for x in holes[:8])}"
                            + ("…" if len(holes) > 8 else ""))
        srange = f"S{smin:02d}–S{smax:02d}" if smin != smax else f"S{smin:02d}"
        f.write(f"| {drive} | {show[:45]} | {srange} ({len(seasons)}) | {eps} | {gb(sizes[(drive, show)])} "
                f"| {'; '.join(gaps[:4]) if gaps else '—'} |\n")


def section_music(conn, f):
    h(f, "Music — artist / album level (never tracks)", 2)
    marks = ",".join("?" * len(AUDIO))
    rows = q(conn, f"""select drive, parent, count(*), sum(size),
                       group_concat(distinct ext)
                       from files where ext in ({marks})
                       group by drive, parent having count(*) >= 2""", tuple(AUDIO))
    if not rows:
        f.write("_No audio found._\n")
        return
    total = sum(r[3] for r in rows)
    f.write(f"**{len(rows):,} albums (directories holding 2+ audio files), {gb(total)}**\n\n")
    h(f, "Largest 60 albums", 3)
    f.write("| Drive | Album (directory) | Tracks | Format | Size |\n|---|---|--:|---|--:|\n")
    for drive, parent, n, size, exts in sorted(rows, key=lambda r: -r[3])[:60]:
        f.write(f"| {drive} | `{parent}` | {n} | {exts} | {gb(size)} |\n")
    h(f, "Suspiciously small albums (2–4 tracks — likely incomplete rips)", 3)
    small = [r for r in rows if r[2] <= 4]
    f.write(f"**{len(small):,} albums**, {gb(sum(r[3] for r in small))}\n\n")
    f.write("| Drive | Album | Tracks | Size |\n|---|---|--:|--:|\n")
    for drive, parent, n, size, exts in sorted(small, key=lambda r: -r[3])[:50]:
        f.write(f"| {drive} | `{parent}` | {n} | {gb(size)} |\n")
    h(f, "Format split", 3)
    f.write("| Format | Files | Size |\n|---|--:|--:|\n")
    for ext, n, size in q(conn, f"""select ext, count(*), sum(size) from files
                                    where ext in ({marks}) group by ext order by sum(size) desc""", tuple(AUDIO)):
        f.write(f"| {ext} | {n:,} | {gb(size)} |\n")


def section_big(conn, f):
    h(f, "Everything else big — directories over 50 GB", 2)
    f.write("_Surprises are the point. Directories at depth 1–3 holding more than 50 GB._\n\n")
    f.write("| Drive | Directory | Files | Size |\n|---|---|--:|--:|\n")
    for drive, path, nf, nb in q(conn, """
        select drive, path, files, bytes from dirsizes
        where depth between 1 and 3 and bytes > 50e9
        order by bytes desc limit 60"""):
        f.write(f"| {drive} | `{path}` | {nf:,} | {gb(nb)} |\n")


def section_dupes(conn, f):
    h(f, "Cross-drive duplicates (same filename + exact byte size)", 2)
    f.write("_No hashing — this is the cheap first cut. Targeted hashing can confirm later._\n\n")
    rows = q(conn, """
        select name, size, count(*) c, count(distinct drive) dd, group_concat(distinct drive)
        from files where size > 10e6
        group by name, size having dd > 1 order by size * (c-1) desc limit 80""")
    tot = q(conn, """select coalesce(sum(size*(c-1)),0) from (
                       select size, count(*) c from files where size > 10e6
                       group by name, size having count(distinct drive) > 1)""")[0][0]
    f.write(f"**Reclaimable if deduplicated: ~{gb(tot)}**\n\n")
    f.write("| File | Size | Copies | Drives |\n|---|--:|--:|---|\n")
    for name, size, c, dd, drives in rows:
        f.write(f"| {name[:70]} | {gb(size)} | {c} | {drives} |\n")

    h(f, "Duplicate directories (same name, same file count, same total size)", 3)
    f.write("_Whole collections that appear to exist twice. Same name, identical "
            "file count and byte total — near-certain copies._\n\n")
    f.write("| Directory name | Files | Size | Copies | Paths |\n|---|--:|--:|--:|---|\n")
    for name, nf, size, n, paths in q(conn, """
        select name, files, bytes, count(*) n, group_concat(path, ' | ')
        from dirsizes where bytes > 5e9 and depth >= 1
        group by name, files, bytes having n > 1
        order by bytes * (n-1) desc limit 40"""):
        locs = "<br>".join(f"`{p}`" for p in paths.split(" | ")[:4])
        f.write(f"| {name} | {nf:,} | {gb(size)} | {n} | {locs} |\n")


def section_skipped(conn, f):
    h(f, "Not indexed (noted, not opened)", 2)
    # bytes < 0 means "deliberately not measured" — those rows must ALWAYS
    # show, regardless of size, or the report silently claims coverage it
    # does not have.
    rows = q(conn, "select drive, path, reason, files, bytes from skipped "
                   "where bytes > 1e9 or bytes < 0 order by bytes desc limit 40")
    if not rows:
        f.write("_Nothing large was skipped._\n")
        return
    f.write("| Drive | Path | Reason | Files | Size |\n|---|---|---|--:|--:|\n")
    for drive, path, reason, nf, nb in rows:
        if nb < 0:
            f.write(f"| {drive} | `{path}` | **{reason}** | _unknown_ | _unknown_ |\n")
        else:
            f.write(f"| {drive} | `{path}` | {reason} | {nf:,} | {gb(nb)} |\n")
    excl = q(conn, "select drive, path from skipped where bytes < 0")
    for drive, path in excl:
        f.write(f"\n> ⚠ **`{path}` on {drive} was NOT measured** — excluded on "
                f"purpose because walking it is too slow. Its contents are absent "
                f"from every total and every duplicate check in these reports.\n")
    tot = q(conn, "select count(*), coalesce(sum(files),0), coalesce(sum(bytes),0) from skipped")[0]
    f.write(f"\n_{tot[0]:,} skipped subtrees in total: {tot[1]:,} files, {gb(tot[2])}._\n")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--config=")]
    if len(args) != 2:
        sys.exit(__doc__)
    db, outdir = args
    config_path = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--config=")), None)
    config = load_config(config_path)
    os.makedirs(outdir, exist_ok=True)
    conn = sqlite3.connect(db)
    print("building directory rollups...", flush=True)
    build_dirsizes(conn)

    with open(os.path.join(outdir, "00-overview.md"), "w") as f:
        f.write("# Media index — overview\n\n"
                "Metadata-only walk. No file contents were read, nothing hashed, "
                "nothing moved, renamed or deleted.\n")
        section_overview(conn, f)
        section_by_type(conn, f)
        section_skipped(conn, f)

    with open(os.path.join(outdir, "01-films-tv.md"), "w") as f:
        f.write("# Films and TV\n")
        section_films(conn, f)
        section_tv(conn, f)

    with open(os.path.join(outdir, "02-music.md"), "w") as f:
        f.write("# Music\n")
        section_music(conn, f)

    # Collection reports: named path prefixes from the config, each rolled up
    # to series/collection level. One file per entry.
    for rep in config.get("collection_reports", []):
        with open(os.path.join(outdir, rep["file"]), "w") as f:
            f.write(f"# {rep['title']}\n")
            if rep.get("intro"):
                f.write(f"\n{rep['intro']}\n")
            for sec in rep["sections"]:
                collection(conn, f, sec["title"], sec["prefixes"], sec.get("note", ""))

    with open(os.path.join(outdir, "05-big-and-dupes.md"), "w") as f:
        f.write("# Big directories and duplicates\n")
        section_big(conn, f)
        section_dupes(conn, f)

    conn.close()
    print(f"reports written to {outdir}")


if __name__ == "__main__":
    main()
