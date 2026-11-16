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
| tiles | jumps of 3 m and more, all jumps, max height, max airtime, longest jump, max speed, max 10 s speed, distance, tacks, total airtime |
| jumps table | one row per jump, sortable; tap a row for the three signal panels (accelerometer magnitude with take-off/landing markers, the wrist turn against the riding posture, GPS speed) |
| track | the GPS trace with jump markers sized by height; *Show on map* loads OpenStreetMap tiles |
| timeline | speed over the session with jump marks |
| buttons | CSV of all jump metrics (including rejected candidates), GPX of the track, print / save as PDF |

**Height** always comes from the airtime (take-off pop to landing impact): `h = 1.18 (T + 1.6 s)² / 8`. The barometer is recorded but not used - see section 4.

**Analysis settings** (collapsible): speed unit (km/h by default), the six detection thresholds and the two height-model numbers.
*Show rejected candidates* lists everything the detector considered and why it was
rejected. Re-analyse applies
the new values to the loaded file; Defaults restores the values from `kitelog.js`.

## 2b. GoPro cross-check (HERO5 and newer)

Every GoPro MP4 from a HERO5 or newer carries a telemetry track: UTC time from the camera's GPS,
~18 Hz GPS (position, speed) and a ~200 Hz accelerometer and gyroscope. Open
<https://vuksv.github.io/kite-app/gopro.html> and pick one or more **original** clips (Quik
"Save" to Files, or the SD card; videos exported through the photo library lose the track).
Only the small telemetry payloads are read, on the phone; the video is never uploaded.

* **Lining up:** with a GPS fix, the camera's GPS clock places the clip (refined by matching the
  hits and slams both devices feel). Without GPS (Preferences › Regional › GPS on the camera)
  the clip is found by that motion pattern alone; the camera's own clock is local time and is
  not trusted. A weak match is flagged.
* **What you get per clip:** the KiteLog session, every KiteLog jump in the clip with its
  **time in the video** (scrub there and count the airtime on the footage), KiteLog's and the
  GoPro's own pop-to-impact airtime, and a chart of both accelerometers around each jump.
* **Download telemetry** saves ~2 KB per second of video (`.gopro.json.gz`) – send that file
  instead of the video for a deeper comparison.

`web/gpmf.js` was checked against GoPro's reference parser on GoPro's sample clips (HERO5–8,
MAX): identical samples, timing within 0.08 s. `web/test/gopro_align.mjs` checks the alignment
recovers a known offset within 0.3 s on clips simulated from a real session.

## 3. Python tools (computer)

```
pip install -r tools/requirements.txt
cd tools
python analyze.py ~/Downloads/2026-05-03.fit --plots       # or the .zip extracted first
```

Writes `jumps.csv`, `session.json`, `track.gpx`, `map.html`, `session.png` and one
`jump_NN.png` per accepted jump next to the input. `--a-eff` changes the airtime model.
`python surfr_compare.py --all` scores the detector against every Surfr reference list in
`sessions/` (section 4). `python simulate_session.py --fit` produces a synthetic session (the
*Load sample session* file); the simulator is a demo and a smoke test, not the yardstick -
the recorded sessions are.

## 4. How a jump is detected, and how accurate it is

*November 2026 review, on the four days recorded in parallel with the Surfr phone app
(sessions 5, 8, 9, 10: 154 Surfr jumps, 114 of them 3 m or higher).*

### The detector

A jump has to show all of this (`tools/kitelog/detect.py`, identical in `web/kitelog.js`):

| # | sign | test | why |
|---|---|---|---|
| 1 | take-off pop | a peak of the accelerometer magnitude (4 Hz low-pass) ≥ **2.0 g** | the rider loads the lines and leaves the water |
| 2 | landing impact | the **first** peak ≥ **2.4 g** between 1.5 and 7.5 s after the pop | later, harder slams are bounces, not the landing |
| 3 | arms overhead | the average direction of the acceleration in the watch frame turns by ≥ **40°** between riding (3–0.5 s before the pop) and the air | riding, the arms reach forward to the bar; in the air the rider hangs under it. Confirmed jumps: median 64°, 5 % below 48°. Chop that passes tests 1–2: median 17° |
| 4 | float | the 1 Hz level of the magnitude drops to ≤ **0.90 g** somewhere in the air | confirmed jumps: 90 % below 0.85 g; chop: 90 % above 0.80 g |
| 5 | moving | GPS speed at the pop ≥ **15 km/h** | standing, walking, water starts |
| 6 | no speed gain | the speed in the 1.5 s after landing is not all above take-off speed + **1.5 km/h** | confirmed jumps never gained more than 1.2 km/h; riding off after a turn gains 2–6 |
| 7 | long needs fast | an airtime over **4 s** needs **25 km/h** at the pop | all 45 confirmed long jumps took off at ≥ 25.9 km/h; the four unconfirmed 5–8 m "jumps" of the calm day at 19–24 km/h |

Of two overlapping candidates the harder pop + impact wins (this picks the real pop over a
bump just before it). Height is `1.18 (T + 1.6 s)² / 8` with T = pop → impact.

That is 10 numbers. The detector it replaces had about 45 (a "vibration stops" generator, a
pop/impact pair with roughness, level and speed gates, special cases for pre-pop bumps and
long weak pops) and never used signs 3 and 4, which turned out to separate jumps from chop
best: per candidate, wrist turn and float each sort jumps from chop about 90 % of the time,
pop and impact strength about 70 %, the old roughness ratio 69 %.

### Accuracy against Surfr

`python tools/surfr_compare.py --all`:

| | Surfr jumps ≥ 3 m that KiteLog has (**found**) | KiteLog jumps ≥ 3 m that Surfr has (**confirmed**) | all jumps found / confirmed | height of jumps ≥ 3 m vs Surfr |
|---|---|---|---|---|
| session 5 | 35/40 (88 %) | 42/44 (95 %) | 85 % / 94 % | 0.62 m off |
| session 8 | 35/38 (92 %) | 37/38 (97 %) | 85 % / 95 % | 0.54 m |
| session 9 (overpowered) | 16/16 (100 %) | 16/18 (89 %) | 94 % / 89 % | 0.87 m, reads 0.84 m low |
| session 10 (calm, slower) | 17/20 (85 %) | 17/19 (89 %) | 70 % / 79 % | 0.44 m |
| **all four days** | **103/114 (90 %)** | **112/119 (94 %)** | **82 % / 90 %** | **0.65 m (14 %)**, 76 % within 1 m |

How far to trust these numbers:

* **They are in-sample.** The thresholds were chosen on these four days. Choosing them on
  three days and scoring the fourth (signs 1–6; sign 7 only acts on session 10) gives
  **87 % found, 88 % confirmed** for jumps ≥ 3 m - expect that on a new day.
* **"3 m" is fuzzy at the edge.** With heights 0.65 m apart, a 3.2 m Surfr jump is often a
  2.8 m KiteLog jump. Requiring *both* apps to call it ≥ 3 m gives 85 % / 80 %.
* **Height has a floor.** Surfr's own height follows Surfr's own airtime only to 0.60 m, so an
  airtime-only estimate cannot get much below the 0.65 m it has now. Other formulas (linear,
  power law) and extra inputs (pop strength, speed, float depth) all land at 0.67–0.70 m when
  scored on a held-out day. On the overpowered day the same airtime was worth ~0.8 m more.
* **Surfr is not the truth either.** It is a phone in a pouch; two of its lists are partly
  minute-only and one has a jump missing from the screenshots. Several of KiteLog's
  "unconfirmed" jumps show every sign of a real jump.
* **Session 10 is the weak day**: slower riding, smaller jumps, long flat ones that keep their
  speed. Seven Surfr jumps between 2 and 4.4 m narrowly fail sign 4 or 6 there. Softer rules
  (a points score, a logistic score) recovered two or three of them and added more false
  jumps than that on the same day.

### The barometer is not used

On the water the port is wet: 143 of 144 jumps on the four Surfr days had pressure steps of
hundreds of Pa. Against Surfr the barometer heights were 2.4–2.8 m off, the airtime model
0.7 m. `HeightParams.use_baro` / `DEFAULT_HEIGHT.useBaro` turn the barometer estimators back
on for a dry mount (helmet, harness).

### A flaw in the earlier comparison

Until this review the reported score was 75 % found / 80 % confirmed for all jumps. Part of
that gap was the comparison, not the detector: Surfr's minute-only times in the session-5
list had been written as the *middle* of the minute, the other lists as its *start*, and the
matcher assumed the start - so in the second half of session 5 real matches were scored as
misses and false jumps. With the list corrected and the two jump lists aligned in time order
(`surfr_compare.match`), the old detector scored 79 % / 84 % for all jumps and 86 % / 88 %
for jumps ≥ 3 m. The new one is at 82 % / 90 % and 90 % / 94 %.

### Tuning on a new session

Open the report with *show rejected candidates* on; every candidate carries the reason it
was not counted (`slow`, `no_tilt`, `no_float`, `speed_gain`, `slow_for_airtime`, `overlap`,
`data_gap`). Tap it to see the three panels. Change a threshold in *Analysis settings* and
*Re-analyse*; `python tools/surfr_compare.py --all` then says what the change does on the
four reference days. Around the chosen values the score is flat (±5° of wrist turn, ±0.03 g
of float, ±0.2 g of pop or impact move found/confirmed by 1–3 points), so small changes are
safe and large ones are not needed.

What would raise the ceiling: more days with Surfr running (especially slow ones like
session 10), Surfr times to the second for the whole session, and GoPro clips of the jumps
the two apps disagree on.

## 4b. History: what the first sessions showed (Nov 2026)

*Kept as a record of how the detector got here. The detector and the numbers in this section
are superseded by section 4; the roughness, level and "pair" settings it mentions no longer exist.*

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
  A pair longer than 4.8 s must pop at ≥ 2.6 g: the widened rules let two chop slams 6–6.5 s
  apart through as 9–11 m "jumps", both with 2.3 g pops, while every real long jump popped at
  ≥ 3.0 g. Session total after that: 48 vs Surfr's 54, the last hour 25 vs 25, best jump
  6.8 m vs Surfr's 6.6 m. `tools/tests/test_real_sessions.py` locks these numbers in.
* **Sessions 6 and 7 (Nov 5).** The afternoon file's first record carried FIT "invalid"
  placeholders (timer 0xFFFFFFFF), which stretched the Python timeline to 46 days; such records
  are now dropped (the browser already ignored them). It then showed an 11.4 m / 6.75 s "jump"
  that was a slam followed by riding off after a slow turn: GPS speed rose 9 km/h "in the air".
  None of the 20 Surfr-confirmed jumps gains more than 2.75 km/h in the air, so a jump may not
  gain more than 2 m/s (7.2 km/h) over its take-off speed (rule `speed_gain`). Session 6 only
  overlaps the last 16 min of that day's Surfr recording (Surfr 9 jumps there, KiteLog 6;
  Surfr times are minute-rounded with an unknown start second, so no per-jump check).
* **Sessions 8 and 9 (Nov 6) – second calibration, 70 Surfr-timed jumps over sessions 5, 8, 9.**
  `tools/surfr_compare.py <fit> <surfr.csv>` finds Surfr's start by requiring the full jump
  pattern (pop 1–3 s before Surfr's stamp *and* impact after it; matching single impulses put
  session 8 2.5 s off) and reports recall, extras and height/airtime errors.
  Out of the box the detector found only 56 % and 44 % of sessions 8/9's jumps: the wrist is
  often as rough in the air as on the water. Thresholds were re-tuned by a leave-one-session-out
  grid search (pair roughness limit 1.45× → 2.2× riding, in-air bumps < 2.6 g; in-air level kept
  at 1.15 g because 1.2 g gave 21 false jumps/h in simulated rough water). A "pre-pop bump" rule
  stops a small bump just before the pop from pairing with the pop itself.
  Heights: KiteLog's pop-to-impact airtime is 1.14–1.31 s shorter than Surfr's on every day, so
  h = 1.39 · (T + 1.25 s)² / 8 (fitted on 56 matched jumps).

  | vs Surfr | recall | precision | height MAE |
  |---|---|---|---|
  | cross-validated (settings tuned without the tested session) | 77 % | 78 % | 0.74 m (17 %) |
  | final settings, session 5 | 21/27 (78 %) | 88 % | 0.63 m (18 %) |
  | final settings, session 8 | 19/25 (76 %) | 95 % | 0.47 m (10 %) |
  | final settings, session 9 | 14/18 (78 %) | 82 % | 0.97 m (19 %) |

  KiteLog's airtime column stays the measured pop-to-impact time (~1.25 s below Surfr's).
  `tools/tests/test_real_sessions.py` checks all three against their Surfr lists.
* **Session 10 (Nov 7) – third calibration, 4 Surfr days, 154 jumps.** A calm day (Surfr's best
  5.2 m) on which the Nov 6 settings over-detected (52 KiteLog vs 37 Surfr). `surfr_compare.py`
  now also scores **whole sessions**: minute-rounded Surfr jumps match any KiteLog jump in that
  minute, so every Surfr jump counts, not only the first hour's. Findings and changes:
  - the original "vibration stops" detector gave 1 confirmed vs 10 false jumps → switched off
    (`use_smooth_generator`; its candidates still show under *rejected*);
  - every confirmed jump with > 4.8 s pop-to-impact popped at ≥ 3.43 g, 7 of 8 false long ones
    at ≤ 2.94 g (riding through chop, one crash) → long flights need a 3.2 g pop;
  - pair thresholds re-searched leave-one-session-out on whole sessions: pop ≥ 2.4 g,
    impact ≥ 2.3 g, ≥ 1.5 s, ≥ 20 km/h at the pop; in-air level 1.15 g, roughness 2.2×;
  - a learned score over the same features did not beat the rules (CV F1 0.70 vs 0.73) – dropped;
  - a crash rule (no speed after landing) was rejected: 18 confirmed jumped transitions land
    at ≤ 8 km/h;
  - heights: a_eff 1.43 on T + 1.25 s (59 exact-time pairs, leave-one-session-out MAE 0.83 m, 20 %);
  - the simulator now flies with the measured wrist signature (≈1 g, riding-level vibration in the
    air), which makes it pessimistic (21–60 % recall); real Surfr sessions are the reference.

  | vs Surfr, final settings | exact-time recall | whole-session recall | whole-session precision | height MAE (exact) |
  |---|---|---|---|---|
  | session 5 | 22/27 | 37/52 (71 %) | 77 % | 0.75 m |
  | session 8 | 17/25 | 35/47 (74 %) | 95 % | 0.63 m |
  | session 9 | 15/18 | 15/18 (83 %) | 83 % | 0.91 m |
  | session 10 | 5/8 | 28/37 (76 %) | 68 % | 0.58 m |
  | **all** | 59/78 (76 %) | **115/154 (75 %)** | **80 %** | 0.71 m |

  Cross-validated whole-session estimate before the long-pop rule: 72 % recall, 68 % precision.
* The barometer is useless on the water as worn: steps of hundreds of Pa (water on the
  port) and no visible rise even on that clean jump. Heights therefore come from the airtime
  model (`a_eff`, to be calibrated against video/WOO); the barometer veto is off by default.
* Pauses show up as long gaps in the batch clock, not as data loss; 35 of 1883 batches
  were dropped by the FIT writer (expected, flagged).

## 5. Troubleshooting

| symptom | cause / fix |
|---|---|
| an indoor / sitting-still test shows a "jump" | a jump needs 15 km/h of GPS speed at the take-off, a 2 g pop and a 2.4 g impact; indoor GPS jitter of 10–15 km/h plus two hard knocks 1.5–7.5 s apart with the wrist turned in between could still pass - ignore jumps recorded off the water |
| GPS speed of 10–15 km/h while standing still | indoor fix jitter; the watch display floors it, the FIT keeps the raw Doppler speed. On the water with a real fix this disappears |
| "No KiteLog developer fields in this file" | the file is a normal watch activity, or the app failed to create its FIT fields (spike S2 in PLAN.md) |
| many gaps, batches < records | *Data Recording* on Smart, or the accelerometer listener stalled (power-cycle the watch) |
| baro 0 % | `rawAmbientPressure` returned null on this firmware – see fallbacks in PLAN.md §8 |
| heights look too high or low across a whole session | the airtime model is an average over four days (0.65 m off per jump, up to 0.8 m low on an overpowered day); adjust *Height model a_eff* in the settings |
| GPS fix % low | GPS configuration rejected; the app falls back to plain GPS – check the watch's satellite setting |
