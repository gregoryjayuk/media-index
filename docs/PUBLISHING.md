# Publishing

The public repository (github.com/gregoryjayuk/media-index) is a single-commit snapshot of this repo's committed tree, never a push of this repo's history.

## Excluded from the snapshot

| Path | Why |
|---|---|
| `*.log` | walk logs print real paths from the indexed drives (also gitignored) |
| `reports/` | real reports listing the indexed collection (also gitignored) |
| `*.db`, `*.sqlite*` | the index itself, a listing of every real file (also gitignored) |
| `media-index.local.json` | the maintainer's own drive paths and collection names (tracked here with `git add -f`, gitignored for everyone else) |
| `docs/LOCAL.md` | the original README: drive labels, storage locations and coverage notes for the maintainer's own estate |

## Scrub steps

None beyond the excludes: drive-specific paths were moved out of the code into `media-index.local.json`, and the screenshot was taken against the demo tree from `demo/make_demo_tree.py`.

## Rebuild

```sh
STAGE=/path/to/staging/media-index
rm -rf "$STAGE" && mkdir -p "$STAGE"
git -C ~/Code/projects/media-index archive HEAD | tar -x -C "$STAGE"
cd "$STAGE" && rm -rf media-index.local.json docs/LOCAL.md reports && find . -name '*.log' -delete
git init -b main && git add -A
git -c user.name=Gregoryjay -c user.email=gregoryjayuk@users.noreply.github.com commit -m "Initial public release"
```

Then run the privacy scan (grep for hostnames, home paths, private folder names, IPs and secrets) before pushing.
