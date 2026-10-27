# Phone-only workflow (trip checklist)

## Before you leave – 30 minutes, needs the watch and the phone only

1. **Prove the sync path once.** Record 2 minutes with KiteLog (START, wait, BACK › Save),
   sync the watch with the Garmin Connect app, then open <https://connect.garmin.com> in the
   phone browser, find the *Kiteboarding* activity, gear icon › **Export Original**. Open
   <https://vuksv.github.io/kite-app/> and pick the downloaded `.zip`. If the report shows
   batches and "baro 100 %", the whole path works and **nothing else is needed for the trip**.
   If the activity never appears in Garmin Connect, the files still stay on the watch
   (`GARMIN/Activity/`, ~0.6 MB per hour, weeks of room) and you analyse them after the trip;
   note in that case whether the watch showed *Saved* and whether a normal built-in activity
   syncs, because that tells which side is broken.
2. **Watch settings:** *System › Data Recording › Every Second*, *Satellites › All + Multi-Band*,
   battery full. Do not change the app ID or rebuild on the trip.
3. **Phone:** bookmark <https://vuksv.github.io/kite-app/>; if you set up the cloud archive
   (`cloud/setup.sh` from Cloud Shell, then run the *Deploy to Cloud Run* action once), open
   the service URL instead and enter the token under *Cloud archive* – the browser remembers it.

## On the water

Start KiteLog before walking in (GPS bars fill), START when you ride off, ignore the watch
until you come back, BACK › Save. The display is informational; the FIT file has everything.
After every session: sync, Export Original, open the report, **Upload to cloud archive** (or
keep the zip in the phone's Files app). Keep every file – a later pipeline fix re-analyses all
of them.

## Changing the analysis from the phone

The analysis is a static page; GitHub rebuilds and republishes it within about a minute of
every push to `main`, and your own pushes are the only way it changes.

* **No code:** every threshold is in *Analysis settings* on the page and persists in the phone
  browser (*Defaults* resets). *Show rejected candidates* explains every near-miss.
* **Code, from the phone:** open this repository in Claude Code (claude.ai/code) or the
  GitHub app, describe what the report got wrong (attach the CSV or the file), let it change
  `web/kitelog.js` (and `tools/kitelog/*.py` for parity) and push. The *Tests* workflow runs the
  Python tests, a short simulation and the JS-vs-Python parity check on every push; the Pages
  workflow redeploys. Reload the page, re-open the zip (or re-open it from the archive).
* Only `web/kitelog.js` matters for the phone report; `tools/` is the Python twin.

## What is and is not finished

Done and verified on the watch: 25 Hz batches every ~0.99 s, all 16 developer fields, session
save, raw barometer (σ 1.5 Pa), GPS fix and speed, the FIT decodes in the browser, and the
pipeline no longer calls a wrist movement at rest a jump.

Not yet verified (needs the first real session; nothing can be lost as long as the file is
saved): the vibration level while riding (sets the "Min riding vibration" and roughness
thresholds), real take-off/landing amplitudes at the wrist, barometer behaviour in spray and
wind, GPS quality on the water, battery per hour (expect ≥ 10 h), what the FIT writer does
over 2–3 hours (watch for `gaps` in the health pills). The live jump counter on the watch is
intentionally off.
