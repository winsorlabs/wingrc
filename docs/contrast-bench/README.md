# Contrast bench

The scripts that measured V.1 (`docs/roadmap.md`'s Done entry "V.1:
`--primary` has headroom"). They are kept here because V's were lost with
its bench and had to be rewritten. Use them for any slice that changes a
colour, and extend `SCREENS` in `measure.mjs` when a slice adds a screen.

They run against a bench stack, never live (`docs/bench-stack-verification.md`).

## Setup on wl-util-1

1. Bench project with a restored copy of live: `db` up, `create role
   wingrc_app`, `pg_restore --no-owner`, then `backend minio` only. Leave
   the worker out, so no scheduled job runs against the copy.
2. Build `main` and the branch from two worktrees with `node:24-alpine`
   (`npm install && npm test && npm run build`).
3. Serve each `dist` from `nginx:1.29-alpine` on the bench network, named
   `v1-web-main` and `v1-web-branch` (rename to suit). The config is a
   root plus `location /api/ { proxy_pass http://backend:8000/; }`. Publish
   no host ports.
4. Mint a session on the bench copy for an existing `msp_admin` with
   `app.auth.create_session`, and write the raw token to `session.txt`
   (mode 600). This is never a live credential, and the file is deleted
   at teardown.

## Run

Mount the bench directory at `/b`, with these scripts in `/b/pw`:

```
docker run --rm --network <bench>_default -v $B:/b -w /b/pw \
  mcr.microsoft.com/playwright:v1.56.0-noble node measure.mjs main
```

Then run the same command with `branch`. `measure.mjs` writes
`out/<build>/*.png` and `results.json`. `ring.mjs` and `hover.mjs` take
pixel crops of a focused or hovered primary button. Locally,
`python -X utf8 analyse.py` (with `main.json` and `branch.json` beside
it) prints the before/after grouping. `contrast.py` holds the WCAG ratio
and the CIEDE2000 colour difference.

## What it measures, and what it does not

- Each visible text node is measured against its composited background,
  with opacity applied. Text under an overlay is skipped.
- `background-image` (gradients) is flagged, not measured.
- Placeholder colour is read from `::placeholder`.
- Non-text uses are recorded only for the blue values listed in
  `MEASURE`. Edit that list for another token.
- The focus walk records computed outline, border and shadow. Only the
  pixel crops show what the browser actually draws: Chrome's `auto`
  ring reports `rgb(16, 16, 16)` and draws a 2px solid ring.
