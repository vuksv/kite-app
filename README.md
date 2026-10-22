# kite-app

A personal Garmin Connect IQ watch app for kiting.

This is built only for my own **Garmin Instinct 2X Solar** (`instinct2x`). It isn't tested on other devices or published to the Connect IQ Store.

## Building

You need the [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/) and the Monkey C extension for VS Code.

- **Run in the simulator:** open the folder in VS Code and run `Monkey C: Run` with `instinct2x` as the target.
- **Install on the watch:** run `Monkey C: Build for Device`, then copy the `.prg` file to `GARMIN/APPS/` on the watch over USB.
