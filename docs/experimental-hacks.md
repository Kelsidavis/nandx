# Experimental Approaches for Stuck/Wiped NAND Chips

When standard approaches (factory-blank chips or matching dump files) aren't available,
these experimental techniques may help recover or initialize NAND chips that are stuck
in the "corrupted but not blank" state where DFU restore fails at ~40%.

## Background

The ANS firmware (SoC storage coprocessor) has three paths:
```
namespace_count == 0  →  "Blank NAND"     →  dummy block device  →  DFU works
namespace_count > 0   →  "Non-Blank NAND" →  load FTL            →  DFU works (if valid)
corrupted/confused    →  init failure     →  no block device     →  DFU fails at 40%
```

The goal is to get from path 3 to path 1 or path 2.

## Approach 1: True Blank Image

Generate an image where every page is the erased pattern, then flash with programmer:

```bash
python3 nand_tool.py true-blank 8 blank_8mb.bin
# Flash blank_8mb.bin to the chip with LB H7 / JC P13
```

This makes every page read as erased (0xFF after hardware descrambling).
The ANS firmware should see no FTL metadata → namespace_count = 0 → blank path.

**Why standard erase might not work:** The NAND programmer's "chip erase" command
erases flash cells to 0xFF at the physical level, but the NAND hardware's data
randomizer means reads come back as the erased pattern, not raw 0xFF. Both should
ultimately produce the same result for the ANS firmware. If chip erase alone doesn't
work, the issue may be with the programmer not erasing ALL areas (spare, OOB, reserved
blocks) or internal NAND controller state surviving the erase.

## Approach 2: Minimal FTL Header

Generate an image with only the FTL header metadata (correct F2 + F3 auth tags)
and everything else erased:

```bash
# For KICM233 (320GB master chip):
python3 nand_tool.py min-ftl f007cc44bdac94bf15111ec5bc88d006 10 kicm233_minftl.bin

# For KICM227 (256GB slave chip):
python3 nand_tool.py min-ftl d3bc36674d8ec40531c35ffec6f04c91 9 kicm227_minftl.bin
```

This writes 5-20KB of valid namespace metadata and leaves everything else erased.
The ANS firmware sees valid FTL headers → namespace_count > 0 → non-blank path.
The clean_NAND step then destroys and recreates the FTL from scratch.

**Limitation:** Requires knowing the chip type's F2 value. Only works for chip types
with registered F2/F3 tables.

## Approach 3: Cross-Vendor Flash (Kioxia → Hynix)

Flash Kioxia dump files onto Hynix chips:

```bash
# Flash KICM233 dump to Hynix H23B2588 (UN000 master position):
cp dumps/gen1_kicm/KICM233/A2337/Model(A2337)_Tag(UN000)_KIC_M233_256G.bin flash_un000.bin

# Flash KICM227 dump to Hynix H23B2T83 (UN100 slave position):
cp dumps/gen1_kicm/KICM227/A2337/Model(A2337)_Tag(UN100)_KIC_M227_256G.bin flash_un100.bin
```

The ANS firmware detects the physical chip type via JEDEC ID (it knows it's Hynix)
but reads Kioxia FTL metadata from the NAND pages. Two possible outcomes:
- FTL metadata format is chip-independent → ANS loads it → clean_NAND reinitializes
  with correct Hynix parameters → DFU succeeds
- FTL metadata is chip-specific → ANS rejects it → still fails

**Worth trying** because the chips are already non-functional. This is the fastest
experiment — no programmer trickery needed, just flash the files.

## Approach 4: Downgrade ANS Firmware

Try DFU restore with the earliest macOS IPSW that supports M1:

- macOS Big Sur 11.0.1 (first M1 release, November 2020)
- URL: `https://updates.cdn-apple.com/2020/macos/...`

Older ANS firmware might have simpler blank detection logic or different version
checks. If the wiped chips have metadata from a newer macOS version, an older
ANS firmware might not recognize it and fall through to the blank path.

## Approach 5: Trigger Clog Version Mismatch

From kernel analysis, the "Clog Version Mismatch" code path at `0x2B4F84C` performs
a controller reset similar to the blank path. If we can craft NAND data that:
1. Makes namespace_count > 0 (non-blank)
2. Has FTL metadata with a deliberately wrong "Clog" version
3. Triggers `CheckClogVersionMismatch()` → returns error code 0xB

...the firmware might reset and reinitialize instead of failing.

The Clog version is checked after the NVMe command returns error code 11 (0xB):
```c
if (nvme_result == 0xB) {  // cmp w0, #0xb at 0x2B4F834
    log("Clog Version Mismatch found on non-blank part");
    if (some_flag[1042]) {
        clear_flag[1046];
        reset_controller();  // same as blank path
    }
}
```

This requires knowing the Clog version format and crafting a mismatched value.

## Approach 6: FPGA NAND Emulator

Use an FPGA board with BGA110 breakout adapter to emulate a blank NAND:
1. Solder FPGA in NAND position
2. Program it to respond as a blank NAND (all reads return erased data)
3. Boot Mac, enter DFU, let ANS initialize the "blank NAND"
4. Capture every write the ANS firmware makes during initialization
5. Now you have the exact initialization sequence
6. Flash it to the real NAND chip

**This is the nuclear option** — it gives you the initialization data for ANY
chip type, any configuration, without needing donor dumps. Once captured, the
initialization sequence can be shared for all chips of that type.

Hardware requirements:
- FPGA dev board (Xilinx/Lattice/Intel, needs fast I/O for Toggle NAND interface)
- BGA110 breakout/interposer board
- Logic level matching (NAND is typically 1.8V/3.3V)
- ONFI/Toggle NAND protocol implementation in HDL

## Approach 7: ONFI Reset Command

Instead of erasing, send a full ONFI RESET command (0xFF) to the NAND:
```
ONFI command sequence: CE# low → CLE high → write 0xFF → wait tRST
```

This resets the NAND chip's internal controller state, which may include metadata
that survives a block erase. Different from block erase — reset returns the chip
to its power-on state.

Most NAND programmers support sending raw ONFI commands. The LB H7 might have this
in an advanced menu. The JC P13 may also support it.

## Approach 8: Partial Erase — Target Metadata Pages Only

From our dump analysis, the FTL metadata is in the first ~20KB (header slots):
- Slots 0-40 (or 0-10 for KICM233): contain F1/F2/F3/Tag
- Everything after: dense data

If we erase ONLY pages 0-40 (the first 20KB) and leave the rest, the ANS firmware
might see no valid FTL header → namespace_count = 0 → blank path. The dense data
pages would be ignored since there's no FTL header pointing to them.

```bash
# Generate a partial-erase image: first 20KB erased, rest unchanged
# (requires custom programmer script — NandX could generate the pattern)
```

## Testing Priority

Recommended order based on effort vs likelihood of success:

1. **Approach 1 (true blank)** — simplest, flash and try
2. **Approach 3 (cross-vendor)** — just copy existing dumps, no modification
3. **Approach 2 (minimal FTL)** — quick generation with NandX
4. **Approach 7 (ONFI reset)** — if programmer supports raw commands
5. **Approach 4 (downgrade)** — try older IPSW
6. **Approach 5 (clog mismatch)** — requires more reverse engineering
7. **Approach 8 (partial erase)** — surgical approach
8. **Approach 6 (FPGA emulator)** — nuclear option, most work but most reward
