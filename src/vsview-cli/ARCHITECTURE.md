# Development Workflows

## Automatic Rebuilds

```powershell
cargo watch -C src/vsview-cli -w rust -s "uv run maturin develop"
```

## Type Stub Generation

```powershell
cargo run --manifest-path src/vsview-cli/rust/Cargo.toml --bin stub_gen
```
