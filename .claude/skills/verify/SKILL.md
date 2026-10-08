---
name: verify
description: Build, launch, and drive this app end-to-end to verify a change at its real surface (the served GUI + API).
---

# Verifying a change in this app

The deliverable is one FastAPI app serving the built React bundle. Verify at
that surface — the running server + browser — not with tests (CI runs those).

## Build & launch

```bash
cd frontend && npm ci && npm run build            # bundle → frontend/dist
cd ../backend && pip install -r requirements.txt  # cryptography may need --ignore-installed on Debian images
TCO_DATABASE_URL=sqlite:///$SCRATCH/tco.db TCO_DATA_DIR=$SCRATCH \
TCO_MASTER_SECRET=verify TCO_FRONTEND_DIST=../frontend/dist \
python -m uvicorn app.main:app --port 8471 &      # run in background, check /api/health
```

Seed data fast via the API (`POST /api/engagements`, then
`/api/engagements/{id}/personas`, `/current-licenses`, `/third-party`, …), then
drive the GUI for the changed flow.

## Driving the GUI (headless)

Python Playwright works; the container pre-installs Chromium — launch with
`p.chromium.launch(executable_path='/opt/pw-browsers/chromium')` (never
`playwright install`).

Gotchas:
- Table cell values render inside `<input>` elements — `text=` locators never
  match them. Wait on structural elements instead (e.g. `button[title='Details']`
  for a third-party row) and read values from inputs.
- Number fields commit on blur/Enter (`NumInput`), not per keystroke — `fill()`
  then `press("Enter")` and wait ~500ms for the save-and-reload round trip.
- Navigation: there is no engagement list. Go straight to
  `http://localhost:8471/#/e/<engagement id>/<step>`, or type part of the customer
  name in the Open-engagement search box and click the result; then use the step
  buttons (`Customer`, `Groups & licences`, `Other tools`, `Future state`,
  `Coverage check`, `Review`, `Summary & PDF`) or the step key in the URL
  (`customer`, `groups`, `tools`, `future`, `gaps`, `review`, `summary`).
- Recommendations need prices: a fresh database has no price list, so load one
  (`POST /api/catalog/import-csv`; `tests/sweep_services.py` has a small CSV)
  before verifying "Fill in recommended plans".
- The customer PDF downloads via `page.expect_download()`; `pdftoppm` or PyMuPDF
  renders its pages to images for a visual check.
- The session DB is throwaway; aborted script runs leave rows behind — don't
  read a duplicate row as an app bug.
