# EQ Implementation Changes

## Summary

This document records the changes made to add a professional parametric EQ system to the existing Linux audio mixer application without rewriting the existing routing backend.

## Files Added

### `mixer/eq.py`

Added a dedicated EQ state and DSP math module.

Included:

- `EQBand` state model
- `EQState` container with serialization helpers
- frequency/gain/Q clamping
- log-scale frequency mapping helpers
- preset definitions
- response curve generation for the graph
- per-band magnitude math for low shelf, peak, and high shelf bands

This module is UI-independent and keeps EQ math separate from the mixer frontend.

### `mixer/eq_ui.py`

Added a dedicated EQ window and graph UI module.

Included:

- `ParametricEQWindow` for the plugin-style editor window
- `EQGraphCanvas` for the live response display
- draggable band points
- selected-band controls for frequency, gain, and Q
- preset selection
- bypass and reset controls
- neon dark styling aligned with the existing mixer UI

## Files Modified

### `plan.md`

Replaced the previous modernization plan with an EQ-specific implementation plan covering:

- architecture constraints
- separation of UI and DSP/state logic
- persistence strategy
- integration strategy
- verification approach

### `mixer/audio_backend.py`

Extended the backend to preserve and apply EQ settings.

Persistence changes:

- preserved `eq_settings` inside normal config saves
- added `save_eq_settings()`

Routing changes:

- extended `apply_routing()` to accept `eq_settings`
- kept the original loopback path for strips with flat or bypassed EQ
- added a real DSP bridge path for strips with active EQ

DSP bridge changes:

- added EQ-enabled route detection
- added ffmpeg-based Pulse/PipeWire bridge startup
- built ffmpeg filter chains from strip EQ band settings
- supported low shelf, peak, and high shelf style filters
- tracked active EQ bridge processes in mixer state
- terminated old bridge processes when routing is reapplied

Volume control changes:

- updated live strip volume handling so it can control:
  - classic loopback routes
  - ffmpeg EQ bridge routes

State tracking changes:

- added `eq_bridge_processes` to backend state
- added sink-input lookup by stream metadata for EQ bridge routes

### `mixer/app.py`

Integrated the EQ feature into the existing GUI.

UI entry points:

- added a top-bar `EQ` button
- added an `EQ` button on each strip card

State changes:

- added per-strip `EQState` storage
- added one reusable EQ window instance
- loaded `eq_settings` from saved config
- persisted EQ changes back to config

Routing integration:

- passed per-strip EQ state into backend `apply_routing()`
- added a short debounce so EQ edits reapply routing automatically
- ensured saved config reapply includes EQ state

UX changes:

- selected strip summary now shows EQ preset or bypass state
- EQ window opens against the selected strip label/context

Shutdown handling:

- closes the EQ window on app exit
- cancels pending EQ reapply callbacks on app exit

## Runtime Behavior Change

Before these changes:

- the EQ graph/UI existed only as visual state
- changing EQ controls did not affect real audio

After these changes:

- strips with active EQ are routed through a real ffmpeg DSP bridge
- strips with bypassed or flat EQ continue to use the original loopback path
- moving EQ controls causes routing to be reapplied so the audio path updates

## Verification Performed

Static verification:

- ran `python3 -m py_compile app.py mixer/*.py updatecheck.py version.py`

Runtime verification:

- confirmed `ffmpeg` is available with Pulse input/output support
- confirmed a live ffmpeg EQ bridge can be created
- confirmed the bridge appears as a controllable sink input
- confirmed the bridge can be shut down cleanly

## Known Limitation

The EQ is now audible, but parameter edits are not yet sample-accurate live automation.

Current behavior:

- changing EQ settings triggers a short debounced routing reapply
- this restarts the affected DSP bridge

Practical effect:

- EQ changes are audible
- rapid continuous dragging may cause brief interruptions instead of seamless plugin-style automation

## Design Intent Preserved

The implementation keeps the original project structure intact:

- no backend rewrite
- no routing engine rewrite
- no removal of existing routing features
- existing device discovery, buses, mute, solo, volume, app routing, and ducking remain in place
- EQ UI/state logic remains separate from the routing backend
