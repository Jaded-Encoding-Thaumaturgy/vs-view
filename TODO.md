# Project Roadmap & TODO

## Porting from VSPreview

These features are present in VSPreview and need to be brought over.

- [ ] VFR support + timecode files loading
- [ ] Audio support
- [ ] Scening (+ API for custom notches on the timeline)
- [ ] Pipette
- [ ] Benchmark (Likely a wrapper for `vspipe`)
- [ ] SAR/DAR support
- [ ] Cropping assistant
- [ ] Alpha support

## Planned Plugins

- [ ] Frame props
- [ ] Slowpics
- [ ] Native-res ([Repository](https://github.com/Jaded-Encoding-Thaumaturgy/native-res))
- [ ] Histogram (NumPy based?)
- [ ] Graph visualizer

## Enhancements & Tasks

- [ ] Workspace API definition
- [ ] Downgrade Python minimum requirement to 3.12
- [ ] Relax VS minimum version
- [ ] View Menu: Toggle visibility for toolpanels/tooldocks
- [ ] Theming: Add option for loading external QSS themes
- [ ] Allow plugins to have configurable shortcuts.
- [ ] Allow arbitrary hooks as plugin such as custom chroma resizers.

## Known Issues

- Waking up VapourSynth during environment creation locks the main thread and can take a few seconds (Only a Windows issue?).
- Workspace layout (toolpanels/tooldocks) is not currently saved.
- Target frame when switching tabs is based on the number and not the timestamp.
