# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5] - 2026-09-26

### Fixed
- Restarting or stopping the service (`svc -t`, `svc -d`, `restart.sh`,
  `setup.sh`) no longer leaves the old bridge running as an orphan. Since 2.4
  `service/run` started python without `exec` to implement the 30 s backoff, so
  daemontools signalled only the shell. The orphan kept its D-Bus names and
  kept serving the old configuration, while every new start failed with a
  DeviceInstance collision and retried every 30 s. A changed EVCC host in
  `config.ini` was therefore silently not applied. `service/run` execs python
  again; the 30 s backoff after a failed start (non-zero exit, exception or
  failed import) now lives in `dbus-evcc.py`.

## [2.4] - 2026-08-24

### Added
- `AcPosition` setting in `config.ini` (`0` = AC-Out, default; `1` = AC-In).
  It is published as `/Position` on every charger service and decides which
  loads branch the Venus GUI draws the chargers on. Verified on Venus OS
  v3.80~39; the system consumption figures are unaffected because
  `dbus-systemcalc-py` reads `/Position` for PV inverters only.
- `/Session/Energy` (kWh) and `/Session/Time` (seconds) on every charger
  service. The gui-v2 overview reads the current session from those paths;
  without them the new GUI showed no session energy and no charging time.
  The legacy `/Ac/Energy/Forward` and `/ChargingTime` paths are unchanged.

- Richer `/Status` and `/Mode`: the Venus GUI now shows *Waiting for sun*,
  *Waiting for start*, *Charged* and *Switching to single/three phase* instead
  of only connected/charging, and a running EVCC plan reports as *Scheduled*.
  Derived from EVCC fields the bridge already polls.
- `/Model`, `/Serial` and `/FirmwareVersion` (the running EVCC version) so the
  chargers are identifiable in the VRM device list.
- **Energy counter continuity** (`energy.py`): `/Ac/Energy/Forward` is now
  published as `source + offset` instead of EVCC's raw counter. This keeps the
  counter monotonic and continuous when the source field changes, when EVCC is
  reinstalled and its meters restart, and when this bridge takes over a
  DeviceInstance from a legacy install. Without it VRM either books the step as
  energy charged in one hour, or - above its 250 kWh plausibility limit -
  discards the reading and stops counting that charger until its logger
  restarts.
- `seed_state.py --adopt-counters` reads `/Ac/Energy/Forward` from the legacy
  services still running under the seeded DeviceInstances, so the new bridge
  continues those counters.
- `dbus-evcc.py --plan`: dry run showing per loadpoint which DeviceInstance it
  would use and which counter value it would publish first. Touches neither the
  D-Bus nor `state.json`.
- `migrate_from_lp.py` prints a ready-to-paste `seed_state.py` line for every
  install it refuses to map automatically.

### Changed
- `state.json` is written in a v2 format that carries the energy state next to
  the DeviceInstance. v1 files are read as before and upgraded on the next
  write, so no migration step is needed.

### Fixed
- **The bridge now survives a reboot.** `/service` is a tmpfs, so rc.local
  re-runs `install.sh` on every boot - and that looked like a first install,
  which dropped the `down` marker again and left the bridge off until someone
  noticed the chargers missing in VRM. A persistent `.installed` marker now
  tells the two cases apart.
- `install.sh` makes `/data/rc.local` executable on every run. Venus only runs
  it when it is (`/etc/init.d/custom-rc-late.sh`: `if [ -x /data/rc.local ]`),
  and a non-executable file silently disabled the autostart.
- `service/run` no longer restarts the bridge in a tight loop after a
  configuration error. daemontools respawns immediately, and that spin is
  enough to trip the Venus load watchdog (`/etc/watchdog.conf`: `max-load-5 =
  10`, `max-load-15 = 6`), which reboots the GX. It now waits 30 s and logs the
  exit code.
- `chargeDuration` is now read in the unit EVCC actually sends. Up to some
  release it was Go nanoseconds; EVCC 0.307.1 sends plain seconds, which the
  old `// 1_000_000_000` turned into 0, so the charging time stayed empty.
  Values at or above 1e9 are read as nanoseconds, everything below as seconds.

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

[2.4]: https://github.com/okuegow/dbus-evcc-multi/releases/tag/v2.4
[2.3]: https://github.com/okuegow/dbus-evcc-multi/releases/tag/v2.3
