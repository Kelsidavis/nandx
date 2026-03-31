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
