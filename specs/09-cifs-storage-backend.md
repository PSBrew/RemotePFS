# Spec 09: Protocol-Backed Sources

## Objective

Use one extensible source contract for remote storage. V1 implements NFS and SMB 3.1.1 through Linux's CIFS client. Future HTTP(S), FTP, and torrent/P2P providers can add provider managers without changing virtual layout entries.

## Generic YAML contract

```yaml
sources:
  - name: nas1
    protocol: cifs
    endpoint: //NAS_IP/games
    mount_point: /mnt/nas1
    read_only: true
    options: ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576
    credentials_file: /etc/remotepfs/nas1.credentials
```

Fields:

- `name`: unique source identifier.
- `protocol`: provider identifier, currently `nfs` or `cifs`.
- `endpoint`: provider endpoint. NFS uses `server:/export`; CIFS uses `//server/share`.
- `mount_point`: absolute local path used by V1 mounted providers.
- `read_only`: defaults to `true`; `false` is rejected.
- `options`: provider options. NFS and CIFS options must include standalone `ro` and reject `rw`. Secure protocol defaults apply when omitted.
- `credentials_file`: optional absolute path for providers requiring credentials. CIFS requires it; file must be owned by root, mode `0600`, and contain no repository data.

Unknown protocol identifiers are accepted by config parsing for forward compatibility, but activation fails until matching provider manager support exists.

## V1 provider behavior

### NFS

- Mount command: `mount -t nfs4 -o <options> <endpoint> <mount_point>`.
- Default options: `ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec`.
- Endpoint must use `server:/export` form.

### SMB 3.1.1 through Linux CIFS client

- `protocol: cifs` selects Linux `mount.cifs`; `vers=3.1.1` selects SMB 3.1.1 on wire.
- Mount command: `mount -t cifs -o <options>,credentials=<credentials_file> <endpoint> <mount_point>`.
- Default options: `ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576`.
- Endpoint must use `//server/share` form.
- Never use `seal` by default on trusted LAN; encryption adds overhead.
- Do not use `cache=loose` or `soft`; stale reads and silent I/O failures are unsafe.
- `multichannel` remains opt-in because benefit depends on NAS, kernel, and network topology.

## Mount behavior

- Mount manager creates mount directories before mounting.
- Unmount uses `umount mount_point` for both V1 protocols.
- Provider-specific command construction stays isolated from config validation and virtual filesystem logic.
- Unsupported protocols fail activation with a clear error; no silent fallback.

## Security

- All V1 source mounts remain read-only.
- Reject unsupported writable options, including standalone `rw` even when `ro` is also present.
- Reject relative or traversal-containing credential paths.
- Never log credential contents or include credentials in YAML examples.
- Documentation instructs users to create credential files with mode `0600` outside repository.

## Future providers

HTTP(S), FTP, and torrent/P2P providers should implement a provider interface that materializes or exposes a local read-only path under `mount_point`. Their endpoint and authentication fields remain provider-specific while `entries[].source` stays unchanged.

## Testing

- Validate generic protocol, endpoint, read-only, options, and credential rules.
- Verify NFS and CIFS command construction with injected subprocess runners.
- Verify missing, relative, and insecure CIFS credentials fail before mount.
- Verify unsupported providers fail activation without invoking subprocesses.
- Run full pytest and Ruff checks.
