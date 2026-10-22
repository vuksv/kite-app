# KiteLog – kite-session recorder for the Garmin Instinct 2X Solar

**Deliverable:** an implementation plan that is ready to build and test, with the physics,
the data format, the watch app, the offline analysis pipeline (Python and a browser report
app), a session/jump simulator, and Monte-Carlo validation results.  Everything referenced
here is in this repository (watch app at the root, `web/`, `tools/`, `docs/`).

Goal recap (your words): start the app, record a kite session, see a timer and live speed,
then analyse jumps afterwards on a computer – height, airtime, jump length/distance –
plus the GPS trace on a map, Hoolan/Surfr style.  Processing happens off the watch.

---

## 0. Executive summary

* **What the hardware gives you (verified):** 25 Hz 3-axis accelerometer (no gyroscope),
  1 Hz barometer, 1 Hz multi-band GNSS, 2-colour 176×176 display, Connect IQ API 3.4,
  ~96 KB RAM per watch app.  Surfr refuses to do live jump heights on exactly this class of
  watch (they require 100 Hz + gyro), so **the design goal is "record everything, compute
  offline"**, which is what you asked for anyway.
* **The hard constraint is getting raw data off the watch.**  There is no file API, the
  debug log stops at 5 KB, FIT array fields do not work on device, and a Connect IQ app can
  write at most 16 developer fields / 256 bytes per 1-second FIT record.  The plan packs the
  25 Hz accelerometer stream into 13 FIT `DOUBLE` fields (2 samples per field, 8-bit
  companded per axis) plus raw pressure, a millisecond timer and a sequence/health word
  – exactly 16 fields, 128 bytes/s, ~460 KB per hour, synced through Garmin Connect like any
  activity.  The pack/unpack code exists in Monkey C and Python and is unit-tested against
  each other.
* **Physics that matters:** a kite jump is not ballistic.  The kite keeps pulling, so the
  net vertical acceleration is 2–4 m/s², not 9.8, the wrist accelerometer reads ~0.6–0.85 g
  in the air (never 0 g), and airtime is far longer than √(8h/g).  Detection therefore uses
  the *drop in water-chop vibration* bracketed by the take-off load peak and the landing
  impact, airtime comes from those two transients, height comes from a physics-model fit to
  the 1 Hz barometer samples (with an airtime-only fallback), and jump length from GPS speed
  integrated over the airtime.
* **Validation (simulation, `tools/validate.py`):** 100 % recall and 0 false positives per
  hour in the baseline scenario (81 jumps), airtime error 0.02 s, height error 7 % for
  5–10 m jumps and 2 % above 10 m, ~0.3–0.5 m for small jumps, jump-length error ~1 m.  The
  simulator is honest about what it does not know (real wrist vibration levels, real
  barometer behaviour in spray and wind) – those are the first things the field tests
  measure.
* **Live display:** timer, speed in km/h, GPS quality and a recording health counter are
  in the skeleton.  A simple live jump counter is included as phase 2 (off by default).

---

## 1. Research findings (what the SDK and the device can do)

| Topic | Finding | Source |
|---|---|---|
| Device API level | Instinct 2X Solar = Connect IQ **3.4**, 176×176 semi-octagon 2-colour MIP | [Compatible devices](https://developer.garmin.com/connect-iq/compatible-devices/) |
| App memory | **98 304 bytes** for a watch-app on `instinct2x`; watchdog 120 000 steps | [Forum: memory limits](https://forums.garmin.com/developer/connect-iq/f/discussion/416669/maximum-prg-file-size-error/1954838) |
| Sensors | Accelerometer yes, **gyroscope no**, barometric altimeter yes, compass yes, multi-band GNSS with SatIQ | [Spec sheet](https://gadgetsandwearables.com/technical-specs/garmin-instinct-2x-solar/), Instinct 2 manual (multi-band only on 2X Solar) |
| High-rate accel | `Sensor.registerSensorDataListener` (API 2.3): `:period` 1–4 s, `:sampleRate` up to `getMaxSampleRate()` (25 Hz on this class), x/y/z arrays in **milli-g**, optional pitch/roll; `timestamp` array only since API 5.1.1 (not available) | [Toybox.Sensor](https://developer.garmin.com/connect-iq/api-docs/Toybox/Sensor.html), [AccelerometerData](https://developer.garmin.com/connect-iq/api-docs/Toybox/Sensor/AccelerometerData.html) |
| Accel caveats | Simulator produces no sensor data unless you play an activity; some devices need a power-cycle before the callback fires; the callback must not be registered from a data field | [Forum thread](https://forums.garmin.com/developer/connect-iq/f/discussion/361135/need-help-with-high-rate-accel-via-registersensordatalistener/1735365) |
| Barometer | `Activity.Info.rawAmbientPressure` (Pa, temperature-compensated, unfiltered), `ambientPressure` (2-stage filtered), `altitude` (fused with GPS).  `Sensor.Info.pressure` is **sea-level calibrated**, not raw.  Only 1 Hz access; Garmin has acknowledged but not delivered a high-rate pressure API | [Activity.Info](https://developer.garmin.com/connect-iq/api-docs/Toybox/Activity/Info.html), [Sensor.Info](https://developer.garmin.com/connect-iq/api-docs/Toybox/Sensor/Info.html), [feature request](https://forums.garmin.com/developer/connect-iq/i/bug-reports/high-frequency-barometric-pressure-sensor-access-in-connect-iq) |
| GPS | `Position.enableLocationEvents` with `:configuration` (API 3.3.6): `CONFIGURATION_GPS_GLONASS_GALILEO_BEIDOU_L1_L5` (multi-band) / `CONFIGURATION_SAT_IQ`; check `hasConfigurationSupport`.  `Position.Info` gives position (dead-reckoned between ~1 s fixes), speed (Doppler), heading, accuracy | [Toybox.Position](https://developer.garmin.com/connect-iq/api-docs/Toybox/Position.html), [Position.Info](https://developer.garmin.com/connect-iq/api-docs/Toybox/Position/Info.html) |
| FIT recording | `ActivityRecording.createSession({:sport => Activity.SPORT_KITESURFING})` (sport 44, API 3.2) → Garmin Connect shows a Kiteboarding activity with map.  Record messages written ≤ 1 Hz; `setData` values are overwritten if set twice before a write | [ActivityRecording](https://developer.garmin.com/connect-iq/api-docs/Toybox/ActivityRecording.html), [Activity](https://developer.garmin.com/connect-iq/api-docs/Toybox/Activity.html), [FitContributor](https://developer.garmin.com/connect-iq/api-docs/Toybox/FitContributor.html) |
| Developer-field limits | **256 bytes per message for apps**; firmware caps active developer fields at **16** (Garmin staff: "typically 16, may vary by device"), a 17th crashes the app with an uncatchable error; `:count` array fields write only the first element in the simulator and crash on device | [Session.createField](https://developer.garmin.com/connect-iq/api-docs/Toybox/ActivityRecording/Session.html), [16-field thread (Garmin staff)](https://forums.garmin.com/developer/connect-iq/f/discussion/234758/what-is-the-limiting-number-of-developer-fields-that-can-be-added-under-session/1115168), [array fields thread](https://forums.garmin.com/developer/connect-iq/f/discussion/315947/fitcontributor-setdata-array) |
| Device log file | `System.println` → `/GARMIN/APPS/LOGS/<app>.TXT`, hard-capped at 5 KB (+5 KB .BAK), "not meant for data" | [Forum: log size](https://forums.garmin.com/developer/connect-iq/f/discussion/6311/device-log-size-limitation---alternate-sysout) |
| App storage | `Application.Storage` values ≤ 32 KB, total varies per device (a few hundred KB at best) – not a raw-data path | [Application.Storage](https://developer.garmin.com/connect-iq/api-docs/Toybox/Application/Storage.html) |
| Recording interval | Watch setting *System › Data Recording: Smart (default) / Every Second*.  Apps cannot control the write rate; **set Every Second** | Instinct 2 owner's manual; [forum](https://forums.garmin.com/developer/connect-iq/f/discussion/3216/override-smart-recording) |
| Competitors | Surfr: needs gyro + 100 Hz accel; Instinct 2 family is "GPS only".  Hoolan: height, airtime, distance, take-off speed, max air-speed, tacks, totals; watch-recorded, no calibration.  WOO: board-mounted 9-axis sensor, defines height as apex of the sensor above take-off | [Surfr devices](https://support.thesurfr.app/en/articles/9859609-which-devices-are-compatible-with-the-surfr-app), [Hoolan](https://apps.apple.com/gb/app/hoolan-kite-wingfoil-windsurf/id1639913635), [WOO](https://www.woosports.com/en/the-woo-way/point-of-measurement) |
| Independent accuracy | Videogrammetric study of 20 jumps (3–7.3 m): RMS error Surfr 0.51 m, WOO3 0.70 m, WOO2 0.95 m; all overestimate, errors > 20 % above 5 m; ±0.2 m zero-level uncertainty from waves | [Marčiš et al., Sensors 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8706814/) |
| Reference jumps | 28.6 m in 8.5 s (Jacobsen 2017, WOO), 32.2 m in 10.15 s (Whaley 2023, Surfr) → effective net acceleration 2.5–3.2 m/s² | search results in session log |
| Barometer + motion | A pressure port moving through air sees part of ½ρv² (1 m error at 10 mph, 36 m at 60 mph for an exposed port); wet ports read low for up to 30 min; wearing the port facing away from the flow reduces it | [Sonoma barometric altimetry note](https://s4.sonoma.edu/wp-content/uploads/2013/08/K-Barometric-Altimetry.pdf), Garmin/Suunto user forums |

Implications: no orientation tracking (no gyro) → work with the accelerometer *magnitude*;
1 Hz barometer → fit a trajectory model instead of reading the maximum; FIT is the only
realistic data path → pack hard; 25 Hz is plenty for airtime (±40 ms).

---

## 2. Architecture

```
 Instinct 2X Solar (Monkey C, watch/)                       Computer (Python, tools/)
 ┌───────────────────────────────────────┐                 ┌──────────────────────────────┐
 │ SensorHub   25 Hz accel batches (1/s) │   FIT activity  │ fitio.read_fit  (garmin-fit-sdk)
 │             GPS 1 Hz, raw pressure 1 Hz│ ──Garmin──────▶│ detect   jumps from |f| + baro + GPS
 │ Recorder    FIT session + 16 dev fields│   Connect /    │ height   physics fit / airtime model
 │ Pack        8-bit companding, 2/DOUBLE │   USB .fit     │ session  distance, speeds, tacks, map
 │ View        timer, speed, GPS, health  │                 │ report   plots, CSV/JSON, GPX, HTML map
 │ Live detector (phase 2, optional)      │                 │ simulate + validate  (synthetic truth)
 └───────────────────────────────────────┘                 └──────────────────────────────┘
```

Watch responsibilities: record faithfully, show timer/speed, never lose data (auto-save on
exit).  Computer responsibilities: everything analytical.  The simulator produces the same
record table the watch writes, so the pipeline is tested before the first session.

---

## 3. Data format (the contract between watch and pipeline)

### 3.1 FIT record message, 1 Hz, 16 developer fields (128 bytes)

| id | name | FIT type | content |
|---|---|---|---|
| 0–12 | `acc_0` … `acc_12` | DOUBLE | 26 sample slots: slot 2k and 2k+1 in field k; each slot = x,y,z codes |
| 13 | `press` | FLOAT | `Activity.Info.rawAmbientPressure` in Pa, read in the accelerometer callback |
| 14 | `tmr` | UINT32 | `System.getTimer()` (ms) at the callback = time of the **last** sample of the batch |
| 15 | `meta` | UINT32 | bits 0-15 batch sequence, 16-20 samples in batch, 21-23 app state, 24-30 flags (bit24 baro valid, bit25 GPS usable) |

Native record fields written by the device: timestamp (1 s), position_lat/long, enhanced
speed, altitude, heart rate, distance.  Session sport = kitesurfing (44).

### 3.2 Sample companding (per axis, `Pack.encodeMg` ⇔ `packing.encode_g`)

```
|a| ≤ 3 g : code = round(a · 32)                      resolution 31 mg
|a| > 3 g : code = sign · (96 + round((|a| − 3 g) · 8)) resolution 125 mg, clips at ±6.9 g
code −128 : empty slot
```
Two samples → 6 bytes (code+128 each, s0x most significant) → one 48-bit integer → stored
as a DOUBLE (exact below 2^53).  31 mg resolution is 4× finer than the quietest
in-flight signal we need to see (roughness floor 35 mg RMS); 6.9 g covers landing impacts
(the detector only needs their presence above ~2 g).  Unit test
`tools/tests/test_packing.py` proves the Monkey C integer arithmetic and the Python
float path produce identical codes for every milli-g value from −9 g to +9 g.

### 3.3 Timing model

* Within a batch, samples are 40 ms apart ending at `tmr` (no per-sample timestamps on API
  3.4).  The pipeline rebuilds a uniform 25 Hz grid from `tmr`.
* The FIT writer ticks on its own 1 Hz clock.  If two batches arrive between writes one is
  lost; if none arrive the previous one is written again → duplicates and gaps are
  **detected from `seq`** and handled (duplicates dropped, gaps bridged for filtering, flagged
  on any jump they touch).  Simulation with 2 % dropped records still gives 98 % recall.
* `ts ≈ tmr/1000 + offset` with the offset estimated as the median over the session; GPS
  fixes are assumed ~0.5 s before the record write.  Jump length therefore uses the speed
  integral (phase-insensitive) rather than positions.

### 3.4 Budget

128 B developer + ~30 B native per record → ~0.57 MB per hour; a 3 h session ≈ 1.7 MB,
far below Garmin Connect's upload limits.  Per-second CPU: 25 `encodeMg` calls × 3 axes,
13 `setData` – negligible against the 120 k-step watchdog.

---

## 4. Physics and algorithms

### 4.1 What the wrist sensors see during a jump

Point-mass model (`tools/kitelog/physics.py`, `JumpModel`): after take-off the rider's
vertical motion is governed by gravity minus the kite's vertical pull, so the *net*
acceleration is `a_up` on the way up and `a_dn` on the way down, both much smaller than g:

```
ascent  h(t) = v0·t − ½·a_up·t²          t_up = v0 / a_up,   h_max = v0² / (2·a_up)
descent h(t) = h_max − ½·a_dn·(t − t_up)²  t_dn = √(2·h_max / a_dn)
airtime T = t_up + t_dn ;   for a known T:  v0 = T / (1/a_up + 1/√(a_up·a_dn))
```

The accelerometer measures *specific force* f = a − g.  Consequences:

| phase | |f| on the wrist | duration | detector cue |
|---|---|---|---|
| riding | ≈ 1 g + chop vibration (0.1–0.6 g RMS, 2–12 Hz, grows with speed) | – | high roughness |
| loading / pop | rises to 1.5–3.5 g (half-sine, 0.25–0.5 s) | ~0.4 s | **load peak** |
| flight | smooth 0.6–0.85 g (kite carries 60–85 % of body weight) | 1–10 s | **vibration gone**, |f| in band |
| landing | impact 2–7 g (0.1–0.35 s), then chop resumes | ~0.2 s | **impact peak** |

Numbers behind the ranges: 28.6 m in 8.5 s requires a_net ≈ 3.2 m/s² (|f| ≈ 0.67 g),
32.2 m in 10.15 s ≈ 2.5 m/s².  A free-fall detector (|f| < 0.3 g), as used for skate/ski
apps, would miss every kite jump.

### 4.2 Jump detection (`tools/kitelog/detect.py`)

All on the magnitude |f| (orientation-free):

1. `m4` = zero-phase low-pass 4 Hz (transients), `m1` = low-pass 1 Hz (flight level),
   `rough` = RMS over 0.32 s of (|f| − low-pass 3 Hz) (chop vibration),
   `rough_ref` = rolling 60 s median of `rough` (adapts to water state and speed).
2. Smooth mask: `rough < max(0.30·rough_ref, 0.035 g)` and `0.25 g < m1 < 1.45 g`.
   Segments ≥ 0.32 s; blips ≤ 0.6 s without an impact in between are bridged (the kite
   re-trims at the apex); data gaps ≤ 1.5 s inside a flight are bridged and flagged.
3. Take-off = **last** load peak ≥ 1.35 g within 1.2 s before the segment (`find_peaks`,
   so a wave bump earlier in the window is not mistaken for the pop).
4. Landing = onset of the first impact ≥ 1.6 g within 0.9 s after the segment, found by
   walking back from the raw-magnitude peak to flight level + 0.5 g.
5. Gates: GPS speed at take-off ≥ 3 m/s; minimum speed during the flight ≥ max(2.5 m/s,
   0.4 × take-off speed) (transitions and crashes lose their speed, jumps keep most);
   ≥ 60 % valid samples; airtime 0.3–14 s; **baro consistency**: for airtime > 2 s the
   barometer must show at least 0.5 × (1 m/s²)·T²/8, otherwise it is a quiet glide, not a
   jump.  If take-off or landing falls into a dropped record the jump is kept and flagged
   (`takeoff_in_gap` / `landing_in_gap`), both in a gap → rejected.

Every threshold is a field of `DetectParams`; they were set on the simulator and **must be
re-tuned on the first real sessions** (section 7).

### 4.3 Height (`tools/kitelog/height.py`)

Pressure → height: `h = (P_ref − P)/(ρ g)` with ρ from P and 15 °C (≈ 12.0 Pa per metre).
`P_ref` is a robust median over 14 → 1.5 s before take-off and 3 → 16 s after landing,
linearly interpolated across the jump (removes drift and the speed-dependent port effect
because both baselines are taken while riding).  Estimators, all written to `jumps.csv`:

| estimator | method | when used |
|---|---|---|
| `h_fit` (**primary**) | fit `JumpModel(a_up, a_dn)` + read-latency `lag ∈ [−0.2, 1.2] s` to the 1 Hz samples with T fixed by the accelerometer; apex of the fitted curve | baseline sane, fit RMSE < max(0.6 m, 12 %), apex ≤ 1.3× ballistic bound |
| `h_quad` | parabola through the three samples around the maximum | fit rejected but trace usable |
| `h_max_sample` | highest 1 Hz sample | diagnostics only (biased low) |
| `h_airtime` (fallback) | `a_eff·T²/8`, a_eff = 3.2 m/s² (reproduces 28.6 m / 8.5 s); calibrate per rider from baro-validated jumps | barometer unusable (splash, port blocked) |

Why the fit works at 1 Hz: with T known the trajectory has only two shape parameters,
so even 2–4 samples pin the apex; the lag parameter absorbs the unknown latency of the
`rawAmbientPressure` read (it recovered a random 0–0.9 s lag with σ = 0.26 s in
simulation).

Error budget (metres): sensor noise 3 Pa ≈ 0.25; waves/zero level ±0.2 (same as the
published study); temperature assumption ±3 %; dynamic-pressure change between riding and
flying ≤ ~0.5 after baseline interpolation (0.15 of ½ρv² assumed – to be measured); wrist
posture change (arms extended in the air) ≈ +0.3 systematic; splash artefacts: detected via
fit RMSE → fallback.

### 4.4 Jump length, speed, tacks (`tools/kitelog/session.py`)

* `dist_speed` = ∫ GPS speed dt over [t_to, t_land] (primary, MAE ≈ 1 m in simulation);
  `dist_pos` = horizontal displacement between interpolated positions (MAE ≈ 2 m).
* Take-off speed, landing speed, max speed in the air from interpolated Doppler speed.
* Session: distance (speed integral), riding time (> 3 m/s), max 1 s and 10 s speed,
  tacks (heading reversal > 100° within 15 s at > 3 m/s), jump totals, data health
  (batches, duplicates, gaps).  Exports: `jumps.csv`, `session.json`, `track.gpx`,
  `map.html` (Leaflet), per-jump and session plots.

---

## 5. Watch app (repository root)

The Connect IQ project you scaffolded (app id `ad30935d-…`, `instinct2x`, API 3.4.0) now
contains these files; the scaffold's placeholder view/menu/layout were removed:

```
manifest.xml                 entry KiteLogApp; permissions Fit, FitContributor, Positioning, Sensor
monkey.jungle
resources/strings/strings.xml
resources/drawables/{drawables.xml, launcher_icon.svg}
resources/fitcontributor/fitcontributor.xml   (display metadata only; packed fields deliberately hidden)
source/KiteLogApp.mc         AppBase: wires Recorder + SensorHub (+ LiveJumpDetector), auto-saves on exit
source/SensorHub.mc          GPS config fallback chain, 25 Hz listener, raw pressure read per batch
source/Recorder.mc           session (SPORT_KITESURFING), 16 record fields, onBatch() packing, pause/save/discard
source/Pack.mc               encodeMg / packPair / packMeta  (mirrors tools/kitelog/packing.py and web/kitelog.js)
source/KiteLogView.mc        timer (big), speed in km/h (big), REC/PAUSED/READY, GPS bars, batch health
source/KiteLogDelegate.mc    START = start/pause, BACK = Save/Discard/Resume menu (Menu2), BACK idle = exit
source/LiveJumpDetector.mc   phase 2, off by default (LIVE_DETECTOR const in KiteLogApp.mc)
```

Key behaviours:

* **Start-up:** GPS on immediately (multi-band → SatIQ → GPS+GLONASS → GPS, via
  `hasConfigurationSupport`), accelerometer listener registered at
  `min(25, getMaxSampleRate())` with `:period => 1`.
* **Per batch (1 Hz):** read `rawAmbientPressure`, pack 25 samples into 13 doubles, set
  `press`, `tmr`, `meta`, bump `seq`.  All `setData` calls happen in one callback so the
  record always carries one consistent batch.
* **Buttons:** START toggles recording/pause; BACK opens Save/Discard/Resume while a session
  is open and exits when idle; `onStop` saves an open session so leaving the app never loses
  data.
* **Display:** 2-colour, large fonts, nothing drawn in the Instinct's top-right subwindow
  area.  Shows timer, speed (km/h), GPS quality bars, and `b<batches> n<samples>` so you can
  see at a glance that the sensor stream is alive (phase 1); jump count + last airtime in
  phase 2.
* **Memory:** no bitmaps except the 40×40 icon, no layouts, no large arrays – well inside
  96 KB.  The code is written for API 3.4 (no `timestamp` arrays, no SatIQ sub-modes).

The sources were written carefully against the API docs but **could not be compiled here**
(the SDK and device files need a Garmin developer login) – expect to fix a few compiler
nits on the first build.

### 5.1 Phase 2 – live jump counter (optional)

`LiveJumpDetector` is a 25-sample-per-second state machine: smooth run (|Δ|f|| < 90 mg,
0.3–1.4 g) ≥ 0.6 s after a load peak > 1.4 g, ended by an impact > 2 g → jump, airtime =
run length × 40 ms, height shown as `0.4·T²`.  On each jump `Recorder.onJump` can write a
LAP with `lap_air`/`lap_h` so Garmin Connect lists jumps as laps – **only after spike S2
confirms LAP fields do not count against the 16-field limit** (`ENABLE_LAP_FIELDS`).

---

## 6. Offline analysis

### 6.1 Browser report app (`web/`) – phone-friendly, no install

`web/index.html` + `fit.js` (FIT decoder incl. developer fields, Garmin "Export Original"
ZIP and .fit.gz unwrapping) + `kitelog.js` (line-by-line port of the Python pipeline) +
`report.js` (rendering).  Pick the file → tiles, sortable jump table with per-jump signal
panels, track with jump markers, OpenStreetMap view, session timeline, CSV/GPX export,
print-to-PDF, tunable thresholds and a "show rejected candidates" view for tuning.
Everything runs locally in the browser.  `web/test/compare.mjs` runs the JS pipeline on
the sample FIT and checks it against the Python result (`web/test/expected_jumps.csv`):
identical jumps, airtime and height differences 0.000, length within 0.5 m.
Hosting: `.github/workflows/pages.yml` publishes `web/` to `https://vuksv.github.io/kite-app/`
(Settings › Pages › Source: GitHub Actions).  Optional session archive + server-side
pipeline on Cloud Run: `cloud/`, `Dockerfile`, `.github/workflows/deploy-cloud-run.yml`,
setup in `docs/CLOUD.md`.  See `docs/ANALYSIS.md` for the end-to-end workflow.

### 6.2 Python pipeline (`tools/`)

```
pip install -r tools/requirements.txt
cd tools
python simulate_session.py --out out/sim --duration 1500 --seed 7 --fit      # synthetic session (+ synthetic.fit)
python analyze.py out/sim/synthetic.fit --truth out/sim/truth.json --plots   # or a real .fit from the watch
python validate.py --sessions 6 --duration 1200                              # Monte-Carlo over 7 scenarios
python -m pytest tests                                                       # packing equivalence tests
```

`analyze.py` prints the jump table and writes `jumps.csv`, `session.json`, `track.gpx`,
`map.html`, `session.png`, `jump_NN.png` next to the input.  The FIT reader uses the
official `garmin-fit-sdk`; the synthetic FIT writer (`fit_tool`) reproduces the exact
developer-field layout, and the decode of a synthetic file was checked bit-exact (packed
doubles identical, pressure within 0.004 Pa, speed within 0.5 mm/s).

Getting the file off the watch: Garmin Connect web › activity › ⚙ › *Export Original*
(zip with the .fit), or USB mass-storage mode (`System › USB Mode`) › `GARMIN/Activity/*.fit`.

---

## 7. Simulation and validation results

Simulator (`tools/kitelog/simulate.py`): 20–25 min sessions with beach idle, walking,
water start, 25–80 s tacks with transitions, occasional crashes, 0.5–18 m jumps
(log-uniform; a_up 2–5 m/s², a_dn 1.2–3.5 m/s², load pulse 0.25–0.5 s, impact 0.12–0.35 s,
15–40 % speed loss in the air), chop vibration scaled by speed and sea state, wave bumps,
slow arm-orientation wobble, 8-bit companding, batch/writer clock phases with jitter,
dropped records, barometer noise + drift + 0.15·½ρv² port coupling + splash transients +
0–0.9 s read latency + ±0.2 m wave height, GPS with correlated 1.5 m position noise,
0.25 m/s speed noise and dropouts.

Baseline scenario, 6 sessions, 81 jumps (`docs/summary_by_height_bin.csv`):

| true height | n | height bias | height MAE | MAPE | p90 abs | airtime MAE | length MAE |
|---|---|---|---|---|---|---|---|
| < 1 m | 4 | +0.06 m | 0.23 m | 25 % | 0.31 m | 0.02 s | 0.6 m |
| 1–2 m | 21 | +0.03 m | 0.26 m | 18 % | 0.50 m | 0.03 s | 0.8 m |
| 2–5 m | 31 | +0.21 m | 0.54 m | 17 % | 1.06 m | 0.01 s | 1.1 m |
| 5–10 m | 18 | +0.36 m | 0.50 m | 6.8 % | 1.16 m | 0.01 s | 0.9 m |
| > 10 m | 7 | +0.09 m | 0.22 m | 1.8 % | 0.46 m | 0.02 s | 0.9 m |

Across scenarios (`docs/summary_by_scenario.csv`):

| scenario | recall | false pos./h | airtime MAE | height MAPE (best) | fit used | airtime-only model MAPE |
|---|---|---|---|---|---|---|
| baseline | 1.00 | 0.0 | 0.02 s | 14 % | 95 % | 22 % |
| glassy water (chop 0.3) | 1.00 | 0.0 | 0.03 s | 11 % | 91 % | 24 % |
| rough water (chop 2) | 1.00 | 0.0 | 0.03 s | 10 % | 97 % | 24 % |
| noisy barometer (12 Pa) | 0.98 | 0.0 | 0.12 s | 26 % | 73 % | 24 % |
| strong port/wind coupling (0.4) | 0.99 | 0.0 | 0.03 s | 11 % | 99 % | 25 % |
| splashy (40 splashes/h) | 0.97 | 0.0 | 0.03 s | 18 % | 79 % | 27 % |
| 2 % dropped records, 10 % GPS loss | 0.98 | 0.0 | 0.06 s | 12 % | 97 % | 26 % |

Reading: airtime is essentially exact (two 25 Hz transients); the physics fit beats the
naive maximum (22 %) and the airtime-only model (22 %) clearly and is within the published
accuracy of WOO/Surfr for jumps above 2 m; small jumps are limited by the 0.25 m
barometer noise and wave height – same as every other product.  Figures:
`docs/figures/jump_profile_big.png` (16 m jump, all four signals), `jump_profile_small.png`,
`session_overview.png`, `validation_scatter.png`.

**What the simulation cannot tell you** (and the field tests must): the real wrist
vibration contrast between riding and flying, the real load/impact amplitudes at the
wrist, the Instinct's barometer noise, latency and behaviour when wet, the actual rate of
dropped/duplicated batches, and whether the "Every Second" setting really gives 1 Hz
records for a Connect IQ session.

---

## 8. Test plan

### Phase 0 – device spikes (one evening, before writing more code)

| id | test | pass criterion | fallback |
|---|---|---|---|
| S1 | Build, sideload, watch `b<n> n<samples>` on the screen | batches increment every second with 25 samples | period/sampleRate variants; power-cycle (forum); `getMaxSampleRate()` value |
| S2 | Start a session with all 16 record fields (+ the 3 LAP/SESSION fields with `ENABLE_LAP_FIELDS=true`), record 2 min, save | no crash; FIT decodes with `analyze.py`; `seq` continuous | if 16 + laps crash → keep laps off; if 16 crash → 12 doubles (24 samples) + press + tmr + meta |
| S3 | Record 10 min sitting still, then walk up/down 3 floors (~10 m) | `press` noise σ (expect 1–5 Pa), updates every second, ~12 Pa per metre, lag estimate stable | if `rawAmbientPressure` is null/stale use `ambientPressure`, else `Sensor.Info.pressure` |
| S4 | Watch setting *Data Recording = Every Second*, 20 min walk with GPS | records every second, no `seq` gaps, GPS track on the map, speed plausible | if Smart recording drops records: document, keep Every Second, measure gap rate |
| S5 | Bike/car ride at 20–40 km/h | speed display, `dist_speed` vs odometer within 2 %, battery drain per hour noted | – |

### Phase 1 – land tests of the algorithms

* **Standing/box jumps** with the watch on the wrist: set `DetectParams(level_min_g=0.0)`
  (free fall instead of kite support) and check airtime against video at 60 fps
  (h = g T²/8 for a real free-fall jump, e.g. 0.5 s → 0.31 m).  Calibrates take-off/landing
  timing offsets.
* **Vibration proxy:** ride a bike on gravel vs. smooth road to see what `rough_ref` and the
  roughness floor look like on this sensor; adjust `rough_floor_g`.
* **Connect IQ simulator:** play back a recorded FIT (Simulation › Activity Data › Play) for UI work.

### Phase 2 – first water sessions

1. Session 1–3: record only, no live detector.  Run `analyze.py --plots`, look at every
   candidate plot (accepted and rejected), count jumps from memory/GoPro, and tune
   `rough_ratio`, `rough_floor_g`, `takeoff_min_peak_g`, `landing_min_peak_g`,
   `level_min/max_g`.  Expect the first tuning to change thresholds by a factor of ~2.
2. Reference: beach camera at 60 fps on a tripod for 5–10 jumps (stick height known),
   or a friend's WOO/Surfr on the same session, to calibrate `a_eff`, the timing offsets
   and the wrist-posture bias (+0.3 m expected).
3. Barometer field facts: plot `press` against GPS speed on each tack (expect a ½ρv²
   dependence), count splash artefacts, decide whether to wear the watch under the wetsuit
   sleeve or on the harness/upper arm (less spray, more body-representative).
4. Acceptance for v1: recall ≥ 90 % of jumps > 2 m, < 1 false positive per hour, airtime
   within 0.15 s of video, height within 20 % of video above 3 m.

### Phase 3 – live feedback

Enable `LIVE_DETECTOR`, compare its count with the offline count over several sessions,
then enable LAP fields (if S2 allowed them) so jumps appear in Garmin Connect.

---

## 9. Risks and mitigations

| risk | likelihood | mitigation |
|---|---|---|
| Developer-field limit < 16 or DOUBLE unsupported on this firmware | low–medium | S2; fallback layouts above (24 samples/s, or 10-bit SINT32 packing at 16 samples/s) |
| Batch drops at the FIT writer boundary | certain, rare | `seq`/`tmr`; pipeline bridges and flags; measured in S4 |
| Accelerometer callback silent on device | reported on other models | S1, power-cycle, register in `onStart` only |
| Barometer reading stale, wet or wind-biased | medium | baseline interpolation, fit-RMSE rejection, airtime fallback, port placement choice, S3 |
| Wrist vibration contrast too small on flat water | medium | adaptive thresholds, baro consistency gate, transients; tune in phase 2 |
| Watch setting left on Smart recording | user error | checklist; `seq` gaps show it immediately |
| App memory/watchdog | low | tiny app; no per-sample allocations beyond the SDK arrays |
| Garmin Connect shows 13 odd developer fields | cosmetic | hidden from `fitcontributor.xml`; harmless |

---

## 10. Roadmap (effort estimates for one developer)

1. Phase 0 spikes – 1 evening.
2. Recording MVP (compile the skeleton, UI polish, save/discard flow) – 1–2 days.
3. First three water sessions + threshold tuning + calibration – 2–3 sessions, ½ day
   analysis each.
4. Live counter + lap fields – 1 day.
5. Nice-to-haves: per-tack stats page on the watch, settings for units, auto-pause when
   stationary, a_eff calibration stored on the watch for the live height number.

---

## 11. File index

```
PLAN.md                      this document
README.md                    build, record, analyse – quick start
docs/ANALYSIS.md             getting the file off the watch, reading and tuning the report
docs/figures/                jump_profile_big.png, jump_profile_small.png, session_overview.png, validation_scatter.png, map_example.html
docs/summary_by_scenario.csv, docs/summary_by_height_bin.csv   validation tables
manifest.xml, source/, resources/   Connect IQ watch app (Monkey C)
web/                         browser report app: index.html, style.css, fit.js, kitelog.js, report.js
web/sample/synthetic.fit     simulated session in the exact watch format (+ synthetic_truth.json)
web/test/compare.mjs         JS-vs-Python parity test (expected_jumps.csv)
tools/kitelog/               packing, physics, simulate, fitio, detect, height, session, report
tools/simulate_session.py, tools/analyze.py, tools/validate.py, tools/tests/test_packing.py
```
