# To Do List

- [ ] Decide on a caching solution for storing messages, contacts, and other state for persistence between app sessions (mysql?)
- [x] Store app configuration in the config.yaml file (see `services/config_service.py` and the Settings screen)
- [x] Connect to the MeshCore SDK and start interfacing with the MeshCore radio (see `MeshCoreService` and `MeshCoreChatProvider`)
  - [ ] Build settings page to enable connecting to radio via Bluetooth or USB, including scanning for devices using Bleak
  - [ ] Surface detected serial devices (pyserial) alongside BLE scan results so users can pick the companion without editing YAML
  - [x] Settings UI to define the companion node's settings
  - [ ] Download known users / nodes from the radio
  - [x] Trigger an initial sync of pending contact/channel messages (MeshCore `get_msg` loop) so history shows up after connecting
  - [ ] Connect with channels and chats, download messages from the radio (and cache them) and display them in the UI
  - [ ] Send messages to channels and users (verify ACK handling and retries for mesh hops)
  - [ ] Persist contact/channel/message metadata locally for offline mode and faster reloads
- [ ] Public channel readiness
  - [x] Indicate MeshCore connection status in the footer so it’s obvious when the radio is reachable
  - [x] Auto-select and focus the first available channel (default `public`) when live data arrives so messages can be sent without extra clicks
  - [x] Show sender metadata for channel posts (map contact prefix to display name) instead of attributing everything to the current user
  - [x] Provide an error toast when sending to a channel fails (e.g., channel not provisioned or radio offline)
- [ ] Refactor classes into files in a more idomatic way
- [ ] Package as a python module and enable easy installation via pipx
