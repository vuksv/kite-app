# kite-app · KiteLog

A personal Garmin Connect IQ app for kiting on the **Instinct 2X Solar** (`instinct2x`),
plus the offline analysis that turns a recorded session into jump height, airtime, jump
length, speed, tacks and a track on a map. Not published to the Connect IQ store.

**Read `PLAN.md` first** – it has the research (what the watch and SDK can and cannot do),
the physics, the FIT data format, the simulation results and the test plan.
`docs/ANALYSIS.md` explains how to get the file off the watch and read the report.

## Repository layout

```
manifest.xml, monkey.jungle, source/, resources/   Connect IQ watch app (build from the repo root)
web/                                               report app: open web/index.html, pick the FIT/ZIP, get the report (runs in the browser)
tools/                                             Python pipeline, session simulator, Monte-Carlo validation, tests
docs/                                              ANALYSIS.md, validation tables, figures
PLAN.md                                            the implementation & test plan
```

## Build and install on the watch

You need the [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/), the Monkey C
extension for VS Code and a developer key.

- **Simulator:** open the folder in VS Code, `Monkey C: Run` with `instinct2x`. The
  simulator does not generate accelerometer data unless you play an activity
  (Simulation › Activity Data › Play).
- **Watch:** `Monkey C: Build for Device` (or the command below), set the watch to
  *System › USB Mode › Mass Storage*, plug it in and copy `KiteLog.prg` to `GARMIN/APPS/`.
  Eject, unplug; KiteLog appears in the activity list (START from the watch face).
  ```
  monkeyc -d instinct2x -f monkey.jungle -o bin/KiteLog.prg -y <developer_key> -r
  ```
- **Watch settings:** *System › Data Recording › Every Second* (otherwise the FIT writer
  drops batches) and *Satellites › All + Multi-Band* (the app also requests it).
- **Crash / debug log:** create an empty `GARMIN/APPS/LOGS/KiteLog.TXT` before testing to
  capture `System.println` output; crashes are written to `GARMIN/APPS/LOGS/CIQ_LOG.YML`.

The app compiles with SDK 9.3 and runs in the simulator (UI, session save, 16 developer
fields declared). The simulator produces no accelerometer data, so the data path is first
proven on the watch: `PLAN.md` §8 lists the five device spikes to run first (accelerometer
callback, developer-field limit, barometer, record rate, GPS).

## Recording a session

1. Start **KiteLog** on the watch; it starts acquiring GPS immediately (bars top-left).
2. **START** begins recording (shows `REC`, timer, speed in knots and a batch counter that
   should tick every second). START again pauses.
3. **BACK** opens *Resume / Save / Discard*. Leaving the app with a session open saves it.
4. Sync. The activity shows up as *Kiteboarding* in Garmin Connect.

## Analysing

Phone: export the activity from connect.garmin.com (*Export Original*), open the report page
(`web/index.html`, or the GitHub Pages URL once enabled) and pick the zip. Computer: the same,
or `python tools/analyze.py file.fit --plots`. Details in `docs/ANALYSIS.md`.

## Tests

```
cd tools && pip install -r requirements.txt
python -m pytest tests                  # Monkey C <-> Python sample packing equivalence
python validate.py                      # detection / height accuracy on simulated sessions
node web/test/compare.mjs web/sample/synthetic.fit web/test/expected_jumps.csv   # JS pipeline == Python pipeline
```
