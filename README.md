# Audio Mixer MVP

This project is a Linux-first audio mixer controller inspired by Voicemeeter's routing model.

It does **not** implement a custom driver. It builds on PipeWire or PulseAudio virtual devices through `pactl`.

## Current feature set

- Dedicated desktop/system playback strip:
  - `VM_System`
- Two extra virtual playback strips for app-specific routing:
  - `VM_Input_1`
  - `VM_Input_2`
- Two virtual output buses:
  - `VM_Bus_B1`
  - `VM_Bus_B2`
- Two virtual source devices exposed for capture apps:
  - `VM_Output_B1`
  - `VM_Output_B2`
- Two hardware input slots:
  - `Hardware In 1`
  - `Hardware In 2`
- Two hardware output buses:
  - `A1`
  - `A2`
- Routing matrix to send each strip to:
  - `A1`
  - `A2`
  - `B1`
  - `B2`
- Output test buttons and input preview test buttons
- Editable strip labels in the GUI
- Per-strip mute in the GUI
- Per-strip volume faders in the GUI
- Optional mic-triggered ducking for `System Playback`, `Input 1`, and `Input 2`
- Keyboard shortcuts to select a specific strip fader in the GUI, configured from a dedicated keybinds window
- Saved config in `mixer-config.json`
- Re-apply saved routing with `apply-saved`
- Mono hardware input normalization before routing, so mono mics behave more predictably on stereo outputs

## Routing model

Think of the mixer like this:

- `VM_System`:
  general desktop audio
- `VM_Input_1`:
  app-specific virtual playback device, for example Discord
- `VM_Input_2`:
  app-specific virtual playback device, for example Spotify
- `Hardware In 1` / `Hardware In 2`:
  physical microphones or capture devices
- `A1` / `A2`:
  physical output buses that point to real sinks such as speakers or headphones
- `B1` / `B2`:
  virtual buses that can be captured by apps through `VM_Output_B1` and `VM_Output_B2`

Example:

- Desktop audio goes to `VM_System`
- Discord goes to `VM_Input_1`
- Spotify goes to `VM_Input_2`
- Mic goes to `Hardware In 1`
- Route chosen strips to `A1` for your speakers
- Route chosen strips to `B1` for Discord, OBS, or another app to record

## Why this approach

Voicemeeter-style routing needs virtual devices at the OS level. On Linux, the practical route is PipeWire or PulseAudio virtual sinks, remapped sources, and loopbacks instead of trying to reproduce a Windows audio-driver stack.

This gives you:

- separate playback targets for different apps
- virtual outputs other apps can capture
- repeatable routing
- a small controller app instead of manual `pactl` commands

## Requirements

- Linux
- PipeWire with PulseAudio compatibility, or PulseAudio
- `pactl`
- Python 3
- Tkinter if you want the GUI

On Arch, if the GUI fails with missing Tk libraries:

```bash
sudo pacman -S tk
```

## Run

GUI:

```bash
python3 app.py --gui
```

CLI help:

```bash
python3 app.py --help
```

## CLI commands

List audio devices and virtual sources:

```bash
python3 app.py list
```

Create or repair all virtual devices:

```bash
python3 app.py create-devices
```

Make `VM_System` your default desktop playback sink:

```bash
python3 app.py set-default-system
```

Test an output sink:

```bash
python3 app.py test-output --sink alsa_output.pci-0000_00_1f.3.analog-stereo
```

Preview an input through a sink for a few seconds:

```bash
python3 app.py test-input \
  --source alsa_input.usb-YourMic-00.mono-fallback \
  --sink alsa_output.pci-0000_00_1f.3.analog-stereo
```

Apply the last saved config:

```bash
python3 app.py apply-saved
```

Control a running GUI instance from another process:

```bash
python3 app.py select-strip hw1
python3 app.py select-strip in1
python3 app.py volume-up
python3 app.py volume-down --steps 2
```

Apply routing directly from CLI:

```bash
python3 app.py apply \
  --a1 alsa_output.pci-0000_00_1f.3.analog-stereo \
  --a2 bluez_output.xx_xx_xx_xx_xx_xx.a2dp-sink \
  --hw1 alsa_input.usb-YourMic-00.mono-fallback \
  --route sys:A1 \
  --route sys:B1 \
  --route hw1:B1 \
  --route vi1:A1 \
  --route vi2:A2
```

## Route aliases

- `hw1`, `hw2` = hardware inputs
- `sys` = `VM_System`
- `in1`, `in2` = bottom two virtual input strips
- `vi1`, `vi2` = virtual input strips
- `A1`, `A2` = physical output buses
- `B1`, `B2` = virtual output buses

## Recommended setup

1. Run `python3 app.py create-devices`
2. Run `python3 app.py set-default-system`
3. Open the GUI with `python3 app.py --gui`
4. Select your real playback devices for `A1` and `A2`
5. Select your physical mic/input devices for `Hardware In 1` and `Hardware In 2`
6. Use `Test A1`, `Test A2`, `Test In 1`, and `Test In 2` to confirm selections
7. Rename strips if useful, for example:
   - `System Playback` -> `Desktop`
   - `VM Input 1` -> `Discord`
   - `VM Input 2` -> `Spotify`
8. Enable the routes you want
9. Click `Apply Routing`

Then assign apps:

- leave normal desktop audio on `VM_System`
- set Discord playback to `VM_Input_1`
- set Spotify playback to `VM_Input_2`
- set Discord or OBS input to `VM_Output_B1` if you want to capture bus `B1`

## Config and persistence

When you click `Apply Routing`, the app saves:

- `A1` and `A2` sink selections
- hardware input selections
- route matrix state
- custom strip labels
- strip mute state
- strip volume levels

Notes:

- Open `Keybinds`, click `Set Key` next to a strip, then press the shortcut you want to bind
- The keybind editor opens in its own window and captures the actual key combination directly
- The same window can also bind `Selected Fader Up` and `Selected Fader Down` shortcuts, with a configurable step size
- The running GUI exposes a local control socket at `/tmp/audio-mixer-control.sock` so external tools such as Hyprland can drive strip selection and volume
- The `Ducking` section can monitor `Hardware In 1` or `Hardware In 2` and temporarily lower `System Playback`, `Input 1`, and `Input 2` while you speak

Saved file:

- `mixer-config.json`

Runtime module state is tracked separately in:

- `.mixer-state.json`

After reboot or audio-server restart, reopen the app or run:

```bash
python3 app.py apply-saved
```

## Notes on buses

- `VM_Bus_B1` and `VM_Bus_B2` are internal virtual sinks used for routing
- `VM_Output_B1` and `VM_Output_B2` are the source devices capture apps should usually use
- `VM_Output_B1` is generally a better choice for Discord than a raw `.monitor` source

## Notes on mono microphones

Some microphones expose a mono source. The app now attempts to normalize mono hardware inputs into stereo before routing so they behave better on stereo outputs and buses.

If a mic still sounds wrong, inspect the device with:

```bash
python3 app.py list
pactl -f json list sources
```

## Current limits

- Linux only
- No EQ, compression, gate, or effects
- No true per-strip gain sliders yet
- No solo yet
- No per-app assignment UI inside the app
- Routing is restored by reopening the app or running `apply-saved`, not by a background daemon
- Uses `module-loopback`, so latency depends on your PipeWire/PulseAudio setup

## Next useful upgrades

- real per-strip gain sliders
- solo controls
- auto-start restoration
- per-app stream assignment inside the UI
- better live metering
- JACK / PipeWire graph inspection
