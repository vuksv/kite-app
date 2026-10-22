# Analysing a session after recording

Everything analytical happens off the watch. The watch only writes a Kiteboarding
activity (FIT file) that carries the raw 25 Hz accelerometer stream, the raw barometer
and GPS in developer fields. You get that file to your phone or computer and open it in
the report app (browser) or the Python tools.

## 1. Get the FIT file off the watch

**Sync, then export from Garmin Connect (works on the phone)**

1. Let the watch sync with the Garmin Connect app as usual. The activity appears as
   *Kiteboarding*.
2. Open <https://connect.garmin.com> in the phone's browser (if the activity menu looks
   cramped, request the desktop site), open the activity, tap the gear icon ⚙ and choose
   **Export Original**. A `.zip` lands in your Downloads / Files app. The report app opens the
   zip directly; you do not need to unzip it.
3. On a computer the same export works from the activity page; or use USB (below).

**USB (no sync needed)**

Set the watch to *System › USB Mode › Mass Storage* (Instinct 2 manual), plug it in and
copy `GARMIN/Activity/<date>.fit`.

Garmin's mobile app does not offer a FIT export itself as far as we could verify; the
website route above is the reliable one.

## 2. The report app (phone or computer, no install)

Open `web/index.html`:

* hosted: `https://vuksv.github.io/kite-app/web/` once GitHub Pages is enabled for the
  repository (Settings › Pages › *Deploy from a branch* › `main` / root);
* locally: double-click `web/index.html` (the "Load sample session" button needs the page to
  be served over http, e.g. `python3 -m http.server` in the repo root → `http://localhost:8000/web/?sample`).

Then **pick the file** (`.fit`, the Garmin `.zip`, or `.fit.gz`). The page decodes the FIT,
runs the same detection pipeline as the Python tools (`web/kitelog.js` is a line-by-line
port; `web/test/compare.mjs` checks that the two give identical numbers) and renders:

| section | what it shows |
|---|---|
| header pills | data health: accelerometer batches, duplicated / missing batches, barometer and GPS availability. Many gaps = the watch dropped records (check *Data Recording = Every Second*) |
| tiles | jumps, max height, max airtime, longest jump, max speed, max 10 s speed, distance, tacks, total airtime |
| jumps table | one row per jump, sortable; tap a row for the three signal panels (accelerometer magnitude with take-off/landing markers, barometer samples with the fitted trajectory, GPS speed) and every intermediate estimate |
| track | the GPS trace with jump markers sized by height; *Show on map* loads OpenStreetMap tiles |
| timeline | speed over the session with jump marks |
| buttons | CSV of all jump metrics (including rejected candidates), GPX of the track, print / save as PDF |

**Height method column:** `baro_fit` is the primary estimate (physics model fitted to the
1 Hz barometer samples), `baro_quad` a parabola through the samples when the fit did not
converge, `airtime` the fallback `a_eff·T²/8` when the barometer was unusable (spray on the
port, missing baseline). Flags such as `landing_in_gap` mean a dropped record touched the
jump; the numbers are still reported but less certain.

**Analysis settings** (collapsible): speed unit (km/h by default), `a_eff`, and the main detection thresholds.
*Show rejected candidates* lists everything the detector considered and why it was
rejected – the most useful view while tuning on the first real sessions. Re-analyse applies
the new values to the loaded file; Defaults restores the values from `kitelog.js`.

## 3. Python tools (computer)

```
pip install -r tools/requirements.txt
cd tools
python analyze.py ~/Downloads/2026-05-03.fit --plots       # or the .zip extracted first
```

Writes `jumps.csv`, `session.json`, `track.gpx`, `map.html`, `session.png` and one
`jump_NN.png` per accepted jump next to the input. `--a-eff` changes the airtime model.
`python validate.py` re-runs the Monte-Carlo validation; `python simulate_session.py --fit`
produces a synthetic session with ground truth for testing either pipeline.

## 4. Tuning and calibration on real data (first sessions)

1. Record a session, open the report with *show rejected candidates* on.
2. Missed jumps → lower `Take-off peak min` / `Landing peak min` (wrist transients may be
   softer than simulated) or raise `Flight roughness ratio` (chop contrast smaller).
3. False jumps during transitions or crashes → raise `Min take-off speed`, lower
   `Flight level max`, or lower the roughness ratio.
4. Heights consistently off vs. video or a WOO/Surfr reference → adjust `a_eff` for the
   airtime model; a constant offset of ~+0.3 m on the barometer estimate is the arm-posture
   change in flight and can be subtracted.
5. **Large quick change ("jerk") gates.** The detector already requires a load peak
   (≥ 1.35 g) before and an impact (≥ 1.6 g) after every flight. The jerk settings add a
   sharper test on the sample-to-sample change of |f| (g per 40 ms); they are off (0) by
   default and each jump's panel shows its measured values. Use them if real sessions show
   false jumps with slow, rounded transients. Simulation caveat: the impulses alone cannot
   replace the vibration test – loosening *Flight roughness ratio* to 0.6 with jerk gates at
   0.5 g kept recall but produced >10 false jumps per hour in chop and >100 in rough water,
   and jerk gates at 0.4 g on their own cost 2–6 % recall. Tune on real data, not on the
   simulator.
6. Barometer trace noisy for a whole session → spray on the sensor port; wear the watch
   under the wetsuit sleeve or on the harness/upper arm next time.

Keep the FIT files: every tuning change can be replayed over all sessions with
`tools/analyze.py` or the report app.

## 5. Troubleshooting

| symptom | cause / fix |
|---|---|
| "No KiteLog developer fields in this file" | the file is a normal watch activity, or the app failed to create its FIT fields (spike S2 in PLAN.md) |
| many gaps, batches < records | *Data Recording* on Smart, or the accelerometer listener stalled (power-cycle the watch) |
| baro 0 % | `rawAmbientPressure` returned null on this firmware – see fallbacks in PLAN.md §8 |
| all heights "airtime" | barometer baseline inconsistent (splash) or fit RMSE too large; check the barometer panel of a jump |
| GPS fix % low | GPS configuration rejected; the app falls back to plain GPS – check the watch's satellite setting |
