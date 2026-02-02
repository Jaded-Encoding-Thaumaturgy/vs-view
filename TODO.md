# Project Roadmap & TODO

## Porting from VSPreview

These features are present in VSPreview and need to be brought over.

- [x] VFR support
- [ ] ~~timecode files loading~~
- [x] Audio support
- [ ] Scening (+ API for custom notches on the timeline)
- [ ] Pipette
- [ ] Benchmark (Likely a wrapper for `vspipe`)
- [x] SAR/DAR support
- [ ] Cropping assistant
- [ ] Alpha support

## Planned Plugins

- [x] Frame props
- [ ] Slowpics
- [ ] Native-res ([Repository](https://github.com/Jaded-Encoding-Thaumaturgy/native-res))
- [ ] Histogram (NumPy based?)
- [ ] Graph visualizer

## Enhancements & Tasks

- [ ] Workspace API definition
- [x] Downgrade Python minimum requirement to 3.12
- [x] Relax VS minimum version
- [x] View Menu: Toggle visibility for toolpanels/tooldocks
- [ ] Theming: Add option for loading external QSS themes
- [x] Allow plugins to have configurable shortcuts.
- [x] Allow arbitrary hooks as plugin such as custom chroma resizers.

## Known Issues

- Waking up VapourSynth during environment creation locks the main thread and can take a few seconds (Only a Windows issue?).
