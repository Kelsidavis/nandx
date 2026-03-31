# DFU Restore Internals — Apple Silicon NAND Initialization

## Restore Process Phases

When Apple Configurator (or idevicerestore) performs a DFU restore on an Apple Silicon Mac,
the process runs in these phases:

```
 0-15%   Boot chain: iBSS + iBEC loaded to SRAM via USB, signature-verified
15-30%   RestoreRamDisk + kernelcache loaded to DRAM, ANS controller initialized
30-45%   PARTITION CREATION — restored_external creates APFS containers
           - Create iBoot System Container (ISC) — disk0s1
           - Create System + Data APFS volumes
           - Format encrypted data partition
45-90%   ASR (Apple System Restore) streams macOS root filesystem image
90-100%  Firmware finalization: NOR/SPI update, NVRAM config, boot args
```

## The 40% Failure

DFU restore failing at ~40% with "could not switch DFU states" means the failure
occurs during partition creation. The `restored_external` binary running on the
restore ramdisk is trying to set up APFS containers on the NAND and failing.

### Why it fails on wiped chips

Factory-blank NAND: all pages erased → restored_external sees empty storage → creates
fresh partitions → success.

Crypto-erased NAND (wiped by SoC): encryption keys destroyed but physical data remains →
restored_external reads residual APFS/FTL structures from previous SoC → tries to
interpret them → data is invalid → state machine error → fails.

Programmer-erased NAND (raw 0xFF): should behave like blank, BUT the erase may not have
covered all areas (spare/OOB, special blocks) or the ANS controller may cache stale state.

## Boot Chain Security

Every stage is signature-verified. No unsigned code can execute:

```
Boot ROM (silicon, read-only)
  → signature check → iBSS (loaded from USB)
    → signature check → iBEC (loaded from USB)  
      → signature check → kernelcache + RestoreRamDisk (from USB)
        → restored_external runs the actual restore
```

No checkm8-style Boot ROM exploit exists for M1+. The boot chain cannot be hijacked.

## Firmware Encryption Status

Component encryption in Apple Silicon Mac IPSW files:

| Component | Encrypted? | Notes |
|-----------|-----------|-------|
| iBSS | YES | AES with GID key |
| iBEC | YES | AES with GID key |
| SEP firmware | YES | AES with GID key |
| kernelcache | NO | Signed (IMG4) but payload is readable |
| RestoreRamDisk | NO | Signed (IMG4) but DMG is readable |
| Root filesystem | NO | Streamed via ASR |

The RestoreRamDisk is **not encrypted** — it can be extracted and analyzed.
The `restored_external` binary inside it contains the NAND initialization logic
that decides whether to create partitions or bail out.

## Reverse Engineering Approach

Since we can't modify the ramdisk (signature check), the approach is:

1. Extract `restored_external` from the RestoreRamDisk DMG
2. Reverse-engineer the NAND initialization / partition creation code
3. Understand what state the NAND must be in for partition creation to succeed
4. Use a NAND programmer to write exactly that state to wiped chips

The goal is NOT to bypass security — it's to understand what "blank enough" looks
like to restored_external, so we can prepare wiped chips to be accepted.

## Tools

- `ipsw` (github.com/blacktop/ipsw) — extract IPSW components including ramdisk
- Ghidra / IDA — disassemble restored_external (ARM64)
- IPSW download — mrmacintosh.com/apple-silicon-m1-full-macos-restore-ipsw-firmware-files-database/

## Related: m1n1 / Asahi Linux

m1n1 has reverse-engineered the ANS controller and can access NAND directly.
However, m1n1 requires working NAND to boot (installed via 1TR), creating a
chicken-and-egg problem for bricked devices. It cannot be loaded via DFU
because it's not Apple-signed.

## References

- https://www.theiphonewiki.com/wiki/IPhone_Restore_Procedure
- https://asahilinux.org/docs/fw/boot/
- https://github.com/blacktop/ipsw
- https://theapplewiki.com/wiki/Decrypting_Firmwares
- https://oliviagallucci.com/boot-rom-security-on-silicon-macs-m1-m2-m3/

## restored_external Binary Analysis (macOS 12.0.1 Monterey)

Extracted from: `UniversalMac_12.0.1_21A558_Restore.ipsw`
Path in ramdisk: `usr/local/bin/restored_external` (1.9MB ARM64e Mach-O)

### NAND Initialization Flow (from string analysis)

```
1. Searching for NAND service
2. Found NAND service: %s
3. NAND initialized. Waiting for devnode.
   OR: NAND failed to initialize: %s  ← POSSIBLE FAILURE POINT
4. asp_nand_set_writable
5. clean_NAND / clean_nand
   OR: failed to clean NAND
6. NAND format complete
7. update_NAND → update_NAND_firmware
   - Checks FTL version → "FTL version mismatch. Erase install required"
   - Checks ECC/DM version → "ECC or DM version mismatch"
8. create_filesystem_partitions
   - create_partition_for_apfs
   - create_iboot_system_container_filesystems (ISC = disk0s1)
   - create_apfs_filesystems
   - create_recovery_os_apfs_filesystems
   - create_volume_group
9. format_effaceable_storage
10. ASR image streaming (macOS root filesystem)
```

### Key NAND Controllers Supported

- `AppleANS2NVMeController` — standard M1 ANS
- `AppleANS2CGNVMeController` — variant
- `AppleANS3NVMeController` — M2/M3+ ANS

### Critical Function: `clean_NAND`

This function runs BEFORE partition creation. It appears to be the step that
prepares the NAND for a fresh install. If the NAND has residual data that
confuses this step, the restore fails.

The string `"failed to reserve space for overprovisioning"` suggests that
the clean/format step needs to understand the physical NAND geometry to set
up the spare block pool correctly.

### Error That Likely Causes 40% Failure

Most probable failure path for wiped chips:
```
NAND initialized → clean_NAND → "failed to clean NAND"
  OR
clean_NAND OK → create_filesystem_partitions → 
  "failed to create APFS filesystem partitions during APFS Erase Install"
  OR
  "failed to reserve space for overprovisioning"
```

### Effaceable Storage

The restore also manages "effaceable storage" — a special NAND region used
for storing encryption keys that can be securely destroyed:
```
format_effaceable_storage
effaceable storage formatted successfully
effaceable storage is formatted, clearing it
Device does not support effaceable storage. Skipping effaceable format.
```

### Over-Provisioning Reference

The string `"failed to reserve space for overprovisioning"` confirms our
earlier finding that the SoC actively manages over-provisioning. The master
chip (KICM232/KICM233) with extra raw capacity is where this spare pool
is allocated during restore.

### Dual-SPI NAND Support

```
supports_dual_SPI_NAND
+[MSUBootFirmwareUpdater supportsDualSPINAND]
```
Some configurations use dual SPI NAND for boot firmware redundancy.

### Next Steps

Full disassembly of `restored_external` with Ghidra/IDA would reveal:
1. What `clean_NAND` actually does — does it erase all blocks? Check for existing data?
2. How `create_partition_for_apfs` calculates block counts for over-provisioning
3. Whether the JEDEC ID influences partition layout decisions
4. What specific NAND state causes `clean_NAND` to fail vs succeed

## clean_NAND Disassembly (function at 0x24280)

Reverse-engineered from ARM64e Mach-O binary, macOS 12.0.1.

### Pseudocode

```c
int clean_NAND(void *ctx, void *params) {
    log("entering clean_NAND");
    
    // 1. Look up storage device from restore parameters
    if (!lookup_storage_info(params)) return error;
    
    // 2. Get device node path (e.g., /dev/rdisk0)
    char devnode[32];
    if (!get_device_node(devnode, 32)) {
        log("couldn't get storage media device node");
        return error;
    }
    if (devnode[0] == '\0') {
        log("Device node was an empty string?");
        return error;
    }
    
    // 3. Open the storage device
    int fd = open_storage_device(devnode, 0);
    if (fd < 0) {
        log("unable to open %s: %s", devnode, strerror(errno));
        return error;
    }
    
    // 4. Issue the NAND clean ioctl
    uint8_t params[16] = {0};
    if (ioctl(fd, 0x8010641A, params) == -1) {
        return error;
    }
    
    log("NAND format complete");
    return 0;
}
```

### The ioctl 0x8010641A

This is the critical command that cleans the NAND:
- `0x80` = IOC_IN (write direction)
- `0x10` = 16 bytes parameter size
- `0x641A` = APFS subsystem command (likely APFS_CONTAINER_DESTROY)

This ioctl tells the ANS controller to destroy all existing APFS containers
and prepare the NAND for a fresh partition layout.

### Where YOUR Failure Happens

The failure is NOT in clean_NAND itself. It's BEFORE clean_NAND gets called.
The ANS controller must first initialize and present a block device (/dev/rdisk0).
If the NAND contains garbage data that the ANS controller can't parse into a
valid block device, the failure occurs at:

```
"NAND failed to initialize: %s" (at 0x3248C)
```

This is the ANS controller saying: "I can see the physical NAND chips via JEDEC,
but I can't build a logical block device from the data on them."

### Implication for Wiped Chips

The ANS controller (running in the SoC, not in restored_external) needs to be
able to construct a basic block device from whatever is on the NAND. For truly
blank chips, it creates one from scratch. For wiped chips with garbage residual
data, it tries to interpret the garbage as FTL structures and fails.

The fix: ensure the NAND is in a state the ANS controller can handle:
1. True full erase (every page, every block, including spare areas) — ANS treats
   this as a new chip
2. OR: write valid FTL initialization data that ANS can parse — this is what the
   "blank" dump files provide

## DetermineBlankPart() Disassembly

Function: `bool AppleEmbeddedNVMeController::DetermineBlankPart()`
Location: kernelcache file offset 0x2B4DBD8 (480 bytes)

### How Blank Detection Works

The kernel does NOT read the NAND directly. It sends an NVMe vendor-specific
command (opcode 0x11) to the ANS controller firmware and checks the response:

```c
bool DetermineBlankPart() {
    cmd = allocate_nvme_command(0x11, 4096, 4096);
    send_identify_command(cmd, 1);
    execute_and_wait(cmd);
    response = get_response(cmd);
    
    namespace_count = *(uint32_t*)(response + 516);  // offset 0x204
    
    if (namespace_count != 0) {
        log("Non-Blank NAND, Number of namespaces - %d", namespace_count);
        return false;
    } else {
        log("Blank NAND, Number of namespaces - %d", namespace_count);
        this->isBlank = true;
        return true;
    }
}
```

### The Decision Chain

```
ANS firmware (SoC coprocessor) reads raw NAND pages
  → Interprets FTL metadata structures
  → Counts valid namespaces
  → Reports namespace_count to kernel via NVMe response

Kernel reads namespace_count:
  == 0 → blank path → dummy block device → DFU restore proceeds
  != 0 → non-blank path → attempts FTL load → success or failure
```

### Why Wiped Chips Fail

The ANS firmware reads garbage data from wiped chips. If any of it
resembles namespace metadata (namespace_count != 0), the firmware
attempts to load the FTL structures. Since they're corrupted, the
load fails → "NAND Controller Init Failed" → DFU aborts at 40%.

### Why Dump Files Work

The dump files contain valid FTL structures with correct:
- Namespace definitions (count > 0)
- FTL revision numbers
- ECC version
- DM version
- Block allocation tables

The ANS firmware reads these, finds valid metadata, loads the FTL
successfully, and presents a working block device. Then clean_NAND
issues ioctl 0x8010641A to destroy and recreate the APFS containers.

### Practical Implications

To fix wiped chips, you need to either:
1. Ensure the ANS firmware reads namespace_count as 0 (true blank state)
2. Provide valid FTL metadata that the ANS firmware can load

Option 1 requires understanding what specific NAND page patterns the
ANS firmware interprets as "no namespaces". A full block-level erase
that covers ALL pages including metadata pages should achieve this.

Option 2 is what the community dump files provide — but requires dumps
from the correct chip type (Kioxia vs Hynix have different FTL layouts).

## The Scrambling / Erase Problem

### NAND Data Randomizer

Modern NAND chips have a hardware data randomizer (LFSR-based XOR) to prevent
bit patterns that cause cell-to-cell interference. This operates transparently:

```
WRITE path: host_data → XOR with randomizer → stored on physical cells
READ path:  physical cells → XOR with randomizer → host_data
```

For erased pages (hardware block erase):
```
Physical cells = all 0xFF (transistors in erased state)
Read through randomizer = 0xFF XOR scramble_key = ERASED_PATTERN
```

The ERASED_PATTERN (`2636ea30350b91efeeab499f988a90ee`) is what the programmer
reads from erased pages. The ANS firmware knows this pattern = erased.

### Why Both Erase and Write-0xFF Should Work

```
True chip erase:
  Physical = 0xFF → read → randomizer → ERASED_PATTERN
  ANS sees: erased → blank → SUCCESS

Write all 0xFF:
  0xFF → write → randomizer → scrambled → physical cells
  Read back → randomizer → descrambled → 0xFF
  ANS sees: 0xFF → blank → SUCCESS

Both produce the same result from the ANS firmware's perspective.
```

### Why Wiped Chips Might Still Fail

If a properly erased chip still fails DFU, possible causes:

1. **Incomplete erase**: The programmer didn't erase ALL blocks, particularly:
   - Reserved/system blocks that the NAND controller protects
   - Spare/OOB areas that contain metadata
   - Bad block markers that survived the erase

2. **ECC metadata**: Modern NAND stores ECC parity data in spare areas.
   A chip erase clears data pages but may leave ECC structures that
   the ANS firmware interprets as valid metadata.

3. **NAND controller internal state**: Some NAND packages have an
   internal controller that maintains its own state independently
   of the raw flash cells. This state may survive a chip erase.

4. **The chips are NOT truly blank-compatible**: The "blank NAND"
   path in the ANS firmware may require specific NAND ID responses
   that only come from never-written chips. Once a chip has been
   initialized by the ANS firmware, some internal state may be
   permanently written that cannot be erased.

### The Practical Solution

Since we can't modify the ANS firmware, we have two reliable paths:

1. **Use factory-blank chips** (never initialized by any SoC)
2. **Flash valid FTL metadata** using NandX with correct dump files
   for the chip type (takes the "Non-Blank" path with valid data)

For chips that have been previously used, option 2 is the only reliable
approach. This requires dump files for the specific NAND type (Kioxia
dumps for Kioxia chips, Hynix dumps for Hynix chips).
