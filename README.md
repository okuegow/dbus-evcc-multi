# dbus-evcc-multi

**Auto-discovery bridge that exposes every [EVCC](https://evcc.io) loadpoint as
its own EV-charger device on a Victron [Venus OS](https://github.com/victronenergy/venus)
GX device (Cerbo GX, etc.).**

One service on the GX device polls a remote EVCC instance and automatically
publishes **all** of its loadpoints, each as its own
`com.victronenergy.evcharger.http_id<NN>` D-Bus service — so they show up in
the Victron GUI and the VRM portal. No per-loadpoint configuration: add a
loadpoint in EVCC and it appears on the next poll.

> EVCC itself does **not** run on the GX device — only this bridge does. EVCC
> runs on a separate host (e.g. a Raspberry Pi) and is reached over HTTP.

It also ships an optional **VRM "Bedienfeld" tunnel** that makes the EVCC web
UI reachable through the VRM portal button without a VPN, and a guided
**one-command installer** (`setup.sh`).

## Features

- **Auto-discovery** — one poll of EVCC's `/api/state` publishes every
  loadpoint; no manual per-loadpoint setup.
- **Stable DeviceInstances** — title→DI mapping is persisted in `state.json`,
  so VRM history survives restarts and EVCC reordering.
- **Single, efficient process** — one HTTP request per poll for all loadpoints,
  keep-alive, and D-Bus `ItemsChanged` batching to keep CPU low on ARM GX
  hardware.
- **Resilient** — if EVCC is unreachable the bridge logs a warning and keeps
  the last-known services alive instead of crashing; a per-loadpoint
  `try/except` keeps one bad loadpoint from taking down the rest.
- **Migration** — an auto-migrator imports legacy single-loadpoint
  `dbus-evcc-*` installs into `state.json` without losing their DeviceInstances.
- **Optional VRM UI tunnel** (`dbus-vrm-tunnel/`) — reach the EVCC web UI via
  the VRM portal "Bedienfeld" button (default off).
- **Guided installer** (`setup.sh`) — installs everything, migrates, prompts
  for the EVCC host and the optional tunnel, and starts the services.

## Requirements

- A Victron Venus OS GX device (Cerbo GX or similar) with root SSH access and
  daemontools (`svc`/`svstat`), as shipped by Venus OS.
- A reachable EVCC instance on the LAN (its `/api/state` HTTP endpoint).
- Python 3 on the GX device (ships with Venus OS), with `dbus`/`gi` and Victron's
  `velib_python` (present on Venus OS).

## Installation

### Guided (recommended, one command)

```bash
# upload, extract, run the guided setup (interactive -> needs ssh -t)
scp dbus-evcc-multi-v2.3.tar.gz root@<gx-device>:/tmp/
ssh -t root@<gx-device> 'tar xzf /tmp/dbus-evcc-multi-v2.3.tar.gz -C /data && \
  /data/dbus-evcc-multi/setup.sh'
```

`setup.sh` installs the bridge (and the VRM-tunnel service), auto-detects and
migrates a legacy single-loadpoint install (with a confirmation prompt), asks
for the EVCC host and whether to enable the VRM tunnel, then starts the
services. Re-run it any time to reconfigure (for example to enable the tunnel
later) — it restarts the services so the new configuration is applied.

### Manual

```bash
# 1. extract under /data
scp dbus-evcc-multi-v2.3.tar.gz root@<gx-device>:/data/
ssh root@<gx-device> 'cd /data && tar xzf dbus-evcc-multi-v2.3.tar.gz'

# 2. set the EVCC host
ssh root@<gx-device> 'vi /data/dbus-evcc-multi/config.ini'   # ONPREMISE/Host = <ip>:7070

# 3. install and watch the log
ssh root@<gx-device> '/data/dbus-evcc-multi/install.sh'
ssh root@<gx-device> 'tail -F /data/log/dbus-evcc-multi/current | tai64nlocal'
```

On first install the service is created in a "down" state so you can set the
config before it starts. `install.sh` registers itself in `/data/rc.local` so
it survives Venus OS firmware updates.

## Configuration (`config.ini`)

| Section | Key | Meaning |
|---|---|---|
| `DEFAULT` | `PollSeconds` | Poll interval in seconds (default 15) |
| `DEFAULT` | `DeviceInstanceRangeStart` / `End` | DeviceInstance range (default 40–59) |
| `ONPREMISE` | `Host` | `<ip>:<port>` of the EVCC host |
| `VRM_TUNNEL` | `Enabled` | Enable the VRM UI tunnel (default `false`) |
| `VRM_TUNNEL` | `AdvertiseIp` | Non-loopback IP VRM tunnels to (the GX or EVCC host LAN IP) |
| `VRM_TUNNEL` | `EvccTarget` | Where the rewrite proxy forwards (e.g. `127.0.0.1:7070`) |
| `VRM_TUNNEL` | `ProxyPort` | Local rewrite-proxy port (default `8099`) |

## Migrating from single-loadpoint dbus-evcc installs

Older setups run one bridge per loadpoint under `/data/dbus-evcc-<name>/`, each
with a fixed DeviceInstance. The auto-migrator imports those mappings:

```bash
# dry run, then interactive (or --auto), optionally remove the old installs
python3 /data/dbus-evcc-multi/migrate_from_lp.py --dry-run
python3 /data/dbus-evcc-multi/migrate_from_lp.py --auto --uninstall-old
```

It scans `/data/dbus-evcc-*` (excluding `dbus-evcc-multi/`), reads each
`config.ini`, fetches the current EVCC loadpoint titles, proposes a
`title → DeviceInstance` mapping, and writes it to `state.json` on
confirmation. The guided `setup.sh` runs this for you when it detects a legacy
install.

## Behaviour notes

- **Loadpoint added in EVCC** → a new service with a free DeviceInstance from
  the range, recorded in `state.json`, on the next poll.
- **Loadpoint reordered in EVCC** → no effect; the DeviceInstance stays bound
  to the title.
- **Loadpoint renamed in EVCC** → the old title is marked offline
  (`/Connected = 0`) and the new title gets a new DeviceInstance. The old VRM
  history is preserved but no longer updated. Avoid renaming where possible.
- **EVCC unreachable** → logged as a warning; existing services keep their last
  values and are not torn down.
- **Duplicate titles in EVCC** → logged as an error and skipped (they would
  collapse identities). Rename them in EVCC.

## VRM UI tunnel (optional)

When `[VRM_TUNNEL] Enabled = true`, each loadpoint is advertised with
`/Mgmt/Connection = "Modbus TCP <AdvertiseIp>"`, which makes the VRM portal show
a "Bedienfeld" button for it. The bundled `dbus-vrm-tunnel` service then
path-rewrites EVCC's `/login.htm` and DNATs `<AdvertiseIp>:80` to the EVCC web
UI, so the button opens the live EVCC interface through the VRM relay — no VPN
needed. Default is off; the bridge behaves exactly as before.

## Diagnostics

```bash
svstat /service/dbus-evcc-multi                                   # service status
dbus -y | grep evcharger                                          # list services
dbus -y com.victronenergy.evcharger.http_id40 / GetItems          # all values of a loadpoint
tail -F /data/log/dbus-evcc-multi/current | tai64nlocal           # readable log tail
```

Enable debug logging by appending `--debug` to the `python3 .../dbus-evcc.py`
line in `service/run`, then `svc -t /service/dbus-evcc-multi`.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q          # ~177 tests
```

The pure logic (config parsing, sync, migration, the tunnel's request handling)
is unit-tested on a normal machine; the D-Bus/`gi`/`iptables` I/O is exercised
on real Venus OS hardware. CI runs the test suite on every push.

## Credits & lineage

This project grew out of the Venus-OS dbus-evcc lineage:
[JuWorkshop/dbus-evsecharger](https://github.com/JuWorkshop/dbus-evsecharger)
→ [SamuelBrucksch/dbus-evcc](https://github.com/SamuelBrucksch/dbus-evcc)
→ this multi-loadpoint, auto-discovery rewrite.

- Multi-service-in-one-driver pattern: Victron community thread by *mvader*.
- Logging pattern: Victron community documentation thread.

## License

[MIT](LICENSE) © 2026 Oliver Kügow.

Note: the upstream repositories listed above do not carry an explicit license.
This repository licenses the original work in it under MIT and credits the
lineage; it is not affiliated with or endorsed by EVCC or Victron Energy.
