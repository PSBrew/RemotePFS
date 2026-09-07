# Configuration

RemotePFS reads `/etc/remotepfs/remotepfs.yaml` as YAML.

```yaml
global:
  image_size_gib: 512
  cluster_size_kib: 64
  label: REMOTEPFS
  oem_name: REMOTEPF

sources:
  - name: nas1
    protocol: cifs
    endpoint: //NAS_IP/games
    mount_point: /mnt/nas1
    read_only: true
    options: ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576
    credentials_file: /etc/remotepfs/nas1.credentials

entries:
  - virtual_path: example-games
    source: /mnt/nas1/example-games
    type: directory
```

## Rules

- `cluster_size_kib` must be `64`.
- `image_size_gib` must be between `1` and `262144`.
- `label` is uppercase ASCII, at most 11 characters.
- `oem_name` is uppercase ASCII, exactly 8 characters. `REMOTEPF` is the default.
- `sources` and `entries` must each contain at least one item.
- Source names and virtual paths must be unique.
- `protocol` identifies source provider. V1 supports `nfs` and `cifs`; schema is generic for future providers.
- `endpoint` is provider-specific. NFS uses `server:/export`; CIFS uses `//server/share`.
- `mount_point` is absolute and stays under `/mnt` on SBC.
- `read_only` defaults to `true`; `false` is rejected.
- NFS and CIFS `options` must contain standalone `ro` and must not contain `rw`.
- CIFS omitted `options` use SMB 3.1.1 with strict cache and read tuning.
- `credentials_file` is absolute, outside repository, root-owned, and must be mode `0600`.
- `virtual_path` is a flat root name and cannot contain `/`.
- Entry sources must be inside a configured `mount_point`.
- `type` is `file` or `directory`.
- Config bodies are limited to 1 MiB and 10,000 entries.
- Paths containing `..`, `./`, or `~` are rejected.

`protocol = "cifs"` selects Linux's CIFS client and `mount.cifs`; `vers=3.1.1` selects SMB 3.1.1. Create credential files on SBC, never in repository:

```bash
sudo install -m 0600 /dev/null /etc/remotepfs/nas1.credentials
sudoedit /etc/remotepfs/nas1.credentials
```

Use `protocol: nfs` with an NFS endpoint and NFS options when matching kernel client support exists. Files remain on source storage and are read on demand. RemotePFS never creates local `.exfat` images.

## API workflow

Compile without interrupting active generation:

```bash
curl -X POST --data-binary @/etc/remotepfs/remotepfs.yaml \
  -H 'Content-Type: application/yaml' http://127.0.0.1:8080/api/config/compile
```

Activate returned generation only after reviewing validation output:

```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"generation": 2}' http://127.0.0.1:8080/api/config/activate
```

`PUT /api/config` and `POST /api/config/reload` combine these operations. JSON remains accepted by the API for automation compatibility.
