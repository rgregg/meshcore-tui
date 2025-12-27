# MeshCore Terminal UI

MeshCore-TUI is a terminal UI interface for working with a MeshCore companion node.

Using the MeshCore-TUI you can easily chat with channels and other individual nodes on the
MeshCore network.

To get started:

1. Create a virtual environment and install dependencies: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
2. Copy `config/config.example.yaml` to `config/config.yaml` (or launch the app once and edit the Settings screen).
3. Run `textual run --dev src/app.py` for live reloads during development.

## Configuration

MeshCore-TUI persists user profile, connection details, and UI preferences to `config/config.yaml`.

- Edit the values directly with your preferred editor or open the Settings screen (`s`) inside the app to change fields and press `Save changes`/`ctrl+s`.
- The schema is defined in `services/config_service.py` with dataclasses for meshcore users, companions, and UI preferences.
- An example file lives at `config/config.example.yaml`; keep this in sync whenever new fields are added so other agents can bootstrap quickly.

Config changes are applied immediately after saving and will be reloaded the next time the TUI starts.

## MeshCore SDK Integration

The chat panes now use `MeshCoreService` (`src/services/meshcore_service.py`) to drive live data from a connected MeshCore companion.

- Set `meshcore.companion.transport` to `bluetooth`, `serial`, or `tcp` and provide `endpoint`/`device` details. Example: `transport = "tcp"` with `endpoint = "192.168.1.55:4403"` or `transport = "serial"` with `device = "/dev/tty.usbserial"`.
- `channel_refresh_seconds` controls how frequently channel metadata is refreshed in the background.
- When a connection is available, `ChannelChatScreen`/`UserChatScreen` automatically subscribe to MeshCore events; otherwise they fall back to the fake provider.
- The service starts automatically when the app mounts. Watch the terminal logs for connection failures and adjust config accordingly.

## Logging

Runtime logs from Textual and the MeshCore SDK mirror to `logs/meshcore-tui.log`. Use this file to capture stack traces or connection errors that might be hidden behind the UI when running with `textual run --dev`. The `logs/` directory is git-ignored; feel free to tail the file or share sanitized snippets in bug reports.
