# NandX

Apple Silicon NAND image toolkit for MacBook SSD repair.

When a MacBook's NAND chip fails or needs replacement, you can't just solder on a new blank chip — the SoC expects specific initialization data. NandX reverse-engineers the NAND programmer dump format, letting you analyze, adapt, and prepare chip images for any supported configuration.

## Quick start

```bash
# Analyze a dump file
python3 nand_tool.py info dump.bin

# Scan a wiped/unknown chip for surviving chip identity
python3 nand_tool.py scan mystery_chip.bin

# List known chip types and generations
python3 nand_tool.py list-chips

# Adapt dumps from one chip type to another (same generation)
python3 nand_tool.py adapt ./donor_dir/ <target_f2_hex> ./output/ CHIP_NAME

# Launch the GUI
python3 nand_gui.py
```

Requirements: Python 3.6+, optional NumPy for ~50x faster processing.

## NAND architecture

### Master/slave chip layout

Apple Silicon Macs use a **master + slave** NAND architecture. The NAND0 (UN000) position always uses an **over-provisioned master chip** with ~25% extra raw capacity that the SoC reserves for:

- FTL (Flash Translation Layer) metadata
- Bad block replacement pool
- Wear leveling reserves
- 1TR recovery partition
- ANS controller state

The remaining positions (UN100, UN200, UN300) use standard-capacity slave chips.

| Role | Chip | Raw capacity | Used as |
|------|------|-------------|---------|
| Master | KICM232 | 160GB | 128GB (in 256GB / 4-NAND 512GB configs) |
| Master | KICM233 | 320GB | 256GB (in 512GB / 4-NAND 1TB configs) |
| Slave | KICM225 | 128GB | 128GB |
| Slave | KICM227 | 256GB | 256GB |
| Slave | KICM229 | 512GB | 512GB |
| Slave | KICM223/R | 1TB | 1TB |

### Capacity upgrades

The same board supports multiple storage configurations — the SoC detects NAND type via JEDEC hardware ID and configures dynamically. Capacity upgrades (e.g., 512GB to 2TB) work by soldering larger chips. The SoC handles the rest during DFU restore.

Confirmed working upgrades (community-verified):
- 512GB → 2TB on M1 MacBook Air using 2× Hynix H23B8T85K7AFJ-BC 1TB chips

### Blank vs wiped chips

- **Factory blank chips**: Solder directly onto the board, DFU restore — no programmer needed for M1 and later.
- **Wiped/erased chips** (previously used): May have residual data that confuses the SoC. DFU restore can fail at ~40% with state transition errors. These chips may need to be programmed with valid initialization data using a NAND programmer (JC P13/P15, LB H7).

## Dump file format (reverse-engineered)

Each `.bin` file from a NAND programmer has this structure:

| Offset | Size | Content |
|--------|------|---------|
| 0x00-0x0F | 16 bytes | Tool header (`e32fb28fa62973d867e54fdc03892414`) |
| 0x10+ | N MB | NAND data, XOR-scrambled with key `d9c915cfcaf46e101154b66067756f11` |

Erased NAND pages read as `2636ea30350b91efeeab499f988a90ee` (not `0xFF`).

The descrambled NAND data is organized as 512-byte slots:

**Header slots** (11, 21, or 41 depending on chip type):
```
0x000-0x00F: F1  — page identifier (per-generation constant cycle)
0x010-0x01F: F2  — chip type identifier (unique per NAND model)
0x020-0x02F: F3  — authentication tag (deterministic per chip type + slot index)
0x030-0x1EF: 0xFF padding
0x1F0-0x1FF: Tag — cycling value (per-generation constant)
```

**Dense data slots**: Full 512 bytes of SoC initialization data (FTL tables, 1TR partition, APFS container bootstrap). These make up 90-98% of each dump file.

## Key findings

1. **"Blank" NANDs are 90-98% full** — even with no user data, the SoC writes extensive initialization structures.

2. **Two NAND generations** with incompatible F1/Tag encryption constants:
   - **gen1_kicm**: KICM223/225/227/229/232/233 (M1/M2 era)
   - **gen2_k5a**: K5A4, K5A5, K5A8 (M3+ era)
   - Cross-generation adaptation will **not** work.

3. **F3 auth tags are per-chip-TYPE** — verified identical across different physical chips and different SoCs. One dump of any chip type provides the F3 table for all chips of that type.

4. **Dense data regenerates every DFU restore** — 65% of data changes between re-reads of the same die. Using donor dense data is viable since the SoC overwrites it during restore.

5. **Same-generation adaptation works** — the tool can retarget dumps between chip types within the same generation by swapping F2 and F3 fields. K5A5 and K5A8 share 86% of their dense data.

6. **The SoC detects NAND type via hardware JEDEC ID** — F2 in the dump is programmer tool metadata, not read by the SoC. This means cross-vendor adaptation (Kioxia → Hynix) may work if the SoC re-initializes during DFU.

## Known chip types

| Chip | Generation | Raw capacity | Role | F2/F3 Known | Header slots |
|------|-----------|-------------|------|-------------|-------------|
| KICM223/R | gen1_kicm | 1TB | slave | Yes | 41 |
| KICM225 | gen1_kicm | 128GB | slave | No | — |
| KICM227 | gen1_kicm | 256GB | slave | Yes | 41 |
| KICM229 | gen1_kicm | 512GB | slave | Yes | 41 |
| KICM232 | gen1_kicm | 160GB | master | No | — |
| KICM233 | gen1_kicm | 320GB | master | Yes | 11 |
| KICM5224 | gen1_kicm | 256GB | ? | No | — |
| K5A4 | gen2_k5a | 256GB | ? | Yes | 21 |
| K5A5 | gen2_k5a | ? | ? | Yes | 41 |
| K5A8 | gen2_k5a | 1TB | ? | Yes | 41 |

Hynix equivalents (F2/F3 unknown — no public dumps exist):

| Hynix chip | Kioxia equivalent | Role |
|-----------|------------------|------|
| H23B1T82D7AEQ | KICM232 / KICM225 | 256GB config |
| H23B2588H7AEQ-BC | KICM233 | 512GB master |
| H23B2T83G7AEQ-BC | KICM227 | 512GB slave |
| H23B8T85K7AFJ-BC | KICM223R | 2TB config |

To register a new chip type from a dump:
```bash
python3 nand_tool.py register-chip dump.bin "CHIP_NAME (capacity)"
# Outputs the F2 identifier and complete F3 table, ready to paste into the code
```

## Mac model configurations

Sources: [logi.wiki](https://logi.wiki/index.php/MacBook_NAND_List), [dosdude1](https://forums.macrumors.com/threads/apple-silicon-soldered-ssd-upgrade-thread.2417822/)

### M1 Air / Pro (A2337 / A2338)

| Total | UN000 (master) | UN100 (slave) |
|-------|---------------|--------------|
| 256GB | KICM232 160GB | KICM225 128GB |
| 256GB (Hynix) | H23B1T82D7AEQ | H23B1T82D7AEQ |
| 512GB | KICM233 320GB | KICM227 256GB |
| 512GB (Hynix) | H23B2588H7AEQ-BC | H23B2T83G7AEQ-BC |
| 1TB | KICM229 512GB | KICM229 512GB |
| 1TB (SanDisk) | SDREGJHIH 512GB | SDREGJHIH 512GB |
| 2TB | KICM223R 1TB | KICM223R 1TB |
| 2TB (Hynix) | H23B8T85K7AFJ-BC | H23B8T85K7AFJ-BC |

### M1 Pro 14" (A2442)

| Total | UN000 (master) | UN100 | UN200 | UN300 |
|-------|---------------|-------|-------|-------|
| 512GB | KICM232 160GB | KICM225 128GB | KICM225 128GB | KICM225 128GB |
| 1TB | KICM233 320GB | KICM227 256GB | KICM227 256GB | KICM227 256GB |

### M2 Air (A2681)

| Total | UN000 | UN100 |
|-------|-------|-------|
| 256GB | KICM5224 256GB (single chip) | — |
| 512GB | KICM233 320GB | KICM227 256GB |

### M3 Air (A2901)

| Total | UN000 | UN100 |
|-------|-------|-------|
| 256GB | K5A4 | K5A4 |
| 512GB | K5A5 | K5A5 |
| 1TB | K5A8 | K5A8 |

## CLI reference

```
nand_tool.py info <file>                           Analyze a dump file
nand_tool.py info-all <dir>                        Analyze all .bin in directory
nand_tool.py detail <file>                         Show header slot table
nand_tool.py compare <a> <b>                       Compare two dumps in detail
nand_tool.py scan <file>                           Scan wiped/corrupted dump for chip identity
nand_tool.py adapt <file_or_dir> <f2> <out> [name] Adapt to new chip type (F2+F3)
nand_tool.py generate <f2> <dies> <mb> <out> [n]   Generate minimal erased dumps
nand_tool.py list-chips                            Show known chip types and generations
nand_tool.py register-chip <file> <name>           Register new chip type (outputs F2+F3)
nand_tool.py descramble <in> <out>                 Remove XOR scrambling for analysis
nand_tool.py scramble <in> <out>                   Re-apply XOR scrambling
```

## Troubleshooting

### DFU restore fails at ~40%

The SoC starts initialization but can't complete the NAND partition setup. Common causes:
- **Wiped chips with residual data** — try full chip erase with programmer, or flash known-good blank dumps
- **Wrong chip position** — master chip (over-provisioned) must go in UN000
- **Cross-vendor mismatch** — if Kioxia dumps are flashed to Hynix chips, the SoC may reject them due to JEDEC ID mismatch

### Debugging DFU failures

Run `idevicerestore` in debug mode to capture the TSS handshake log:
```bash
idevicerestore -d -e    # debug + enumerate only
```
The log shows what hardware the SoC reports to Apple's servers, including NAND detection status.

### Identifying wiped chips

Even wiped chips may have surviving header data:
```bash
python3 nand_tool.py scan wiped_dump.bin
```
If the chip was crypto-erased by the SoC (not raw-erased by a programmer), the F2 identifier and F3 table may be intact on the physical NAND.

## Dump sources

- [dosdude1 NAND repository](http://dosdude1.com/files/Mac-NAND/Apple-Silicon/)
- [logi.wiki NAND list](https://logi.wiki/index.php/MacBook_NAND_List)
- [MacRumors Apple Silicon SSD upgrade thread](https://forums.macrumors.com/threads/apple-silicon-soldered-ssd-upgrade-thread.2417822/)
- [Badcaps NAND chip change method](https://www.badcaps.net/forum/document-software-archive/schematics-and-boardviews/3514524-apple-macbook-pro-air-2018-and-above-nand-chip-change-and-method-m1-m2-m3-m4)
- [Tamas Gal M1 2TB upgrade writeup](https://www.tamasgal.com/mac/m1-macbook-air-2t-nand-upgrade/)

## License

Tools are provided as-is for repair purposes.
Dump files are from public sources (dosdude1, community contributions).
