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

## 4b. What the first real session (Brazil, Nov 2026) showed

* Riding vibration on the wrist: roughness median 0.13 g (5th–95th percentile 0.06–0.34 g);
  in the air it collapses to 0.02–0.05 g. |f| while riding averages 1.1 g with chop slams
  to 4–7 g that are *not* jumps (no smooth stretch after them).
* A clean jump: 3.5 g pop, 0.69 g while flying, impact 4.8 g **1.7 s after the vibration
  first came back** (kite steering in the descent) – hence the 2.5 s landing window and the
  "first impact, not biggest" rule.
* The rider reported a jump on ~70 % of 21 turns, biggest ≤ 4–5 m. The detector found one
  by the "smooth stretch" rule; the other 14 show as **pop → impact pairs** (2.6–3.8 g, then
  3.2–5.9 g, 2.7–4.1 s apart) with a middle at 0.83–0.96 g and roughness 0.5–1.2× riding –
  the wrist is not still in the air. That pair signature is now the second candidate
  generator (`pair_*` settings); jumped transitions nearly stop, so no speed-collapse gate
  for them. `a_eff` lowered to 2.5 m/s² to match the reported heights (rough calibration).
* **Session 5 (Nov 4) – calibration against the Surfr phone app.** Surfr (phone in a pouch,
  100 Hz IMU + barometer) logged 54 jumps over 2 h 17 min; its list is in
  `sessions/2026-11-04_brazil-5_surfr.csv`. Aligned in time (Surfr started 548.5 s before the
  watch; Surfr stamps a jump ~2 s after the pop), the wrist shows a pop → impact pair for 22 of
  the 27 jumps with exact times. Those flights read **0.9–1.2 g** on the wrist (session 1:
  0.83–0.96 g), with small bumps up to 2.3 g in the air, so the pair rules were widened:
  pop ≥ 2.2 g, impact = first peak ≥ 2.5 g, level ≤ 1.15 g, in-air bumps < 2.4 g, roughness
  ≤ 1.45× riding, ≥ 15 km/h at the pop. Result: 20/27 found with 3 extras (one where Surfr's
  unlisted jump 17 is), 50 jumps for the session vs Surfr's 54, heights correlate 0.85 with
  Surfr. Pop-to-landing airtime is ~0.9 s shorter than Surfr's airtime. `a_eff` = 2.0 m/s²
  puts KiteLog 0.5 m below Surfr on average; 2.2 would match Surfr exactly, but the
  independent study found Surfr overestimates by about that much, so 2.0 stays the default.
  `tools/tests/test_real_sessions.py` locks these numbers in.
* The barometer is useless on the water as worn: steps of hundreds of Pa (water on the
  port) and no visible rise even on that clean jump. Heights therefore come from the airtime
  model (`a_eff`, to be calibrated against video/WOO); the barometer veto is off by default.
* Pauses show up as long gaps in the batch clock, not as data loss; 35 of 1883 batches
  were dropped by the FIT writer (expected, flagged).

## 5. Troubleshooting

| symptom | cause / fix |
|---|---|
| an indoor / sitting-still test shows a "jump" | fixed in the Oct 2026 pipeline: at rest there is no chop vibration, so a wrist movement used to pass as a flight and the airtime formula invented a height. Candidates now need riding vibration around them (*Min riding vibration*), and a healthy barometer that shows no rise rejects the jump instead of falling back to the airtime model |
| GPS speed of 10–15 km/h while standing still | indoor fix jitter; the watch display floors it, the FIT keeps the raw Doppler speed. On the water with a real fix this disappears |
| "No KiteLog developer fields in this file" | the file is a normal watch activity, or the app failed to create its FIT fields (spike S2 in PLAN.md) |
| many gaps, batches < records | *Data Recording* on Smart, or the accelerometer listener stalled (power-cycle the watch) |
| baro 0 % | `rawAmbientPressure` returned null on this firmware – see fallbacks in PLAN.md §8 |
| all heights "airtime" | barometer baseline inconsistent (splash) or fit RMSE too large; check the barometer panel of a jump |
| GPS fix % low | GPS configuration rejected; the app falls back to plain GPS – check the watch's satellite setting |
