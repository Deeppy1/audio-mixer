# Parametric EQ Integration Plan

## Objective

Add a professional DAW-style parametric EQ window to the existing mixer without rewriting the audio backend, routing engine, or current UI shell.

## Constraints

- Keep `PactlBackend` routing behavior unchanged
- Preserve existing device, bus, mute, solo, volume, app assignment, ducking, and command socket flows
- Keep EQ UI logic separate from EQ DSP/response math
- Reuse the existing dark neon theme and reusable widget patterns
- Persist EQ state through the existing config file without changing current config semantics

## Implementation Strategy

### 1. Add a dedicated EQ math/state module

- create a reusable EQ state model with multiple bands
- support per-band frequency, gain, and Q
- support presets, bypass, reset, and serialization
- calculate smooth frequency response curves independently from the mixer backend

### 2. Add a dedicated EQ UI module

- create a plugin-style `Toplevel` EQ window
- add a real-time response graph on a `Canvas`
- add draggable EQ points mapped to frequency and gain
- add focused controls for the selected band
- add preset, bypass, and reset actions
- keep the visual language aligned with the neon mixer styling

### 3. Integrate EQ into the existing mixer UI

- add an EQ entry point from the main window and strip cards
- keep one reusable EQ window instance that can retarget to the selected strip
- sync strip labels into the EQ title/context
- persist EQ state per strip

### 4. Extend config persistence only

- preserve existing config keys
- store EQ state under a dedicated `eq_settings` payload
- ensure routing saves continue to retain EQ settings

### 5. Verification

- run `python3 -m py_compile` on the project
- verify the new modules import cleanly
- confirm the existing backend API usage remains intact
