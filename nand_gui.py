#!/usr/bin/env python3
"""
Apple Silicon NAND Programmer GUI

Visual tool for preparing blank NAND images for MacBook repair.
Lets you pick the target device, assign chip positions, choose donor
dumps, adapt between chip types, and export ready-to-flash images.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path

# Import the core engine
from nand_tool import (
    NANDDump, KNOWN_CHIPS, CHIP_GENERATIONS, ERASED_PATTERN,
    FILE_HEADER, SCRAMBLE_KEY, get_chip_generation,
    adapt_dump, scramble, descramble, build_header_slot,
)

# ── Device / board definitions ────────────────────────────────────────────────

# Source: https://logi.wiki/index.php/MacBook_NAND_List
# Note: "capacity" = TOTAL system capacity. Each chip is typically half (2 chips)
#       or quarter (4 chips) of total.
# F2 values with '?' are unknown — need a dump to identify.

DEVICES = {
    # ── Apple Silicon M1 ──────────────────────────────────────────────────
    'A2337/A2338 (M1)': {
        'name': 'MacBook Air/Pro M1 (A2337/A2338)',
        'generation': 'gen1_kicm',
        'capacities': {
            '256GB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (128GB)', 'chip': 'KICM232', 'f2': '?'},
                    {'tag': 'UN100', 'label': 'NAND1 (128GB)', 'chip': 'KICM225', 'f2': '?'},
                ],
            },
            '256GB (Hynix)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (128GB)', 'chip': 'H23B1T82D7AEQ', 'f2': '?'},
                    {'tag': 'UN100', 'label': 'NAND1 (128GB)', 'chip': 'H23B1T82D7AEQ', 'f2': '?'},
                ],
            },
            '512GB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (256GB)', 'chip': 'KICM233', 'f2': 'f007cc44bdac94bf15111ec5bc88d006'},
                    {'tag': 'UN100', 'label': 'NAND1 (256GB)', 'chip': 'KICM227', 'f2': 'd3bc36674d8ec40531c35ffec6f04c91'},
                ],
            },
            '512GB (Hynix)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (256GB)', 'chip': 'H23B2588H7AEQ-BC', 'f2': '?'},
                    {'tag': 'UN100', 'label': 'NAND1 (256GB)', 'chip': 'H23B2T83G7AEQ-BC', 'f2': '?'},
                ],
            },
            '1TB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (512GB)', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                    {'tag': 'UN100', 'label': 'NAND1 (512GB)', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                ],
            },
            '1TB (SanDisk)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (512GB)', 'chip': 'SDREGJHIH', 'f2': '?'},
                    {'tag': 'UN100', 'label': 'NAND1 (512GB)', 'chip': 'SDREGJHIH', 'f2': '?'},
                ],
            },
            '2TB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (1TB)', 'chip': 'KICM223R', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                    {'tag': 'UN100', 'label': 'NAND1 (1TB)', 'chip': 'KICM223R', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                ],
            },
            '2TB (Hynix)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (1TB)', 'chip': 'H23B8T85K7AFJ-BC', 'f2': '?'},
                    {'tag': 'UN100', 'label': 'NAND1 (1TB)', 'chip': 'H23B8T85K7AFJ-BC', 'f2': '?'},
                ],
            },
        },
    },
    # ── Apple Silicon M1 Pro/Max ──────────────────────────────────────────
    'A2442 (M1 Pro 14")': {
        'name': 'MacBook Pro 14" M1 Pro (A2442)',
        'generation': 'gen1_kicm',
        'capacities': {
            '512GB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                    {'tag': 'UN100', 'label': 'NAND1', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                    {'tag': 'UN200', 'label': 'NAND2', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                    {'tag': 'UN300', 'label': 'NAND3', 'chip': 'KICM229', 'f2': '098816c0854210564584afd0f5c1e6c1'},
                ],
            },
            '1TB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0', 'chip': 'KICM223', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                    {'tag': 'UN100', 'label': 'NAND1', 'chip': 'KICM223', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                ],
            },
        },
    },
    'A2485 (M1 Max 16")': {
        'name': 'MacBook Pro 16" M1 Max (A2485)',
        'generation': 'gen1_kicm',
        'capacities': {
            '1TB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0', 'chip': 'KICM223', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                    {'tag': 'UN100', 'label': 'NAND1', 'chip': 'KICM223', 'f2': 'e4569cdf058135a8a80096adba963bf1'},
                ],
            },
        },
    },
    # ── Apple Silicon M2 ──────────────────────────────────────────────────
    'A2681 (M2 Air)': {
        'name': 'MacBook Air M2 (A2681)',
        'generation': 'gen1_kicm',
        'capacities': {
            '256GB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (256GB)', 'chip': 'KICM5224', 'f2': '?'},
                ],
            },
            '512GB (Kioxia)': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (256GB)', 'chip': 'KICM233', 'f2': 'f007cc44bdac94bf15111ec5bc88d006'},
                    {'tag': 'UN100', 'label': 'NAND1 (256GB)', 'chip': 'KICM227', 'f2': 'd3bc36674d8ec40531c35ffec6f04c91'},
                ],
            },
        },
    },
    # ── Apple Silicon M3 ──────────────────────────────────────────────────
    'A2901 (M3 Air)': {
        'name': 'MacBook Air M3 (A2901)',
        'generation': 'gen2_k5a',
        'capacities': {
            '256GB': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0 (256GB)', 'chip': 'K5A4', 'f2': 'ba5cb781c2ac883db41f1636aeb804d5'},
                    {'tag': 'UN100', 'label': 'NAND1 (256GB)', 'chip': 'K5A4', 'f2': 'ba5cb781c2ac883db41f1636aeb804d5'},
                ],
            },
            '512GB': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0', 'chip': 'K5A5', 'f2': 'fea75da1118971de1d3d621be63ea23e'},
                    {'tag': 'UN100', 'label': 'NAND1', 'chip': 'K5A5', 'f2': 'fea75da1118971de1d3d621be63ea23e'},
                ],
            },
            '1TB': {
                'positions': [
                    {'tag': 'UN000', 'label': 'NAND0', 'chip': 'K5A8', 'f2': '599612320ef0007cb3544dd74c99bd00'},
                    {'tag': 'UN100', 'label': 'NAND1', 'chip': 'K5A8', 'f2': '599612320ef0007cb3544dd74c99bd00'},
                ],
            },
        },
    },
    # ── Custom ────────────────────────────────────────────────────────────
    'Custom': {
        'name': 'Custom / Unknown Device',
        'generation': None,
        'capacities': {
            'Custom': {
                'positions': [],
            },
        },
    },
}


# ── GUI Application ───────────────────────────────────────────────────────────

class NANDToolGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Apple Silicon NAND Programmer")
        self.root.geometry("1050x750")
        self.root.minsize(900, 600)

        # State
        self.donor_dumps = {}       # tag -> NANDDump or filepath
        self.position_widgets = []  # list of position UI rows
        self.base_dir = str(Path(__file__).parent)

        self._build_ui()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='x')

        ttk.Label(top, text="Apple Silicon NAND Programmer",
                  font=('Helvetica', 16, 'bold')).pack(side='left')

        # Main paned
        paned = ttk.PanedWindow(self.root, orient='vertical')
        paned.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        # Upper section: config
        upper = ttk.Frame(paned)
        paned.add(upper, weight=2)

        # Lower section: log
        lower = ttk.LabelFrame(paned, text="Log", padding=4)
        paned.add(lower, weight=1)

        self.log = scrolledtext.ScrolledText(lower, height=10, font=('Courier', 10),
                                              state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True)

        # ── Device selection row ──
        dev_frame = ttk.LabelFrame(upper, text="1. Target Device", padding=8)
        dev_frame.pack(fill='x', pady=(0, 6))

        row1 = ttk.Frame(dev_frame)
        row1.pack(fill='x')

        ttk.Label(row1, text="Mac Model:").pack(side='left')
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(row1, textvariable=self.device_var,
                                          values=list(DEVICES.keys()), width=20,
                                          state='readonly')
        self.device_combo.pack(side='left', padx=(4, 16))
        self.device_combo.bind('<<ComboboxSelected>>', self._on_device_changed)

        self.device_desc = ttk.Label(row1, text="", foreground='gray')
        self.device_desc.pack(side='left')

        row2 = ttk.Frame(dev_frame)
        row2.pack(fill='x', pady=(4, 0))

        ttk.Label(row2, text="Capacity:").pack(side='left')
        self.capacity_var = tk.StringVar()
        self.capacity_combo = ttk.Combobox(row2, textvariable=self.capacity_var,
                                            width=12, state='readonly')
        self.capacity_combo.pack(side='left', padx=(4, 16))
        self.capacity_combo.bind('<<ComboboxSelected>>', self._on_capacity_changed)

        self.gen_label = ttk.Label(row2, text="", foreground='blue')
        self.gen_label.pack(side='left')

        # ── Position assignment area ──
        self.pos_frame = ttk.LabelFrame(upper, text="2. Chip Positions", padding=8)
        self.pos_frame.pack(fill='both', expand=True, pady=(0, 6))

        # Scrollable inner frame for positions
        self.pos_canvas = tk.Canvas(self.pos_frame, highlightthickness=0)
        self.pos_scrollbar = ttk.Scrollbar(self.pos_frame, orient='vertical',
                                            command=self.pos_canvas.yview)
        self.pos_inner = ttk.Frame(self.pos_canvas)

        self.pos_inner.bind('<Configure>',
                            lambda e: self.pos_canvas.configure(
                                scrollregion=self.pos_canvas.bbox('all')))
        self.pos_canvas.create_window((0, 0), window=self.pos_inner, anchor='nw')
        self.pos_canvas.configure(yscrollcommand=self.pos_scrollbar.set)

        self.pos_canvas.pack(side='left', fill='both', expand=True)
        self.pos_scrollbar.pack(side='right', fill='y')

        # ── Action buttons ──
        btn_frame = ttk.LabelFrame(upper, text="3. Actions", padding=8)
        btn_frame.pack(fill='x')

        ttk.Button(btn_frame, text="Auto-Detect Donors",
                   command=self._auto_detect).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="Analyze All",
                   command=self._analyze_all).pack(side='left', padx=4)

        ttk.Separator(btn_frame, orient='vertical').pack(side='left', fill='y', padx=8)

        ttk.Button(btn_frame, text="Export Images",
                   command=self._export, style='Accent.TButton').pack(side='left', padx=4)

        self.export_dir_var = tk.StringVar(value=os.path.join(self.base_dir, 'output'))
        ttk.Label(btn_frame, text="Output:").pack(side='left', padx=(16, 4))
        ttk.Entry(btn_frame, textvariable=self.export_dir_var,
                  width=30).pack(side='left')
        ttk.Button(btn_frame, text="...",
                   command=self._pick_export_dir, width=3).pack(side='left', padx=2)

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_device_changed(self, event=None):
        model = self.device_var.get()
        if model not in DEVICES:
            return
        dev = DEVICES[model]
        self.device_desc.config(text=dev['name'])

        caps = list(dev['capacities'].keys())
        self.capacity_combo.config(values=caps)
        if caps:
            self.capacity_var.set(caps[0])
        self._on_capacity_changed()

    def _on_capacity_changed(self, event=None):
        model = self.device_var.get()
        cap = self.capacity_var.get()
        if model not in DEVICES or cap not in DEVICES[model]['capacities']:
            return

        dev = DEVICES[model]
        gen = dev['generation'] or 'unknown'
        self.gen_label.config(text=f"Generation: {gen}")

        cap_info = dev['capacities'][cap]
        self._build_position_rows(cap_info['positions'], model, cap)

    def _build_position_rows(self, positions, model, capacity):
        """Rebuild the position assignment grid."""
        # Clear old rows
        for widget in self.pos_inner.winfo_children():
            widget.destroy()
        self.position_widgets = []

        if not positions:
            ttk.Label(self.pos_inner,
                      text="No positions defined. Use Custom mode or select a capacity.",
                      foreground='gray').grid(row=0, column=0, columnspan=6, pady=20)
            return

        # Header
        headers = ['Position', 'Chip Type', 'F2 Identifier', 'Donor File', '', 'Status']
        for col, h in enumerate(headers):
            ttk.Label(self.pos_inner, text=h, font=('Helvetica', 10, 'bold')).grid(
                row=0, column=col, padx=4, pady=(0, 6), sticky='w')

        for i, pos in enumerate(positions):
            row = i + 1
            dies = pos.get('dies', 1)
            label = pos['label']
            if dies > 1:
                label += f" [{dies} dies]"

            # Position label
            ttk.Label(self.pos_inner, text=label,
                      font=('Helvetica', 10)).grid(row=row, column=0, padx=4, sticky='w')

            # Chip type
            chip_name = pos.get('chip', '?')
            ttk.Label(self.pos_inner, text=chip_name).grid(
                row=row, column=1, padx=4, sticky='w')

            # F2
            f2 = pos.get('f2', '')
            if f2 == '?':
                f2_display = 'UNKNOWN - need dump'
                f2_color = 'red'
            else:
                f2_display = f2[:16] + '...' if len(f2) > 16 else f2
                f2_color = 'gray'
            ttk.Label(self.pos_inner, text=f2_display, foreground=f2_color,
                      font=('Courier', 9)).grid(row=row, column=2, padx=4, sticky='w')

            # Donor file entry + browse
            donor_var = tk.StringVar()
            donor_entry = ttk.Entry(self.pos_inner, textvariable=donor_var, width=35)
            donor_entry.grid(row=row, column=3, padx=4, sticky='ew')

            browse_btn = ttk.Button(self.pos_inner, text="Browse",
                                     command=lambda v=donor_var, p=pos: self._browse_donor(v, p))
            browse_btn.grid(row=row, column=4, padx=2)

            # Status
            status_var = tk.StringVar(value="--")
            status_label = ttk.Label(self.pos_inner, textvariable=status_var,
                                      foreground='gray')
            status_label.grid(row=row, column=5, padx=4, sticky='w')

            self.position_widgets.append({
                'pos': pos,
                'donor_var': donor_var,
                'status_var': status_var,
                'status_label': status_label,
                'dies': dies,
                'model': model,
                'capacity': capacity,
            })

        self.pos_inner.columnconfigure(3, weight=1)

    def _browse_donor(self, donor_var, pos):
        """Browse for a donor file or directory."""
        dies = pos.get('dies', 1)
        if dies > 1:
            # Multi-die: select directory
            path = filedialog.askdirectory(
                title=f"Select donor directory for {pos['label']} ({dies} dies)",
                initialdir=self.base_dir)
            if path:
                donor_var.set(path)
                self._validate_donor(donor_var, pos)
        else:
            # Single die: select file
            path = filedialog.askopenfilename(
                title=f"Select donor file for {pos['label']}",
                initialdir=self.base_dir,
                filetypes=[("NAND dumps", "*.bin"), ("All files", "*.*")])
            if path:
                donor_var.set(path)
                self._validate_donor(donor_var, pos)

    def _validate_donor(self, donor_var, pos):
        """Validate a donor file/dir and update status."""
        path = donor_var.get()
        widget = None
        for w in self.position_widgets:
            if w['donor_var'] is donor_var:
                widget = w
                break
        if not widget:
            return

        try:
            if os.path.isdir(path):
                bins = sorted([f for f in os.listdir(path) if f.endswith('.bin')])
                if not bins:
                    widget['status_var'].set("No .bin files!")
                    widget['status_label'].config(foreground='red')
                    return
                # Check first file
                dump = NANDDump(os.path.join(path, bins[0]))
                target_gen = get_chip_generation(pos.get('f2', ''))
                donor_gen = dump.generation

                if donor_gen == target_gen and donor_gen != 'unknown':
                    widget['status_var'].set(f"OK: {len(bins)} dies, {dump.chip_type}")
                    widget['status_label'].config(foreground='green')
                elif donor_gen != target_gen and donor_gen != 'unknown' and target_gen != 'unknown':
                    widget['status_var'].set(f"WARN: wrong gen ({donor_gen})")
                    widget['status_label'].config(foreground='orange')
                else:
                    widget['status_var'].set(f"{len(bins)} dies, {dump.chip_type}")
                    widget['status_label'].config(foreground='blue')
            else:
                dump = NANDDump(path)
                target_f2 = pos.get('f2', '')
                target_gen = get_chip_generation(target_f2)
                donor_gen = dump.generation

                if dump.f2.hex() == target_f2:
                    widget['status_var'].set(f"EXACT match: {dump.chip_type}")
                    widget['status_label'].config(foreground='green')
                elif donor_gen == target_gen and donor_gen != 'unknown':
                    widget['status_var'].set(f"OK: same gen, will adapt F2")
                    widget['status_label'].config(foreground='green')
                elif donor_gen != target_gen and donor_gen != 'unknown' and target_gen != 'unknown':
                    widget['status_var'].set(f"WRONG gen ({donor_gen})!")
                    widget['status_label'].config(foreground='red')
                else:
                    widget['status_var'].set(f"Unknown: {dump.chip_type}")
                    widget['status_label'].config(foreground='orange')
        except Exception as e:
            widget['status_var'].set(f"Error: {e}")
            widget['status_label'].config(foreground='red')

    def _auto_detect(self):
        """Auto-detect available donor dumps in the base directory."""
        self._log("Scanning for donor dumps...")

        # Build a map of F2 -> files
        f2_map = {}
        gen_map = {}  # generation -> list of (f2, path)

        for dirpath, dirnames, filenames in os.walk(self.base_dir):
            # Skip output dirs
            if 'output' in dirpath or '__MACOSX' in dirpath:
                continue
            for fname in filenames:
                if not fname.endswith('.bin'):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    dump = NANDDump(fpath)
                    f2 = dump.f2.hex()
                    if f2 not in f2_map:
                        f2_map[f2] = []
                    f2_map[f2].append(fpath)

                    gen = dump.generation
                    if gen not in gen_map:
                        gen_map[gen] = set()
                    gen_map[gen].add(f2)
                except Exception:
                    pass

        self._log(f"Found {sum(len(v) for v in f2_map.values())} dump files "
                  f"across {len(f2_map)} chip types")

        for f2, paths in f2_map.items():
            name = KNOWN_CHIPS.get(f2, f'Unknown ({f2[:16]}...)')
            gen = get_chip_generation(f2)
            self._log(f"  {name} [{gen}]: {len(paths)} files")

        # Try to assign donors to positions
        assigned = 0
        for widget in self.position_widgets:
            if widget['donor_var'].get():
                continue  # Already assigned
            target_f2 = widget['pos'].get('f2', '')
            target_gen = get_chip_generation(target_f2)
            dies_needed = widget.get('dies', 1)

            # 1. Try exact F2 match
            if target_f2 in f2_map:
                paths = f2_map[target_f2]
                if dies_needed > 1:
                    # Find directory containing these files
                    dirs = set(os.path.dirname(p) for p in paths)
                    for d in dirs:
                        bins = [f for f in os.listdir(d) if f.endswith('.bin')]
                        if len(bins) >= dies_needed:
                            widget['donor_var'].set(d)
                            self._validate_donor(widget['donor_var'], widget['pos'])
                            assigned += 1
                            break
                else:
                    widget['donor_var'].set(paths[0])
                    self._validate_donor(widget['donor_var'], widget['pos'])
                    assigned += 1
                continue

            # 2. Try same-generation match
            for f2, paths in f2_map.items():
                if get_chip_generation(f2) == target_gen:
                    if dies_needed > 1:
                        dirs = set(os.path.dirname(p) for p in paths)
                        for d in dirs:
                            bins = [f for f in os.listdir(d) if f.endswith('.bin')]
                            if len(bins) >= dies_needed:
                                widget['donor_var'].set(d)
                                self._validate_donor(widget['donor_var'], widget['pos'])
                                assigned += 1
                                break
                    else:
                        widget['donor_var'].set(paths[0])
                        self._validate_donor(widget['donor_var'], widget['pos'])
                        assigned += 1
                    break

        self._log(f"Auto-assigned {assigned} position(s)")

    def _analyze_all(self):
        """Analyze all assigned donors."""
        for widget in self.position_widgets:
            path = widget['donor_var'].get()
            if not path:
                continue
            self._log(f"\n--- {widget['pos']['label']} ---")
            try:
                if os.path.isdir(path):
                    bins = sorted([f for f in os.listdir(path) if f.endswith('.bin')])
                    for fname in bins:
                        dump = NANDDump(os.path.join(path, fname))
                        self._log(f"  {fname}: {dump.chip_type}, "
                                  f"{dump.data_size / 1024 / 1024:.0f}MB, "
                                  f"{dump.num_header_slots} hdr slots, "
                                  f"gen={dump.generation}")
                else:
                    dump = NANDDump(path)
                    self._log(f"  File: {os.path.basename(path)}")
                    self._log(f"  Chip: {dump.chip_type}")
                    self._log(f"  Gen:  {dump.generation}")
                    self._log(f"  Size: {dump.data_size / 1024 / 1024:.0f}MB")
                    self._log(f"  Hdr:  {dump.num_header_slots} slots")
                    self._log(f"  F2:   {dump.f2.hex()}")
            except Exception as e:
                self._log(f"  ERROR: {e}")

    def _export(self):
        """Export adapted images for all positions."""
        output_dir = self.export_dir_var.get()
        if not output_dir:
            messagebox.showerror("Error", "Select an output directory first.")
            return

        # Check all positions have donors
        missing = []
        for widget in self.position_widgets:
            if not widget['donor_var'].get():
                missing.append(widget['pos']['label'])
        if missing:
            messagebox.showerror("Missing donors",
                                 f"No donor assigned for: {', '.join(missing)}")
            return

        model = self.device_var.get()
        cap = self.capacity_var.get()

        os.makedirs(output_dir, exist_ok=True)
        self._log(f"\n=== EXPORTING for {model} {cap} ===")
        self._log(f"Output: {output_dir}")

        success = 0
        errors = 0

        for widget in self.position_widgets:
            pos = widget['pos']
            donor_path = widget['donor_var'].get()
            target_f2 = bytes.fromhex(pos.get('f2', ''))
            tag = pos.get('tag', 'NAND')
            chip_name = pos.get('chip', 'NAND')
            dies = widget.get('dies', 1)

            self._log(f"\nPosition: {pos['label']} (tag={tag})")

            try:
                if os.path.isdir(donor_path):
                    # Multi-die: process each file
                    bins = sorted([f for f in os.listdir(donor_path)
                                   if f.endswith('.bin')])
                    for i, fname in enumerate(bins[:dies]):
                        donor = NANDDump(os.path.join(donor_path, fname))
                        out_name = f"Model({model})_Tag({tag})_{chip_name}_{cap}_die{i + 1}.bin"
                        out_path = os.path.join(output_dir, out_name)

                        if donor.f2 == target_f2:
                            # Exact match: just copy
                            import shutil
                            shutil.copy2(os.path.join(donor_path, fname), out_path)
                            self._log(f"  Die {i + 1}: copied (exact F2 match)")
                        else:
                            adapt_dump(donor, target_f2, out_path)
                            self._log(f"  Die {i + 1}: adapted F2")
                        success += 1

                    if len(bins) < dies:
                        self._log(f"  WARNING: only {len(bins)} donor dies, "
                                  f"need {dies}. Reusing last die for remaining.")
                        last_donor = NANDDump(os.path.join(donor_path, bins[-1]))
                        for i in range(len(bins), dies):
                            out_name = f"Model({model})_Tag({tag})_{chip_name}_{cap}_die{i + 1}.bin"
                            out_path = os.path.join(output_dir, out_name)
                            adapt_dump(last_donor, target_f2, out_path)
                            self._log(f"  Die {i + 1}: adapted (reused donor)")
                            success += 1
                else:
                    # Single die
                    donor = NANDDump(donor_path)
                    out_name = f"Model({model})_Tag({tag})_{chip_name}_{cap}.bin"
                    out_path = os.path.join(output_dir, out_name)

                    if donor.f2 == target_f2:
                        import shutil
                        shutil.copy2(donor_path, out_path)
                        self._log(f"  Copied (exact F2 match)")
                    else:
                        adapt_dump(donor, target_f2, out_path)
                        self._log(f"  Adapted F2: {donor.f2.hex()[:16]}... -> {target_f2.hex()[:16]}...")
                    success += 1

            except Exception as e:
                self._log(f"  ERROR: {e}")
                errors += 1

        self._log(f"\n=== DONE: {success} files exported, {errors} errors ===")
        if errors == 0:
            self._log("Remember: DFU restore is required after flashing!")
            messagebox.showinfo("Export Complete",
                                f"{success} files exported to:\n{output_dir}\n\n"
                                f"Flash these to the NAND chip(s), then DFU restore the Mac.")
        else:
            messagebox.showwarning("Export Complete (with errors)",
                                   f"{success} exported, {errors} errors.\nCheck log for details.")

    def _pick_export_dir(self):
        path = filedialog.askdirectory(title="Select output directory",
                                        initialdir=self.base_dir)
        if path:
            self.export_dir_var.set(path)

    def _log(self, msg):
        self.log.config(state='normal')
        self.log.insert('end', msg + '\n')
        self.log.see('end')
        self.log.config(state='disabled')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()

    # Try to set a nicer theme
    style = ttk.Style()
    available = style.theme_names()
    for theme in ['clam', 'alt', 'default']:
        if theme in available:
            style.theme_use(theme)
            break

    app = NANDToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
