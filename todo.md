# To Do List

- [ ] Decide on a caching solution for storing messages, contacts, and other state for persistence between app sessions (mysql?)
- [x] Store app configuration in the config.yaml file (see `services/config_service.py` and the Settings screen)
- [x] Connect to the MeshCore SDK and start interfacing with the MeshCore radio (see `MeshCoreService` and `MeshCoreChatProvider`)
  - [ ] Build settings page to enable connecting to radio via Bluetooth or USB, including scanning for devices using Bleak
  - [x] Settings UI to define the companion node's settings
  - [ ] Download known users / nodes from the radio
  - [ ] Connect with channels and chats, download messages from the radio (and cache them) and display them in the UI
  - [ ] Send messages to channels and users
- [ ] Refactor classes into files in a more idomatic way
- [ ] Package as a python module and enable easy installation via pipx
