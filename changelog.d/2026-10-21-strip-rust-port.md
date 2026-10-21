### Removed (2026-10-21 — unfinished Rust port)
- Remove upstream's unfinished native Rust port from this fork: the `rust/`
  tree, its `evals/rust_port/` and `evals/rust_baseline/` harnesses and
  recorded port evidence, the Rust daemon image files, and the Rust CI
  workflow. Nothing installed or ran the port; the Python daemon and shim
  are unchanged.
- CI's Rust-only pull request lane is gone with it, and so is the earlier
  scoping of Rust CI to port inputs. Changes under `rust/` now run the full
  Python suite like any other change.
