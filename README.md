# Audio Mixer

Linux-first virtual audio mixer inspired by Voicemeeter.

Built with Python, PipeWire/PulseAudio, and `pactl` — no custom audio driver required.

## Features

* Virtual playback devices:

  * `VM_System`
  * `VM_Input_1`
  * `VM_Input_2`
* Hardware inputs:

  * `Hardware In 1`
  * `Hardware In 2`
* Hardware output buses:

  * `A1`
  * `A2`
* Virtual buses:

  * `B1`
  * `B2`
* Per-strip:

  * volume
  * mute
  * solo
  * labels
* App routing window
* Parametric EQ system
* Ducking support
* Keybind editor
* Remote control socket/API
* Saved routing + persistence

---

## Requirements

* Linux
* PipeWire or PulseAudio
* Python 3
* `pactl`
* Tkinter (GUI)

Arch Linux:

```bash
sudo pacman -S tk
```

---

## Install

```bash
git clone https://github.com/Deeppy1/audio-mixer.git
cd audio-mixer
```

(Optional virtual environment)

```bash
python -m venv .venv
source .venv/bin/activate
```

---

## Run

GUI:

```bash
python3 app.py --gui
```

CLI help:

```bash
python3 app.py --help
```

---

## Main Commands

List devices:

```bash
python3 app.py list
```

Create virtual devices:

```bash
python3 app.py create-devices
```

Set desktop playback sink:

```bash
python3 app.py set-default-system
```

Apply saved config:

```bash
python3 app.py apply-saved
```

---

## Routing Model

### Inputs

* `VM_System` → desktop audio
* `VM_Input_1` → app-specific playback
* `VM_Input_2` → app-specific playback
* `Hardware In 1/2` → microphones/interfaces

### Outputs

* `A1/A2` → real playback devices
* `B1/B2` → virtual buses for OBS/Discord/etc

Example:

| Source  | Routes  |
| ------- | ------- |
| Desktop | A1 + B1 |
| Discord | A1      |
| Mic     | B1      |
| Spotify | A2      |

---

## EQ System

Includes:

* Parametric EQ
* Live response graph
* Draggable bands
* Frequency / gain / Q controls
* Presets
* Bypass + reset

---

## App Routing

The `Apps` window can:

* Move active streams between mixer inputs
* Save rules by app identity
* Restore assignments automatically

---

## Remote Control

The GUI exposes a local control socket:

```text
/tmp/audio-mixer-control.sock
```

Example commands:

```bash
python3 app.py select-strip in1
python3 app.py volume-up
python3 app.py volume-down --steps 2
```

Useful for:

* Hyprland
* scripts
* Stream Deck setups

---

## Config Files

Saved config:

```text
mixer-config.json
```

Runtime state:

```text
.mixer-state.json
```

After reboot/audio restart:

```bash
python3 app.py apply-saved
```

---

## Current Limitations

* Linux only
* No compressor/gate yet
* Loopback latency depends on PipeWire/PulseAudio config
* No dedicated background daemon yet

---

## Inspiration

Inspired by Voicemeeter-style routing using Linux virtual audio devices and PipeWire.
