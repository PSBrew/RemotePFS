# 05 — Security Model

> **RemotePFS context:** SBC runs as a dedicated appliance bridging PS5 and NAS.
> Security model must defend against: config tampering, unauthorized access to
> NBD block device, HTTP API abuse, NFS credential exposure, and denial of
> service. The entire data path is read-only by design — multiple enforcement
> layers ensure the PS5 cannot write to NAS game files.

---

## 1. Trust Boundaries

```
┌──────────────────────────────────────────────────────────────────────┐
│  PS5 (untrusted)                                                     │
│  • Issues SCSI READ commands. WRITE commands rejected by gadget.      │
│  • Cannot authenticate — USB MSC has no authentication mechanism.     │
│  • Must not be able to corrupt NAS files under any circumstance.      │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ USB 3.0 — SCSI READ only (ro=1)
                            ▼
╔══════════════════════════════════════════════════════════════════════╗
║  TRUST BOUNDARY 1 — USB Gadget (g_mass_storage ro=1)                ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  /dev/nbd0 (kernel NBD client)                                       │
│  • Connected read-only (-r flag) to local nbdkit.                    │
│  • Block device accessible only to root and remotepfs group.          │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ AF_UNIX socket (0600)
                            ▼
╔══════════════════════════════════════════════════════════════════════╗
║  TRUST BOUNDARY 2 — NBD Unix Socket                                 ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  nbdkit (dedicated user, restricted capabilities)                     │
│  • --readonly flag enforces no-write at protocol level.              │
│  • --filter=blocksize --filter=cache                                  │
│  • Python plugin: pread(), extents(). No pwrite(), no trim().         │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ os.pread() on NFS file descriptors
                            ▼
╔══════════════════════════════════════════════════════════════════════╗
║  TRUST BOUNDARY 3 — Network (NFS over TCP 2049)                     ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  NAS (trusted, user-managed)                                         │
│  • Read-only NFS export.                                              │
│  • NFS export restricted to SBC IP.                                   │
└──────────────────────────────────────────────────────────────────────┘
```

Three hard boundaries:

1. **USB gadget `ro=1`**: PS5 cannot issue writes. Any SCSI WRITE command is
   stalled by `g_mass_storage` before it reaches the block layer.

2. **NBD socket 0600 + plugin read-only**: Only root and `remotepfs` group
   members can connect to nbdkit. The Python plugin implements only `pread()`
   and `extents()` — no write path exists in code.

3. **NFS `ro` mount**: The NAS export is mounted read-only at the kernel
   level. Even if the nbdkit process is compromised, writes to NFS files are
   rejected by the kernel NFS client.

---

## 2. Read-Only Enforcement Layers

Three independent layers guarantee the data path is read-only. Each layer
would stop writes even if the other two were bypassed.

| Layer                     | Mechanism                              | Scope                       |
|---------------------------|----------------------------------------|-----------------------------|
| 1. USB gadget             | `ro=1` in LUN config                   | Blocks SCSI WRITE commands  |
| 2. NBD                    | `nbdkit --readonly` + `nbd-client -r`  | Blocks NBD write requests    |
| 3. NFS mount              | `mount -o ro`                          | Blocks file writes           |

### 2.1 Layer 1: USB Gadget `ro=1`

Set via ConfigFS:

```bash
echo 1 > /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/lun.0/ro
```

The `g_mass_storage` driver rejects SCSI WRITE(10/12/16) commands at the
gadget layer. No write reaches `/dev/nbd0`. Stalled commands return SCSI
sense `DATA PROTECT` (0x07) to the PS5.

Additional LUN hardening:

```bash
echo 1 > .../lun.0/nofua        # Disable Force Unit Access
echo 1 > .../mass_storage.0/stall  # Stall on unsupported commands
```

### 2.2 Layer 2: NBD Read-Only

**nbdkit server side:**

```bash
nbdkit --readonly \
       --unix /run/remotepfs/nbd.sock \
       --filter=blocksize \
       --filter=cache \
       python /usr/lib/remotepfs/plugin.py
```

The `--readonly` flag causes nbdkit to reject `NBD_CMD_WRITE`, `NBD_CMD_TRIM`,
and `NBD_CMD_WRITE_ZEROES` with `NBD_EPERM` before they reach the plugin.

**NBD client side (defense in depth):**

```bash
nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
```

The `-r` flag sets the kernel NBD device read-only. The block layer rejects
writes to `/dev/nbd0` regardless of the nbdkit configuration.

**Plugin code guarantee:** The Python plugin implements `pread()` and
`extents()` only. There is no `pwrite()`, `trim()`, `zero()`, `flush()`, or
`cache()` method. The plugin is incapable of writing even if nbdkit forwarded
write requests.

### 2.3 Layer 3: NFS Read-Only Mount

```bash
mount -t nfs4 -o ro,nosuid,nodev,noexec,hard,nconnect=2,rsize=1048576,noatime \
    nas.example.com:/exports/games /mnt/nas/games
```

Mount options:

| Option     | Purpose                                                 |
|------------|---------------------------------------------------------|
| `ro`       | Read-only. Kernel NFS client rejects writes.            |
| `nosuid`   | Ignore setuid/setgid bits on NFS files.                 |
| `nodev`    | Do not interpret device nodes on NFS.                   |
| `noexec`   | Do not permit execution of binaries from NFS mount.     |
| `noatime`  | Skip access-time updates (reduces metadata writes).     |

The NAS should also export the share read-only, providing a fourth enforcement
point at the server side.

---

## 3. NBD Socket Permissions

The nbdkit Unix socket is the inter-process boundary between the kernel NBD
client and the nbdkit userspace server. Unauthorized access to this socket
would allow reading arbitrary sectors from the virtual exFAT.

```bash
# Socket creation by nbdkit
nbdkit -U /run/remotepfs/nbd.sock \
    --unix-mode=0600 \
    --user remotepfs-nbd \
    --group remotepfs \
    --exit-with-parent \
    --readonly \
    --pidfile /run/remotepfs/nbdkit.pid \
    --filter=blocksize --filter=cache \
    python remotepfs_nbd.py

# Socket permissions enforced by nbdkit --unix-mode=0600
# Directory managed by systemd RuntimeDirectory (see section 4)
```

| Property   | Value                    | Rationale                                      |
|------------|--------------------------|------------------------------------------------|
| Owner      | `root`                   | nbdkit runs as root (drops capabilities).       |
| Group      | `remotepfs`              | Only members of the remotepfs group can access. |
| Mode       | `0600`                   | No world/other access.                          |
| Directory  | `/run/remotepfs/`        | tmpfs, cleared on reboot, 0750 root:remotepfs.  |

The `remotepfs` group is created at package installation. Only the `remotepfs`
service user (for CLI/API access) and the nbdkit process user are members.

The parent directory is also restricted:

```bash
mkdir -p /run/remotepfs
chown root:remotepfs /run/remotepfs
chmod 0750 /run/remotepfs

---

## 4. nbdkit Process Isolation

nbdkit runs as a dedicated system user with minimal privileges.

### 4.1 Service User

```ini
# /etc/systemd/system/remotepfs-nbdkit.service
[Service]
User=remotepfs-nbd
Group=remotepfs
```

The `remotepfs-nbd` user is created at package installation:
- No login shell (`/usr/sbin/nologin`).
- No home directory.
- No password.
- Member of `remotepfs` group (for socket access).

### 4.2 Capability Restrictions

Systemd `CapabilityBoundingSet` limits the nbdkit process to the minimum
capabilities needed:

```ini
[Service]
CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_BIND_SERVICE
AmbientCapabilities=
```

| Capability            | Needed? | Reason                                           |
|-----------------------|---------|--------------------------------------------------|
| `CAP_SYS_ADMIN`       | Yes     | nbdkit may need it for NBD device operations.     |
| `CAP_NET_BIND_SERVICE`| No      | Not needed (Unix socket, not TCP). Removed via drop-in if confirmed. |
| All others            | No      | Dropped. No raw socket, no module load, no ptrace.|

Additional hardening:

```ini
[Service]
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
RuntimeDirectory=remotepfs
RuntimeDirectoryMode=0700
ReadOnlyPaths=/etc/remotepfs /usr/lib/remotepfs
ReadWritePaths=/run/remotepfs
RestrictAddressFamilies=AF_UNIX
SystemCallFilter=@system-service
```

| Directive                 | Effect                                              |
|---------------------------|-----------------------------------------------------|
| `NoNewPrivileges=yes`     | Process cannot gain new privileges via setuid etc.  |
| `PrivateTmp=yes`          | Private `/tmp` and `/var/tmp` namespace.            |
| `ProtectSystem=strict`    | `/usr`, `/boot`, `/etc` read-only. `/dev`, `/proc`, `/sys` filtered. |
| `ProtectHome=yes`         | `/home` appears empty.                              |
| `ReadOnlyPaths=`          | Explicit read-only access to config and plugin dirs.|
| `ReadWritePaths=`         | Only `/run/remotepfs` is writable (socket creation).|
| `RuntimeDirectory=`       | Creates `/run/remotepfs` as private tmpfs at service start.|
| `RuntimeDirectoryMode=`   | `0700` — only root can access the socket directory. |
| `RestrictAddressFamilies=`| Only `AF_UNIX` allowed. No TCP/UDP socket creation. |
| `SystemCallFilter=`       | Whitelist of syscalls. Blocks dangerous calls.      |

---

## 5. HTTP API Security

The HTTP API server listens on `127.0.0.1:8080` only. It exposes config
management and status endpoints.

### 5.1 Endpoints

| Method | Path                     | Purpose                                  | Risk Level  |
|--------|--------------------------|------------------------------------------|-------------|
| POST   | `/api/config/compile`    | Validate + compile TOML config           | Medium      |
| POST   | `/api/config/activate`   | Atomically swap active generation         | High        |
| GET    | `/api/status`            | Current state (bound, game count, etc.)   | Low         |
| POST   | `/api/eject`             | Gracefully unbind UDC                    | High        |

### 5.2 Localhost-Only Binding

```python
# api_server.py
app.run(host="127.0.0.1", port=8080)
```

No external network interface. The API is unreachable from the LAN unless the
attacker has shell access to the SBC.

### 5.3 No Authentication

The API has no authentication mechanism. Rationale:

- **Localhost-only binding is the authentication.** Only processes running on
  the SBC can reach `127.0.0.1:8080`. SSH access to the SBC is the
  prerequisite for API access.
- **Config is not secret.** The TOML config contains no credentials, keys, or
  secrets (see section 6). Authenticating API access adds complexity without
  protecting anything sensitive.
- **Simplicity.** No token management, no TLS certificates, no credential
  rotation. The SBC is a single-user appliance.

Future versions MAY add authentication if remote API access is required
(e.g., a companion mobile app). For V1, localhost binding is sufficient.

### 5.4 API-Specific Threats and Mitigations

#### POST /api/config/compile

**Threat: Malicious TOML input.** Attacker with shell access submits a crafted
config to crash the compiler, exhaust memory, or trigger path traversal.

**Mitigations:**
- TOML parser in strict mode: rejects duplicate keys, invalid UTF-8, and
  type mismatches.
- Path traversal defense: reject `virtual_path` and `source_dir` values
  containing `../`, `./`, or absolute paths starting with `/`.
- Size limits: reject config bodies larger than 1 MiB. Reject more than
  10,000 game entries. Reject `total_size` values exceeding 16 TiB.
- Memory limit: compile process bounded by systemd `MemoryMax=2G`.
- Validation report enumerates all errors; no partial compilation.

#### POST /api/config/activate

**Threat: Unauthorized device rebind.** Attacker triggers eject/activate
cycle to disrupt PS5 gameplay.

**Mitigations:**
- Activate requires a valid generation ID returned by a prior compile.
- Only one activate can be in-flight at a time (mutex on UDC state).
- PS5 sees a transient device removal — games may crash if they were
  reading files. This is an availability impact, not a data integrity one.

#### GET /api/status

**Threat: Information disclosure.** Status response reveals game count, NAS
mount status, and active generation ID.

**Mitigations:**
- Localhost-only binding limits exposure.
- Status response intentionally includes no file paths or NAS IPs.
- Response format: `{"bound": true, "game_count": 12, "generation": "<uuid>"}`.

#### POST /api/eject

**Threat: Unauthorized eject.** Attacker forces PS5 to lose the virtual drive.

**Mitigations:**
- Only ejects if currently bound. No-op if already unbound.
- PS5 recovery: unplug/replug USB cable restores the device (gadget rebinds
  on cable reconnection if the systemd service is active).

### 5.5 HTTP Server Hardening

```python
# Request limits
max_request_size = 1 * 1024 * 1024  # 1 MiB

# Timeouts
request_timeout = 30   # seconds
keepalive_timeout = 5  # seconds

# Headers
response_headers = {
    "Server": "",                     # Don't advertise server version
    "X-Content-Type-Options": "nosniff",
}
```

---

## 6. Config Security

### 6.1 No Secret Material

The TOML config contains no credentials, keys, tokens, or secrets:

```toml
# What the config contains (safe):
[general]
label = "PS5 Games"

[[nfs_servers]]
name = "synology"
host = "192.168.1.100"
export = "/volume1/games"

[[games]]
virtual_path = "/PS5/Games/Elden Ring"
source_dir = "/ps5-games/elden-ring"
```

NFS authentication is handled at mount time by the Linux kernel NFS client.
Kerberos tickets, if used, are managed by `gssproxy` or the system keytab —
not stored in the RemoPFS config. The config references NFS server hostnames
and export paths, but the credentials live in the kernel's NFS mount context.

### 6.2 Input Validation

The config compiler enforces strict validation on all fields:

| Field              | Validation                                                    |
|--------------------|---------------------------------------------------------------|
| `virtual_path`     | Must start with `/PS5/`. No `..`, no `./`, no symlinks.       |
| `source_dir`       | No `..`, no absolute paths, no shell metacharacters.          |
| `host`             | Must be a valid IPv4 address or resolvable hostname.           |
| `total_size`       | Positive integer. Minimum 1 GiB, maximum 16 TiB.               |
| `cluster_size`     | Power of 2, 512 KiB to 32 MiB.                                |
| `label`            | 1–11 characters (exFAT volume label limit).                   |
| `serial`           | Alphanumeric, 1–32 characters.                                |
| Game entry count   | Maximum 10,000 (reject with error beyond this).               |
| Config body size   | Maximum 1 MiB (reject before parsing).                        |

### 6.3 Path Traversal Defense

All path fields are validated against traversal sequences:

```python
FORBIDDEN_PATTERNS = ["..", "./", "~"]

def validate_path(path: str, field_name: str) -> None:
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in path:
            raise ConfigValidationError(
                f"{field_name}: path traversal pattern '{pattern}' rejected"
            )
    if path.startswith("/") and field_name == "source_dir":
        raise ConfigValidationError(
            f"{field_name}: absolute paths not allowed in source_dir"
        )
```

`virtual_path` MUST start with `/PS5/` and contain no traversal patterns.
`source_dir` is relative to the NFS mount root and MUST NOT start with `/`

### 6.4 File Permissions

```bash
chown root:remotepfs /etc/remotepfs/config.toml
chmod 0640 /etc/remotepfs/config.toml
```

Readable by root and `remotepfs` group. Not world-readable (though no secrets
are stored, it reveals NAS topology).

---

## 7. Threat Model

### 7.1 Threat: Input Validation (Config Parsing)

**Scenario:** Attacker with shell access submits a malicious TOML config.

**Attack vectors:**
- TOML parser bomb: deeply nested tables, excessively long keys.
- Path traversal in `virtual_path` or `source_dir`.
- Integer overflow in `total_size` or `cluster_size`.
- Excessively large config (DoS via memory exhaustion).

**Mitigations:**
- Config body size limit: 1 MiB (reject before parsing).
- TOML parser in strict mode with depth limit (max 32 nesting levels).
- Path traversal rejection (see section 6.3).
- Size validation bounds (see section 6.2).
- Compile runs in a subprocess with `MemoryMax=2G` via systemd.
- Maximum 10,000 game entries.

### 7.2 Threat: Denial of Service (Config Compile)

**Scenario:** Attacker submits a valid but pathologically large config
designed to exhaust memory or CPU during compilation.

**Attack vectors:**
- 10,000 game entries × long path names → memory exhaustion.
- Repeated POST /api/config/compile → CPU exhaustion.

**Mitigations:**
- 10,000 game entry hard limit.
- Compile subprocess `MemoryMax=2G` — OOM kill if exceeded.
- Compile timeout: 30 seconds. If compile does not complete, process is killed.
- Only one compile can run at a time (mutex).
- Rate limit: maximum 10 compiles per minute (token bucket).

### 7.3 Threat: Information Disclosure (HTTP API)

**Scenario:** Attacker probes HTTP API for information about connected NAS,
game library, or system state.

**Attack vectors:**
- GET /api/status reveals game count, NAS status.
- Error messages leak file paths.
- Stack traces expose internal structure.

**Mitigations:**
- Localhost-only binding (see section 5.2).
- Status response intentionally minimal: no file paths, no NAS IPs.
- Error responses use generic messages in production: `{"error": "config_validation_failed", "details": [...]}`.
- Stack traces only in debug mode (environment variable `REMOTEPFS_DEBUG=1`).
- Production responses include no internal paths.

### 7.4 Threat: NBD Socket Hijacking

**Scenario:** Unauthorized process connects to the nbdkit Unix socket and
reads arbitrary sectors from the virtual exFAT.

**Attack vectors:**
- Socket permissions too permissive (world-readable).
- Process in `remotepfs` group is compromised.

**Mitigations:**
- Socket mode `0600` + owned `root:remotepfs` (see section 3).
- `remotepfs` group membership is tightly controlled — only service users.
- nbdkit `RestrictAddressFamilies=AF_UNIX` prevents outbound connections.
- If a process in `remotepfs` group is compromised, the attacker can read
  the virtual exFAT but cannot write to NAS files (NFS `ro` mount).

### 7.5 Threat: nbdkit Process Compromise

**Scenario:** Attacker exploits a vulnerability in nbdkit or the Python
plugin to execute arbitrary code.

**Attack vectors:**
- Buffer overflow in nbdkit C code.
- Python code injection via config values.
- nbdkit filter vulnerability.

**Mitigations:**
- nbdkit runs as dedicated `remotepfs-nbd` user (see section 4.1).
- Systemd capability bounding set restricts available syscalls.
- `NoNewPrivileges=yes` prevents privilege escalation.
- `ProtectSystem=strict` makes filesystem mostly read-only.
- `RestrictAddressFamilies=AF_UNIX` prevents outbound network connections.
- NFS mount `ro` prevents writes even under full process compromise.
- `SystemCallFilter=@system-service` whitelists safe syscalls.

**Worst case:** Attacker achieves code execution as `remotepfs-nbd`. They can:
- Read any NAS file (NFS `ro` mount permits reads).
- Crash the nbdkit process (DoS for PS5).
- Read exFAT metadata from memory (equivalent to reading the PS5's view of
  the filesystem).

They cannot:
- Write to NAS files (NFS `ro`, nbdkit `--readonly`).
- Access other SBC services (no privileges, no network).
- Persist across service restart (tmpfs `/run`, read-only `/usr`).

### 7.6 Threat: PS5 Malicious Input

**Scenario:** PS5 (or a device impersonating one) sends crafted USB packets
or SCSI commands.

**Attack vectors:**
- Malformed SCSI commands.
- USB packet flooding.
- Attempted writes despite `ro=1`.

**Mitigations:**
- `g_mass_storage ro=1` blocks writes at kernel gadget layer.
- `stall=1` drops unsupported commands.
- NBD `--readonly` is second layer.
- Linux USB stack handles malformed USB packets at kernel level.
- Buffer overflows in USB gadget driver are kernel bugs, not RemotePFS scope.

### 7.7 Threat: Physical Access

**Scenario:** Attacker has physical access to SBC (console, USB ports, SD card).

**Attack vectors:**
- Boot from external media.
- Read SD card directly.
- Connect to serial console.

**Mitigations:**
- Physical access is out of scope for RemotePFS software security model.
- Standard physical hardening (locked case, secure boot, encrypted storage)
  is the user's responsibility.
- NAS-side NFS export restriction (by IP) provides partial mitigation:
  even if SBC is compromised, attacker needs valid source IP to access NAS.

---

## 8. Security Checklist (Deployment)

| Check | Command / Verification                                       |
|-------|--------------------------------------------------------------|
| USB gadget ro=1 | `cat /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/lun.0/ro` → `1` |
| NBD client read-only | `cat /sys/block/nbd0/ro` → `1`                               |
| nbdkit --readonly | `ps aux | grep nbdkit | grep -- --readonly`                  |
| NFS mount ro | `mount | grep /mnt/nas | grep -o 'ro'` (verify `ro` present) |
| NFS nosuid,nodev,noexec | `mount | grep /mnt/nas` (verify all three flags present) |
| NBD socket permissions | `stat -c '%a %U:%G' /run/remotepfs/nbd.sock` → `600 root:remotepfs` |
| Socket directory permissions | `stat -c '%a %U:%G' /run/remotepfs` → `750 root:remotepfs` |
| API localhost-only | `ss -tlnp | grep 8080` → `127.0.0.1:8080`                    |
| Config file permissions | `stat -c '%a %U:%G' /etc/remotepfs/config.toml` → `640 root:remotepfs` |
| nbdkit service user | `systemctl show remotepfs-nbdkit | grep ^User=` → `remotepfs-nbd` |
| nbdkit capabilities | `systemctl show remotepfs-nbdkit | grep CapabilityBoundingSet` |
| No secrets in config | `grep -i '\(password\|secret\|key\|token\|credential\)' /etc/remotepfs/config.toml` → no output |

---

## 9. What Is NOT Protected

Explicitly out of scope for V1:

| Scenario                    | Why Out of Scope                                  |
|-----------------------------|---------------------------------------------------|
| PS5 reads game files        | This is the intended function.                    |
| SBC compromise via SSH      | SSH security is the user's responsibility.        |
| NAS compromise              | NAS security is the user's responsibility.        |
| Network eavesdropping       | NFS over trusted LAN. Kerberos optional for V2.    |
| Physical SBC theft          | Physical security is the user's responsibility.   |
| Side-channel attacks on USB | Impractical against PS5. Not a realistic threat.  |

---

*Conforms to mkpfs conventions: Python 3.11+, Google docstrings, Ruff
line-length=119, Conventional Commits.*
