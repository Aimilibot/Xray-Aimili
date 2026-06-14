import threading
import subprocess

# Global synchronization lock
lock = threading.RLock()

# API session tracking: token -> expiry timestamp
active_sessions: dict[str, float] = {}

# OpenVPN client states
active_openvpn_process: subprocess.Popen | None = None
active_openvpn_node_id = ""
openvpn_enabled = False
is_connecting = False
last_active_ping_time = 0.0
last_active_latency = 0

# Service heartbeats
last_collector_heartbeat = 0.0
last_checker_heartbeat = 0.0

# Xray Core states
active_xray_process: subprocess.Popen | None = None
xray_last_error = ""
xray_last_command: list[str] = []
xray_log_tail: list[str] = []
xray_install_lock = threading.Lock()
xray_install_status = {"status": "idle", "message": "", "progress": 0}

# Public IP cache
cached_public_ip = ""
cached_public_ip_time = 0.0

# Session traffic stats
session_rx_start = 0
session_tx_start = 0

# Additional service heartbeats
last_pinger_heartbeat = 0.0
import time
server_start_time = time.time()



