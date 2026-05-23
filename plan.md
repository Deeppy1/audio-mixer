# Audio Mixer Frontend Modernization Plan

## Objective

Modernize the existing Tkinter frontend into a commercial-grade Linux audio mixer interface while keeping the current Python backend, PipeWire integration, routing logic, persistence, and command socket behavior intact.

## Current Architecture

### Backend boundary

`mixer/audio_backend.py` owns:

- device discovery
- virtual device creation
- routing application
- live strip volume updates
- output/input testing
- config persistence
- app stream reassignment

The frontend should continue using the existing `PactlBackend` methods and data models without changing their behavior.

### Frontend boundary

`mixer/app.py` currently owns:

- Tk root creation
- ttk widget construction
- all layout code
- window styling decisions
- control state via `StringVar` / `BooleanVar` / `IntVar`
- command dispatch to `PactlBackend`
- remote control socket handling
- ducking monitor UI lifecycle
- keybind capture UI

The current GUI is functionally complete but visually dated and structurally monolithic.

## Compatibility Requirements

The refactor must preserve:

- `PactlBackend` API usage
- saved config semantics in `mixer-config.json`
- routing matrix behavior
- A1/A2/B1/B2 assignment behavior
- mute/solo logic
- volume update flow and debouncing
- ducking behavior
- app assignment behavior
- keybind behavior
- remote control socket behavior

## Refactor Strategy

### Phase 1: Documentation and separation

- document the integration boundary
- extract theme tokens and ttk style configuration into a dedicated UI module
- add reusable UI helpers/components so visuals stop living inline in `mixer/app.py`

### Phase 2: Main window redesign

- replace the flat form layout with a modern dashboard shell
- add a top action bar with clearer grouping
- move bus/device configuration into a dedicated sidebar
- render strips as card-based channel strips
- keep routing controls and labels familiar but modernize spacing, alignment, and hierarchy
- add visual emphasis for selected strips and active routes

### Phase 3: Visual improvements

- dark neon palette with glass-style panels
- stronger typography hierarchy
- custom button, combobox, scale, and checkbutton styling
- animated strip meters driven by current UI state
- route toggles with active glow state

### Phase 4: Secondary windows

- restyle Apps, Keybinds, and Ducking windows to match the main UI
- improve density and readability without changing behaviors

### Phase 5: Verification

- run static validation with `python3 -m py_compile`
- confirm no backend method signatures or call sites were broken
- review for config and command-socket compatibility

## Implementation Notes

- Tkinter/ttk remains the UI toolkit
- no backend rewrite
- no routing logic rewrite
- no feature removal
- UI-only modules should be additive and imported by `mixer/app.py`

## Expected Deliverables

- `plan.md`
- extracted styling/component module(s)
- modernized main mixer window
- restyled supporting dialogs
- preserved backend compatibility
