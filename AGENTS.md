# Repository Guidelines

## Project Structure & Module Organization
Meshcore TUI now lives inside `src/` as a [Textual](https://textual.textualize.io/) application. Treat the directories below as canonical:
- `src/app.py`: entry point that wires the app modes (`settings`, `chat`, `channel`). Extend `MeshCoreTuiApp.MODES` instead of rolling a new `App` subclass when adding screens.
- `src/chat.py`: channel/user chat screens plus shared list item helpers; add new panes as subclasses of `BaseChatScreen` to inherit split-view behavior and bindings.
- `src/settings.py`: contains `SettingsScreen` for configuration copy; add new configuration panes here while keeping heavy logic in providers/services.
- `src/dialog.py`: lightweight modal helpers (`PromptDialog`) that can be re-used across screens.
- `src/data.py`: shared data models (`MeshCoreChannel`, `MeshCoreNode`, `BaseMessage`) and provider contracts; keep transport/adapters here.
- `src/services/config_service.py`: config loader/writer plus typed dataclasses and defaults.
- `src/services/meshcore_service.py`: wraps the MeshCore SDK, manages subscriptions, and feeds providers real-time contact/channel/message updates.
- `*.tcss`: each screen owns a `.tcss` file (e.g., `app.tcss`, `chat.tcss`) that matches the module name.

Tests still belong in `tests/` (add a folder if missing) and shared fixtures sit under `assets/fixtures/`. Config files belong under `config/` with committed `.example` variants to help agents replay scenarios.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`: bootstrap dependencies the first time you clone or whenever requirements change.
- `cargo run -- --config config/dev.toml`: launches the TUI against the dev meshcore endpoint with hot reload logging enabled.
- `cargo build --release`: produces an optimized binary for packaging on embedded targets.
- `cargo fmt --all` and `cargo clippy --all-targets --all-features`: enforce formatting and lints before every push.
- `cargo test -- --nocapture`: runs unit and integration suites; keep logs visible for diagnosing layout regressions.
- `textual run --dev src/app.py`: faster feedback while tweaking layouts or CSS (auto-reloads on `*.py`/`*.tcss` changes).

When hacking on UI code, leave the dev server running in one pane and rely on the on-screen log (press `ctrl+shift+l` in Textual) plus stdout logging.

## Coding Style & Naming Conventions
Use Rust 2021 defaults for Rust crates and Black-compatible formatting (4 spaces) for Python/Textual modules. Modules and files use `snake_case`, types and components use `UpperCamelCase`, and consts use `SCREAMING_SNAKE_CASE`. Keep modules <200 lines; factor shared helpers into new modules rather than stacking more logic into `chat.py`. Document any public API, widget, or protocol handler with `///` (Rust) or triple-quoted docstrings (Python) explaining the meshcore concept surfaced. Keep layout concerns in `*.tcss`/`compose` methods and push data manipulation into `data.py` or future `src/services/` modules so redraws stay predictable.

Textual-specific rules:
- Always update the matching `.tcss` file when adding IDs, classes, or layout containers; unused selectors should be deleted.
- Wire bindings through `BINDINGS` arrays on each `Screen` and keep descriptions/shortcuts in sync with on-screen tooltips.
- When adding dialogs, inherit from `ModalScreen` (see `PromptDialog`) and keep dismissal callbacks side-effect free.

## Testing Guidelines
Add fast unit coverage beside the code via `#[cfg(test)]` modules and name cases with the `given_state_when_action_then_result` style. For Python modules, add `pytest`-style tests either inline under `if __name__ == "__main__"` guards or in `tests/` until the Rust crate replaces the shim. Integration tests should stub meshcore endpoints with fixtures under `assets/fixtures/`. Aim for >80% coverage once Tarpaulin is wired in CI and include `textual` smoke tests that drive the fake data provider when possible. Record manual steps for interactive panes inside the PR description when automated checks cannot cover them (e.g., “switch to Channels with `1`, send fake message, verify indicator”).

## Commit & Pull Request Guidelines
Follow Conventional Commits, e.g., `feat(ui): add telemetry pane`. Each commit must compile and pass `cargo fmt`, `cargo clippy`, and `cargo test`. Rebase onto `main` before opening a PR. Pull requests need: a concise summary, linked issues (`Fixes #42`), a checklist of commands executed, and screenshots or GIFs for UI-facing changes. Wait for CI greenlights and at least one maintainer review before merging.

## Security & Configuration Tips
Never commit live credentials; use `.env` or `config/local.toml` locally and provide sanitized `.example` files. When sharing logs, scrub vessel identifiers and tokens. Gate unfinished widgets with Cargo features (e.g., `--features experimental`) so they can be left out of production builds.

## Data Providers & State Synchronization
- `MeshCoreChatProvider` in `src/data.py` consumes `MeshCoreService` snapshots; fall back to `FakeDataProvider` only when the SDK is unavailable.
- All providers must call `_on_update(DataUpdate(...))` to keep the UI in sync—never mutate `ListView` contents directly from outside a `Screen`.
- Treat `MeshCoreChannel`/`MeshCoreNode` as value objects; prefer adding richer methods (e.g., formatting unread counts) instead of scattering string helpers.
- When you add persistence or meshcore SDK calls, isolate all transport/auth code under `src/services/` and expose pure data objects to the UI.

## UI Interaction Patterns
- Every split-pane chat screen should derive from `BaseChatScreen` (see `ChannelChatScreen` and `UserChatScreen`) so the loader, list selection, and footer behavior remain consistent.
- Use dialogs (`PromptDialog`) for destructive actions and keep callbacks synchronous; long-running work should be moved into background tasks using Textual `work` helpers.
- Keep key bindings predictable: `1` = channels, `2` = chats, `s` = settings, `a` = add, `d` = delete. Introduce new bindings only if they are discoverable via the footer/tooltips.
