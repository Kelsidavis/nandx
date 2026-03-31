# NAND Dumps

Binary dump files are not tracked in git due to size.

## Where to get dumps

- dosdude1: `http://dosdude1.com/files/Mac-NAND/Apple-Silicon/`
- Extract zips into the appropriate subdirectory below

## Directory structure

```
dumps/
├── gen1_kicm/              # M1/M2 era Kioxia NAND
│   ├── KICM223/            # 1TB per chip (used in 2TB total configs)
│   │   ├── blank/          # 10-die generic blank set
│   │   └── A2348/          # MacBook Pro M1 Pro configs
│   ├── KICM227/            # 256GB per chip
│   │   └── A2337/          # MacBook Air M1 (UN100 position)
│   ├── KICM229/            # 512GB per chip (used in 1TB total configs)
│   │   ├── blank_set1/     # 8-die blank set (source chip 1)
│   │   ├── blank_set2/     # 5-die blank set (source chip 2, includes DUMP2 re-read)
│   │   └── A2442/          # MacBook Pro 14" M1 Pro (4 positions: UN000-UN300)
│   └── KICM233/            # 256GB per chip
│       └── A2337/          # MacBook Air M1 (UN000 position)
└── gen2_k5a/               # M3+ era Kioxia NAND
    ├── K5A4/               # 256GB per chip
    │   └── A2901/          # MacBook Air M3
    ├── K5A5/               # Capacity unknown
    └── K5A8/               # 1TB per chip
```

## Important

- Dumps from the same generation (gen1 or gen2) can be adapted between chip types
- Cross-generation adaptation will NOT work (different encryption constants)
- After flashing, a DFU restore is always required
