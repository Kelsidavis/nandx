# LB Tool H7 NAND Dump File Format

Reverse-engineered from `nandWild.cp37-win32.pyd` disassembly and binary analysis
of dump files from `fileoss.lbtool.net`.

## File Structure

```
[16 bytes]     File header
[N × 18336]    Raw NAND pages (one complete block per die)
[512 bytes]    Block address table (bad block test map)
```

### File Header (16 bytes)

```c
struct h7_header {
    uint32_t field0;    // Unknown — varies per file, possibly checksum
    uint32_t field1;    // Unknown
    uint32_t field2;    // Unknown
    uint32_t field3;    // Unknown
};
```

The header fields are different for every dump file, even of the same chip type.
They do not directly encode file size, chip type, or page count.

### NAND Page Data

Each NAND page is stored as raw physical data including the spare/OOB area:

```
Page structure: [16384 bytes data] [1952 bytes spare/ECC]
Total per page: 18336 bytes (0x47A0)
Pages per block: 384
```

For a standard "blank" dump file:
- One complete NAND block is stored (384 pages)
- Total page data: 384 × 18336 = 7,041,024 bytes

The page data includes the **NAND hardware randomizer output**. Unlike the
standard programmer format (dosdude1/JC P13) which applies a fixed XOR
descramble, the H7 saves raw data as read from the physical NAND cells
through the chip's data randomizer.

This means:
- Page data has ~8.0 bits/byte entropy (appears random)
- The erased pattern is different per page (address-dependent randomizer)
- No standard patterns (F1, F2, F3, Tags) are recognizable without
  knowing the chip family's randomizer polynomial

### Block Address Table (512 bytes)

Follows immediately after the page data. Contains 16-byte entries:

```c
struct block_address_entry {
    uint8_t  die_number;    // 01, 02, 03 etc.
    uint8_t  command;       // 0xA2 (write/test command)
    uint16_t block_address; // NAND block address (single-bit-clear pattern)
    uint32_t page_size;     // 0x000047A0 = 18336
    uint32_t param1;        // 0x00000200 (512) or 0x00000000
    uint32_t param2;        // varies: 0x0000079C, 0x00000005, 0x00000180
};
```

Example from KICM233 dump (17 entries):

```
Die  Cmd   BlkAddr   PageSize  Param1   Param2
 01  0xA2  0xFFFE    0x47A0    0x0200   0x079C
 02  0xA2  0xFFFE    0x47A0    0x0200   0x0005
 01  0xA2  0xFFFD    0x47A0    0x0200   0x079C
 02  0xA2  0xFFFD    0x47A0    0x0200   0x0005
 ...
 03  0xA2  0xFFFE    0x47A0    0x0000   0x0180  (final entry)
```

The block addresses use a single-bit-clear pattern:
```
0xFFFE = 1111111111111110  (bit 0 clear)
0xFFFD = 1111111111111101  (bit 1 clear)
0xFFFB = 1111111111111011  (bit 2 clear)
0xFFF7 = 1111111111110111  (bit 3 clear)
0xFEFF = 1111111011111111  (bit 8 clear)
0xFDFF = 1111110111111111  (bit 9 clear)
0xFBFF = 1111101111111111  (bit 10 clear)
0xF7FF = 1111011111111111  (bit 11 clear)
```

This is a bad block detection pattern — each address tests a different
bit position to verify the NAND block can store all bit combinations.

## File Size Variants

| Config type | File size | Pages | Notes |
|------------|-----------|-------|-------|
| General (iPhone/iPad) | 7,078,416 | ~386 | Extra 2 pages for device config |
| Mac-specific | 7,041,552 | 384 | Exactly one NAND block |
| Mac UN100 (large) | 13,550,832 | ~739 | Nearly two blocks |

## Conversion Notes

### H7 → Standard Format

Cannot be done by simple transformation because:
1. H7 data includes per-page-address NAND randomizer output
2. Standard format uses a fixed 16-byte XOR key
3. The randomizer polynomial is chip-family-specific
4. Spare/OOB data is included in H7 but stripped in standard format

### Standard → H7 Format

Would require:
1. Adding the H7 16-byte header (fields unknown)
2. Applying page-address-specific NAND randomization to each page
3. Including spare/ECC data (1952 bytes per page)
4. Building the block address table

### Format Conversion Functions

The `nandWild.pyd` module has these conversion methods:
- `runA0ToA2` — raw NAND (A0) to file format (A2)
- `runA2ToA0` — file format (A2) to raw NAND (A0)
- `runB0ToB2` — alternate raw format to file format
- `runB2ToB0` — file format to alternate raw format

These handle the page layout reordering and possibly the randomizer
transform. The `A0/A2` pair is for one NAND protocol variant and
`B0/B2` for another (possibly Toggle vs ONFI).
