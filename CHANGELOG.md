# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3] - 2026-05-23

First public release.

### Added
- **Auto-discovery bridge**: one process polls a remote EVCC instance and
  publishes every loadpoint as its own `com.victronenergy.evcharger.http_id<NN>`
  D-Bus service, with stable DeviceInstances persisted in `state.json`.
- **Guided installer** (`setup.sh`): interactive one-command setup that installs
  the bridge and the VRM-tunnel service, auto-detects and migrates a legacy
  single-loadpoint install, prompts for the EVCC host and the optional VRM
  tunnel, and (re)starts the services so configuration changes are applied.
- **`setup_config.py`**: case-preserving `config.ini` writers plus a small CLI
  (`set-host`, `set-tunnel`), reusing the bridge's own validation.
- **Optional VRM "Control panel" tunnel** (`dbus-vrm-tunnel/`): advertises each
  loadpoint as `Modbus TCP <AdvertiseIp>` and runs a `/login.htm` rewrite proxy
  + iptables DNAT so the EVCC web UI is reachable through the VRM portal button
  without a VPN. Default off.
- **Auto-migrator** (`migrate_from_lp.py`): imports legacy `dbus-evcc-*` installs
  into `state.json` without losing their DeviceInstances.
- Resilient polling (clean warning instead of crash when EVCC is unreachable),
  per-loadpoint error isolation, ItemsChanged batching, and named-logger →
  multilog logging.
- Test suite (~177 tests) and CI (pytest matrix + shellcheck).

[2.3]: https://github.com/okuegow/dbus-evcc-multi/releases/tag/v2.3
