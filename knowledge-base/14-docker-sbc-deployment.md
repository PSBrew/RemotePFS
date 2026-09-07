# Docker Deployment on Linux SBCs

## Source Identity

- **Canonical name:** Docker deployment with Linux SBC NBD and USB gadget access
- **Type:** web research
- **Topic focus:** Whether RemotePFS can run as a Docker image on an SBC, including USB OTG gadget exposure from the host
- **Researched on:** 2026-09-06
- **Upstream sources:**
  - [Docker `run` reference](https://docs.docker.com/engine/containers/run/)
  - [Docker runtime privilege and Linux capabilities](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)
  - [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/)
  - [Docker volumes and NFS volume options](https://docs.docker.com/engine/storage/volumes/#create-a-service-which-creates-an-nfs-volume)
  - [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)
  - [Docker Compose service attributes](https://docs.docker.com/reference/compose-file/services/)
  - [Linux USB gadget through ConfigFS](https://docs.kernel.org/usb/gadget_configfs.html)
  - [Linux mass-storage ConfigFS ABI](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/ABI/testing/configfs-usb-gadget-mass-storage)
  - [Linux Network Block Device documentation](https://docs.kernel.org/admin-guide/blockdev/nbd.html)

## Executive Summary

Docker deployment is viable for RemotePFS on a **Linux SBC running Docker Engine**, but Docker does not abstract away the hardware contract. Docker packages the Python service, nbdkit, and supporting user-space tools. The host still supplies the Linux kernel, NBD module, NFS client, ConfigFS, USB Device Controller (UDC), USB gadget modules, device nodes, and hardware role configuration.

USB OTG exposure is not ordinary USB device passthrough. RemotePFS must configure the host's USB gadget framework through `/sys/kernel/config/usb_gadget` and bind the gadget to a host UDC listed in `/sys/class/udc`. A container can manipulate those host paths only with explicit host bind mounts and elevated capabilities, commonly `CAP_SYS_ADMIN`; a prototype may use `privileged: true`, but that gives the container broad host access and is not a good default.

Recommended deployment model: build and publish multi-platform images, but keep a small host bootstrap layer. The host loads kernel modules, prepares NFS and ConfigFS, owns lifecycle and cleanup, and starts the container. The container handles RemotePFS user space. A single fully privileged container can be made to work, but it reduces isolation and makes failures during gadget teardown more dangerous.

## Table of Contents

1. Source Identity
2. Scope and Question
3. Docker and Linux Kernel Boundary
4. NFS Access Models
5. NBD Access Model
6. USB OTG Gadget Access
7. Viability Matrix
8. Recommended Deployment Designs
9. Prototype Compose Shape
10. Security and Failure Analysis
11. Portability Limits
12. Actionable Checklist
13. Discrepancies and Unknowns
14. Source Index

## Scope and Question

This article evaluates deployment of the existing RemotePFS architecture in a Docker container on Linux SBCs. It covers ARM image distribution, NFS source access, `/dev/nbd0`, nbdkit, ConfigFS USB mass storage, UDC binding, lifecycle, and security.

It does not claim that a particular SBC has a usable UDC, that a specific kernel exposes the required gadget modules, or that a PS5 accepts the resulting gadget. Those are target-hardware checks.

## Docker and Linux Kernel Boundary

Docker containers are isolated processes, not virtual machines. Docker's multi-platform documentation explicitly notes that containers share the host kernel and that image architecture must match the host architecture unless emulation is used. A manifest list can provide `linux/arm64` and `linux/arm/v7` variants, and Docker selects the matching variant on pull.

This gives RemotePFS a useful packaging boundary:

| Layer | Docker provides | Host still provides |
|---|---|---|
| Python, FastAPI, nbdkit plugin | Image filesystem and dependencies | Compatible Linux user-space ABI |
| ARM portability | `linux/arm64`, optional `linux/arm/v7` image variants | Matching CPU architecture and kernel |
| NFS reads | Process and mount target | Kernel NFS client, routes, credentials, NAS reachability |
| NBD attach | `nbd-client` process | `nbd` kernel module and `/dev/nbd0` |
| USB mass storage | Gadget configuration commands | ConfigFS, gadget modules, UDC, USB role, kernel |
| Lifecycle | Container restart policy | Host module loading, cleanup, permissions, cable/role state |

Docker's default container cannot access host devices. Docker documents three relevant controls:

- `--device` exposes selected host device nodes without using `--privileged`.
- `--cap-add` adds individual Linux capabilities.
- `--privileged` grants all capabilities, all host devices, and broad security-policy access. Docker warns to use it with caution.

For RemotePFS, `--device=/dev/nbd0` and `CAP_SYS_ADMIN` are the minimum plausible starting point for NBD operations. Exact permission requirements depend on kernel, Docker runtime, AppArmor/SELinux policy, and whether the container performs mounts or ConfigFS writes. `[UNVERIFIED]` Validate this combination on the target SBC.

Docker's multi-platform build command is:

```console
docker buildx build --platform linux/amd64,linux/arm64 .
```

The image can include an ARM64 variant for modern 64-bit SBCs and an ARMv7 variant if the target kernel/userspace and dependencies support it. Multi-platform image selection does not make a UDC or kernel module portable.

## NFS Access Models

### Model A: Host-mounted NFS, read-only bind mount into container

Host mounts NFS at `/mnt/remotepfs/nas1`. Container receives a read-only bind mount at the same path:

```yaml
volumes:
  - type: bind
    source: /mnt/remotepfs/nas1
    target: /mnt/remotepfs/nas1
    read_only: true
```

This is the recommended model. Host systemd or an existing mount unit owns NFS credentials, mount options, retries, and unmount ordering. The container only reads files. Docker documents that bind mounts are host paths, are read-write by default, and can be made read-only; it also warns that bind mounts are host-specific and have security impact.

Advantages:

- No `mount(2)` capability inside container.
- NFS credentials and network filesystem lifecycle stay on host.
- Container cannot remount or change NFS options.
- RemotePFS source paths remain ordinary read-only paths.

### Model B: Docker-managed NFS volume

Docker's local volume driver can create an NFS volume. Docker documents NFSv3 and NFSv4 examples using `volume-driver=local`, `volume-opt=type=nfs`, `device`, and `o` mount options.

Example shape:

```yaml
volumes:
  nas1:
    driver: local
    driver_opts:
      type: nfs
      o: addr=192.0.2.10,nfsvers=4,ro,hard
      device: :/volume1/games
```

This moves the mount operation to the Docker host/daemon rather than into the container. It can simplify Compose, but Docker daemon permissions and NFS option behavior become part of deployment. Confirm NFSv4.1 and the required RemotePFS options on the target Docker Engine. Docker's documented example uses NFSv4 and `nfsvers=4`; it does not prove every Docker/host combination supports `nconnect=2`.

### Model C: Mount NFS inside container

This requires a mount-capable container, host kernel NFS support, network access, and elevated capability. Docker's own capability documentation shows that mount-like operations commonly require `CAP_SYS_ADMIN`; `--privileged` is the broad fallback. This model expands container authority and complicates shutdown. Do not use it for production unless host mounting is impossible and the threat model accepts that authority.

## NBD Access Model

Linux NBD documentation describes the kernel NBD module as the client-side block-device component. A userspace server supplies data while Linux exposes a block device. RemotePFS's nbdkit process is userspace; `/dev/nbd0` belongs to the host kernel.

A containerized NBD path therefore requires:

1. Host loads the `nbd` module and creates `/dev/nbd0`.
2. Docker exposes `/dev/nbd0` with `--device` or Compose `devices`.
3. Container runs `nbd-client -U <Unix socket> -r /dev/nbd0`.
4. nbdkit and its Python plugin run inside the container or as a host service.
5. Host or container then presents `/dev/nbd0` to the USB mass-storage gadget.

Docker's `--device` control limits which device nodes are visible, but device visibility does not automatically grant every ioctl or mount permission. `CAP_SYS_ADMIN` may be required for NBD attach. `[UNVERIFIED]` Test `nbd-client -r` and disconnect behavior with the exact SBC kernel and Docker runtime.

Safer lifecycle split:

- Host loads `nbd` and owns the final `/dev/nbd0` cleanup.
- Container owns nbdkit and exposes its Unix socket through a bind-mounted runtime directory.
- A host helper or controlled container command performs NBD attach and gadget bind.

A single container can run all steps, but container restart must detach NBD and unbind UDC before a new process claims the same resources.

## USB OTG Gadget Access

### What “expose the OTG port” means

A USB OTG port in device/peripheral mode is represented by a Linux USB Device Controller. It is not normally exposed as a generic `/dev/usb-otg` node. The USB gadget framework is configured through ConfigFS, and the gadget is bound by writing a UDC name found under `/sys/class/udc/*` to the gadget's `UDC` attribute.

The kernel ConfigFS guide requires:

1. `CONFIGFS_FS` and USB gadget support in the kernel.
2. `libcomposite` loaded.
3. ConfigFS mounted.
4. Gadget directory created under `usb_gadget`.
5. VID/PID and strings written.
6. Configurations and functions created.
7. Function symlinked into a configuration.
8. UDC name written to `UDC` to enable enumeration.
9. Empty string written to `UDC` to disable it.

The mass-storage ABI documents `functions/mass_storage.<name>` and its LUN attributes:

| Attribute | Meaning |
|---|---|
| `file` | Backing file or block path for LUN |
| `ro` | Make LUN read-only |
| `nofua` | Control FUA behavior for SCSI writes |
| `forced_eject` | Detach backing file while active |
| `stall` | Permit bulk endpoint halts |

### Container access pattern

A prototype container could receive host ConfigFS paths:

```yaml
volumes:
  - type: bind
    source: /sys/kernel/config
    target: /sys/kernel/config
  - type: bind
    source: /sys/class/udc
    target: /sys/class/udc
    read_only: true
```

It also needs permission to write the ConfigFS gadget subtree. A bind-mounted ConfigFS path is still host kernel state; it does not become container-private. `--device=/dev/bus/usb` is not the right mechanism for creating a USB gadget. That option exposes host-mode USB device nodes, while OTG gadget creation uses ConfigFS and a UDC.

`CAP_SYS_ADMIN` may be enough with a carefully configured runtime, but `[UNVERIFIED]` the exact Docker/LSM/ConfigFS combination must be tested. `privileged: true` is the practical prototype fallback because Docker documents that it enables all host devices and broad capabilities, but it weakens isolation substantially.

### Required host preparation

The host must still:

- Select the USB controller's peripheral/device role.
- Load the controller and gadget modules.
- Load `libcomposite` and mass-storage support.
- Mount ConfigFS.
- Provide a UDC under `/sys/class/udc`.
- Ensure no other gadget owns the UDC.
- Unbind and remove stale gadget directories on container stop.

These are kernel and board concerns. A Docker image cannot add a missing UDC, fix a board's OTG role switch, or replace an incompatible BSP kernel.

## Viability Matrix

| Capability | Docker viability | Required host contract | Recommendation |
|---|---:|---|---|
| Package Python/nbdkit | High | Linux userspace ABI | Use image |
| ARM64/ARMv7 distribution | High | Matching architecture and dependencies | Publish multi-platform manifest |
| Read host-mounted NFS | High | Host mount and read permissions | Preferred |
| Docker-managed NFS volume | Medium-high | Docker daemon NFS support/options | Acceptable alternative |
| NFS mount inside container | Medium-low | `CAP_SYS_ADMIN`, NFS kernel, network | Avoid |
| Access `/dev/nbd0` | Medium | Host `nbd` module, device mapping, capabilities | Test on target |
| Configure ConfigFS gadget | Medium-low | RW ConfigFS bind, UDC, elevated capability | Prefer host helper |
| Run fully privileged container | Technically high | Trust container as host-root equivalent | Prototype only |
| Port to arbitrary SBC | Low | Board UDC, kernel modules, USB role, Docker | Keep host hardware profile |

## Recommended Deployment Designs

### Recommended: Docker user space plus host hardware supervisor

Keep host systemd responsible for hardware and kernel lifecycle:

1. Host loads `nbd`, `libcomposite`, and mass-storage modules.
2. Host mounts NFS read-only.
3. Host creates and owns ConfigFS gadget lifecycle.
4. Docker container runs Python service and nbdkit.
5. Host exposes only the required NFS path, mapper state path, nbdkit socket path, and `/dev/nbd0`.
6. Host helper attaches NBD and binds the gadget after nbdkit is ready.
7. Host helper unbinds UDC, disconnects NBD, and removes stale gadget state before stopping container.

This preserves Docker portability for user space while keeping board-specific operations explicit and auditable.

### Practical prototype: one privileged container

For early experiments, one Compose service can run the complete sequence with `privileged: true`, `/dev/nbd0`, ConfigFS, UDC, NFS paths, and host networking or ordinary outbound networking. This proves functionality quickly, but it gives a container broad host access and makes a compromised service equivalent to a privileged host process. Do not call this hardened deployment.

### Avoid: Docker Desktop on development Mac as hardware test

Docker Desktop containers run inside a Linux VM. A Mac's visible USB ports are not the SBC's UDC, and Docker Desktop does not reproduce the target Linux kernel, NBD device, ConfigFS, or OTG role. Build and software-test images on Mac; run gadget validation on the Linux SBC.

## Prototype Compose Shape

This is a **starting point for target-SBC testing**, not a verified production file:

```yaml
services:
  remotepfs:
    image: ghcr.io/example/remotepfs:arm64
    restart: unless-stopped
    cap_add:
      - SYS_ADMIN
    devices:
      - /dev/nbd0:/dev/nbd0:rwm
    volumes:
      - type: bind
        source: /mnt/remotepfs/nas1
        target: /mnt/remotepfs/nas1
        read_only: true
      - type: bind
        source: /sys/kernel/config
        target: /sys/kernel/config
      - type: bind
        source: /sys/class/udc
        target: /sys/class/udc
        read_only: true
      - type: bind
        source: /run/remotepfs
        target: /run/remotepfs
      - type: bind
        source: /var/lib/remotepfs
        target: /var/lib/remotepfs
    security_opt:
      - no-new-privileges:true
```

Required changes before use:

- Replace the example image with a pinned digest.
- Confirm host has `/dev/nbd0`, ConfigFS, and at least one UDC.
- Decide whether nbd-client and gadget operations stay in a host helper.
- Add an explicit container user and file ownership policy.
- Test `CAP_SYS_ADMIN` before escalating to `privileged: true`.
- Keep NFS bind mount read-only.
- Ensure stop handling unbinds UDC and disconnects NBD.

If the container must mount NFS or manipulate host modules, this Compose shape is incomplete by design. Prefer host preparation instead.

## Security and Failure Analysis

### Privilege escalation surface

`privileged: true` grants all capabilities and all devices, and Docker documents that it relaxes AppArmor/SELinux protections. A RemotePFS container has network-facing NFS inputs and a local HTTP control plane; broad host authority increases the impact of a service compromise.

Use, in order of preference:

1. Host NFS mount plus read-only bind mount.
2. Host module loading and UDC setup.
3. `--device=/dev/nbd0` instead of all devices.
4. `CAP_SYS_ADMIN` only where required, tested against the target kernel.
5. Read-only container root and read-only source mounts.
6. Host systemd cleanup on stop and watchdog failure.
7. `privileged: true` only for a documented prototype.

### Restart and stale state

Docker restarts a process, not necessarily the kernel state it left behind. A crash can leave a gadget bound to the UDC, an NBD device attached, or a stale ConfigFS directory. Host cleanup must run before restart. Do not assume `restart: unless-stopped` alone is safe.

### Bind mounts and portability

Docker bind mounts are tied to host paths. `/sys/kernel/config`, `/sys/class/udc`, `/dev/nbd0`, and `/mnt/remotepfs/nas1` must exist on every deployment host. A portable image still needs a board-specific host profile.

### Network mode

The container needs outbound access if it reads NFS or reaches a host-mounted service. Default Docker bridge networking may work for host-mounted NFS. `network_mode: host` can simplify discovery but removes network namespace isolation; use it only when target testing shows it is necessary.

## Portability Limits

Docker improves distribution across Linux SBCs with compatible architectures. It does not make these properties portable:

- UDC driver name and USB role-switch behavior.
- BSP kernel configuration and module names.
- ConfigFS mount location and LSM policy.
- NBD ioctl permissions and device-node setup.
- NFS client feature set and mount option support.
- USB electrical behavior, cable, power, and PS5 enumeration.

For Radxa Cubie A7S or another target board, record kernel version, architecture, UDC names, module availability, Docker Engine version, and successful NBD/gadget commands. `[UNVERIFIED]` Do not generalize one board's Docker privilege recipe to another without repeating those checks.

## Actionable Checklist

1. Confirm Docker Engine runs native Linux containers on target SBC.
2. Confirm `uname -m`, Docker architecture, and image platform agree.
3. Build/publish `linux/arm64` and any required `linux/arm/v7` image variants.
4. Load host `nbd`, `libcomposite`, and mass-storage modules.
5. Confirm `/dev/nbd0`, `/sys/kernel/config`, and `/sys/class/udc/*` exist.
6. Mount NFS on host with RemotePFS read-only options, or validate Docker's NFS volume driver.
7. Start container with read-only NFS bind and only required device/capability access.
8. Test nbdkit socket, `nbd-client -r`, and safe disconnect.
9. Test ConfigFS gadget creation, UDC bind, USB enumeration, and cleanup.
10. Test container restart while host cleanup is active.
11. Validate PS5 detection and ShadowMountPlus scanning on target hardware.
12. Pin image digest and document board-specific host prerequisites.

## Discrepancies and Caveats

- Docker documents NFS volume examples, but those examples do not establish that every Docker Engine supports RemotePFS's exact NFSv4.1 options, including `nconnect=2`. Validate on target.
- Docker documents `--device` as a narrower alternative to `--privileged`, but it does not guarantee that NBD ioctls or ConfigFS writes will pass the target kernel's capability and LSM checks.
- Linux ConfigFS documentation defines the gadget and UDC flow, but it does not define Docker namespace or bind-mount behavior. Container ConfigFS access remains an integration test item.
- A multi-platform image solves CPU architecture selection, not kernel, UDC, NBD, NFS, or USB compatibility.
- `[UNVERIFIED]` Exact minimum capability set for RemotePFS on the target SBC is unknown until Docker Engine, kernel, LSM, and board UDC testing is performed.

## Source Index

1. [Docker `run` reference](https://docs.docker.com/engine/containers/run/) — device, capability, privilege, and namespace controls.
2. [Docker runtime privilege and Linux capabilities](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities) — `--privileged`, `--device`, and capability behavior.
3. [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/) — manifest selection and host-kernel boundary.
4. [Docker volumes and NFS](https://docs.docker.com/engine/storage/volumes/#create-a-service-which-creates-an-nfs-volume) — host-managed NFS volume examples.
5. [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/) — host path, read-only, propagation, and portability behavior.
6. [Docker Compose services](https://docs.docker.com/reference/compose-file/services/) — `cap_add`, `cap_drop`, devices, and service configuration.
7. [Linux USB gadget ConfigFS](https://docs.kernel.org/usb/gadget_configfs.html) — gadget construction, functions, UDC binding, and cleanup.
8. [Linux mass-storage ConfigFS ABI](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/ABI/testing/configfs-usb-gadget-mass-storage) — LUN `file`, `ro`, `nofua`, `forced_eject`, and `stall` attributes.
9. [Linux NBD documentation](https://docs.kernel.org/admin-guide/blockdev/nbd.html) — kernel module and userspace NBD model.

## Reindex Notes

- New article: `14-docker-sbc-deployment.md`.
- This is web research; no source-artifact folder is required.
- Revalidate Docker flags, target kernel capabilities, and UDC behavior during SBC bring-up.
