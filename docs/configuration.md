# Configuration

RemotePFS reads `/etc/remotepfs/remotepfs.conf` as TOML.

```toml
[global]
image_size_gib = 512
cluster_size_kib = 64
label = "REMOTEPFS"
oem_name = "REMOTEPF"

[[sources]]
name = "nas1"
server = "192.168.1.100"
export = "/volume1/games"
mount_point = "/mnt/nas1"
nfs_options = "nfsvers=4.1,nconnect=2,rsize=1048576,hard,noatime,nosuid,nodev,noexec"

[[entries]]
virtual_path = "games"
source = "/mnt/nas1/games"
type = "directory"
```

## Rules

- `cluster_size_kib` must be `64`.
- `image_size_gib` must be between `1` and `262144`.
- `label` is uppercase ASCII, at most 11 characters.
- `oem_name` is uppercase ASCII, exactly 8 characters. `REMOTEPF` is the default; the older `REMOTEPFS` spelling is 9 characters and invalid in the exFAT OEM field.
- `sources` and `entries` must each contain at least one item.
- Source names and virtual paths must be unique.
- `virtual_path` is a flat root name and cannot contain `/`.
- Entry sources must be inside a configured `mount_point`.
- `type` is `file` or `directory`.
- Config bodies are limited to 1 MiB and 10,000 entries.
- Paths containing `..`, `./`, or `~` are rejected.

Directory entries are scanned in lexical order. Files remain on NFS and are read on demand. RemotePFS never creates a local `.exfat` image.

## API workflow

Compile without interrupting the active generation:

```bash
curl -X POST --data-binary @/etc/remotepfs/remotepfs.conf \
  -H 'Content-Type: application/toml' http://127.0.0.1:8080/api/config/compile
```

Activate returned generation only after reviewing validation output:

```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"generation": 2}' http://127.0.0.1:8080/api/config/activate
```

`PUT /api/config` and `POST /api/config/reload` combine these operations.
