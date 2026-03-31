# Apple Silicon NAND Tool

Tools for analyzing, adapting, and preparing blank NAND images for Apple Silicon MacBook SSD repair.

When a MacBook's NAND chip fails or needs replacement, you can't just solder on a new blank chip — the SoC expects to find specific initialization data. These tools work with NAND programmer dump files to prepare chips that the SoC will accept, allowing DFU restore to complete.

## Quick start

```bash
# Analyze a dump file
python3 nand_tool.py info dump.bin

# List known chip types
python3 nand_tool.py list-chips

# Adapt dumps from one chip type to another (same generation)
python3 nand_tool.py adapt ./donor_dir/ <target_f2_hex> ./output/ CHIP_NAME

# Launch the GUI
python3 nand_gui.py
```

## How it works

### Dump file format

Each `.bin` file from a NAND programmer has this structure:

| Offset | Size | Content |
|--------|------|---------|
| 0x00 | 16 bytes | Tool header (universal constant) |
| 0x10 | N MB | NAND data, XOR-scrambled with fixed key |

The scrambled NAND data is organized as 512-byte slots:

- **Header slots** (11-41 depending on chip type): sparse records containing
  - F1: page identifier (per-generation constant)
  - F2: chip type identifier (unique per NAND model)
  - F3: authentication tag (deterministic per chip type + slot index)
  - Tag: cycling value (per-generation constant)
- **Dense data slots**: SoC initialization data (FTL tables, 1TR partition, APFS bootstrap)

### Key findings

1. **"Blank" NANDs are 90-98% full** — a properly initialized chip contains FTL tables, the 1TR recovery partition, and APFS container structures even when empty of user data.

2. **Data is XOR-scrambled** with a fixed 16-byte key. Erased NAND pages read as `2636ea30350b91efeeab499f988a90ee` instead of `0xFF`.

3. **Two NAND generations** with incompatible encryption constants:
   - **gen1_kicm**: KICM223, KICM227, KICM229, KICM233 (M1/M2 era)
   - **gen2_k5a**: K5A4, K5A5, K5A8 (M3+ era)

4. **F3 auth tags are per-chip-TYPE**, not per-physical-chip or per-SoC — verified by comparing dumps from different machines. All 7 known chip types have their F3 tables stored in the tool.

5. **Dense data regenerates every DFU restore** — comparing two reads of the same die shows 65% of data changes between initializations. Using donor data from any source is viable.

6. **Same-generation adaptation works** — K5A5 and K5A8 share 86% of their dense data. The tool can swap the F2 chip identifier and F3 auth tags to retarget a dump.

## Known chip types

| Chip | Generation | Per-chip capacity | F2 Known | F3 Known |
|------|-----------|-------------------|----------|----------|
| KICM223/R | gen1_kicm | 1TB | Yes | Yes |
| KICM225 | gen1_kicm | 128GB | No | No |
| KICM227 | gen1_kicm | 256GB | Yes | Yes |
| KICM229 | gen1_kicm | 512GB | Yes | Yes |
| KICM232 | gen1_kicm | 128GB | No | No |
| KICM233 | gen1_kicm | 256GB | Yes | Yes |
| KICM5224 | gen1_kicm | 256GB | No | No |
| K5A4 | gen2_k5a | 256GB | Yes | Yes |
| K5A5 | gen2_k5a | ? | Yes | Yes |
| K5A8 | gen2_k5a | 1TB | Yes | Yes |

To register a new chip type, get one dump and run:
```bash
python3 nand_tool.py register-chip dump.bin "CHIP_NAME (capacity)"
```

## Mac model configurations

From [logi.wiki](https://logi.wiki/index.php/MacBook_NAND_List):

### M1 (A2337 Air / A2338 Pro)
| Total | Position | Chip (Kioxia) | Chip (Hynix) |
|-------|----------|--------------|--------------|
| 256GB | UN000 | KICM232 (128GB) | H23B1T82D7AEQ |
| | UN100 | KICM225 (128GB) | H23B1T82D7AEQ |
| 512GB | UN000 | KICM233 (256GB) | H23B2588H7AEQ-BC |
| | UN100 | KICM227 (256GB) | H23B2T83G7AEQ-BC |
| 1TB | UN000 | KICM229 (512GB) | — |
| | UN100 | KICM229 (512GB) | — |
| 2TB | UN000 | KICM223R (1TB) | H23B8T85K7AFJ-BC |
| | UN100 | KICM223R (1TB) | H23B8T85K7AFJ-BC |

### M1 Pro (A2442 14")
| Total | Positions | Chip |
|-------|-----------|------|
| 512GB | UN000-UN300 (4 chips) | KICM229 |
| 1TB | UN000, UN100 | KICM223 |

### M3 Air (A2901)
| Total | Position | Chip |
|-------|----------|------|
| 256GB | UN000, UN100 | K5A4 |
| 512GB | UN000, UN100 | K5A5 |
| 1TB | UN000, UN100 | K5A8 |

## CLI reference

```
nand_tool.py info <file>              Show dump structure
nand_tool.py info-all <dir>           Analyze all .bin in directory
nand_tool.py detail <file>            Show header slot table
nand_tool.py compare <a> <b>          Compare two dumps
nand_tool.py adapt <dir> <f2> <out>   Adapt dumps to new chip type
nand_tool.py generate <f2> ...        Generate minimal erased dumps
nand_tool.py list-chips               Show known chip types
nand_tool.py register-chip <f> <n>    Register new chip type
nand_tool.py descramble <in> <out>    Remove XOR scrambling
nand_tool.py scramble <in> <out>      Re-apply XOR scrambling
```

## Dump sources

- [dosdude1](http://dosdude1.com/files/Mac-NAND/Apple-Silicon/)
- [logi.wiki NAND list](https://logi.wiki/index.php/MacBook_NAND_List)
- [MacRumors SSD upgrade thread](https://forums.macrumors.com/threads/apple-silicon-soldered-ssd-upgrade-thread.2417822/)

## License

Tools are provided as-is for repair purposes.
Dump files are from public sources (dosdude1, community contributions).
