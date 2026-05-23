import sys
from pathlib import Path

# Layout: dbus-evcc-multi/dbus-vrm-tunnel/tests/conftest.py
#   parent.parent        = dbus-vrm-tunnel/  (holds vrm_tunnel.py)
#   parent.parent.parent = dbus-evcc-multi/  (holds cli.py, the single
#                                             [VRM_TUNNEL] parser)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
