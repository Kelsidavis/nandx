#!/usr/bin/env python3
"""
NandX — Apple Silicon NAND Image Tool

Analyzes, adapts, and generates blank NAND programmer dumps for MacBook repair.
These dumps are used with NAND programmers to initialize replacement NAND chips
so the Mac can enter DFU mode and be restored.

File format (reverse-engineered):
  - 0x00-0x0F: 16-byte file/tool header (universal constant)
  - 0x10+: NAND data, XOR-scrambled with a fixed 16-byte key

Descrambled NAND data is organized as 512-byte slots:
  - Header slots (varies per chip type: 11, 21, or 41):
    - 0x000-0x00F: F1 field (page/block identifier, per-generation constant cycle)
    - 0x010-0x01F: F2 field (chip type identifier, constant per NAND type)
    - 0x020-0x02F: F3 field (per-slot authentication/nonce, deterministic per chip type)
    - 0x030-0x1EF: 0xFF padding
    - 0x1F0-0x1FF: Tag (cycling values, per-generation)
  - Dense data slots: full 512 bytes of SoC initialization data (FTL, 1TR, etc.)
"""

import os
import sys
import struct
import hashlib
import itertools
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

FILE_HEADER = bytes.fromhex('e32fb28fa62973d867e54fdc03892414')
ERASED_PATTERN = bytes.fromhex('2636ea30350b91efeeab499f988a90ee')
SCRAMBLE_KEY = bytes(b ^ 0xFF for b in ERASED_PATTERN)

# Known chip type F2 identifiers (descrambled)
KNOWN_CHIPS = {
    # Generation 1: KICM series (older Apple Silicon, M1/M2 era)
    # Master chips (NAND0) — over-provisioned with extra spare blocks
    'f007cc44bdac94bf15111ec5bc88d006': 'KICM233 (Kioxia 320GB master, used as 256GB)',
    # Slave chips (NAND1+) — standard capacity
    'd3bc36674d8ec40531c35ffec6f04c91': 'KICM227 (Kioxia 256GB slave)',
    '098816c0854210564584afd0f5c1e6c1': 'KICM229 (Kioxia 512GB)',
    'e4569cdf058135a8a80096adba963bf1': 'KICM223 (Kioxia 1TB)',
    # Generation 2: K5A series (newer Apple Silicon, M3+ era)
    'ba5cb781c2ac883db41f1636aeb804d5': 'K5A4 (Kioxia 256GB, A2901)',
    'fea75da1118971de1d3d621be63ea23e': 'K5A5 (Kioxia)',
    '599612320ef0007cb3544dd74c99bd00': 'K5A8 (Kioxia 1TB)',
}

# Chip generation grouping — chips within the same generation share F1 values
# and have partially compatible dense data. Cross-generation adapt WILL NOT work.
CHIP_GENERATIONS = {
    'gen1_kicm': [
        'e4569cdf058135a8a80096adba963bf1',  # KICM223
        '098816c0854210564584afd0f5c1e6c1',  # KICM229
        'f007cc44bdac94bf15111ec5bc88d006',  # KICM233
        'd3bc36674d8ec40531c35ffec6f04c91',  # KICM227
    ],
    'gen2_k5a': [
        'ba5cb781c2ac883db41f1636aeb804d5',  # K5A4
        'fea75da1118971de1d3d621be63ea23e',  # K5A5
        '599612320ef0007cb3544dd74c99bd00',  # K5A8
    ],
}


def get_chip_generation(f2_hex):
    """Return the generation name for a chip F2 identifier."""
    for gen, chips in CHIP_GENERATIONS.items():
        if f2_hex in chips:
            return gen
    return 'unknown'


# Per-chip-type F3 auth tag tables (descrambled).
# F3 is deterministic per chip type and slot index — confirmed identical
# across different physical chips and different SoCs using the same NAND type.
F3_TABLES = {
    'e4569cdf058135a8a80096adba963bf1': [  # KICM223 (41 slots)
        '2ca2d49d01db89aaba57fc95582b7b18', '4d9d642da3e8295f834ec93f34b4d4e2',
        '9f7271f350efe92797cc29c8c40f1fd1', '6935426a3ec9e678dbe557ce9d978915',
        '0709ed072f9ac4914444b331d9e2d406', '0a9a59f2b109a3e860fa2372b4e6b1d4',
        '9f67d0b44cb3afe6790869804eb336c0', '66834509c6c4f8cf78747dc69bbc4a6e',
        '2b94967f09ceeb0961acb91b4d62409d', 'c242b2173836d4a0eae6dd207a150a07',
        '5cf44b96272ad802e19bae31b21f14e6', 'e4f551a755dce2da790546b3fe89b175',
        '6489c715147e20423d36ed5e7f624d49', 'e99e08d5d2588512f89061e2bcf577e0',
        'a806a8f89726a1db1b50b6d09cd476b6', '307ad798dad2295ea6d9dcd2cc2d86f2',
        '0771a8504f38856e1787cd1489f86b64', '08358d916e2d8cea3d6f3b5908bf4d9b',
        '4f10a3d6979bc816fe14eaaf2cf65677', '6fc25394a4ab0c8d0f785147c3dd720a',
        '483aca32da53681fde6bfe77d245ac34', '99822534572306010eb6d17d99a94893',
        '200ef80020d033225c07921162a50d7c', 'af25e89b7f98f20c00d0f61ceb70420c',
        '9c959b602fd3204561b799176d2afa91', 'e65b32927e80121552cdb9f1a9693aaf',
        '5cf776aff0044d6d8e27d25a2b190d45', '079aa425958e280dd78a2530dabe4170',
        '0f6a58da9f4d71bdc3d22df637743388', '98c0c6f23b9dbb999fe02b66bd251131',
        'beab38c9ff8db40085c8fe2940252a88', '5c310b413a16a0146b4ad0f151c654e0',
        '6becb1351d07a3ebd815e976c4927e45', 'c82d44f3a2af3eae56441ff3764cc8c7',
        '1d73846d8b4c440ecac89e4a4473f0b3', 'aaaba56110a8804da00493ed50f4d1e7',
        'd4e3c7365270af5194fa984e686367bf', '1c224f0fcd3b0fb2f9a17e101d168d64',
        '9750745783058f288441e8ec5f99fe70', '368eff874016f6a0f3aa4827ae226f69',
        '79dd8463c832ef0d7adb6af498eb13c5',
    ],
    '098816c0854210564584afd0f5c1e6c1': [  # KICM229 (41 slots)
        '13ac25b723b0656d2ae0797ab7a05f10', 'b674c66d23d076a65629966760faaf60',
        'd943970d161cbb7f9a5c51a3ae9536c1', '5624ff941c142c5ceea35a8b0d990bb0',
        '985922e6df345e3a55f3c1254c634623', 'cd84c4cf14c7c575f5abc9afcd6cc31c',
        '8f8a5483daa61de9467c12a227ba49bd', '5f2220163abafff89d703f597ac55b19',
        '3ea683dc0f7d407dce53a1211216ff30', '4059b8656b9a4a2eb0e4e79f3ee27047',
        '92799f1dac8ebd7174687e9da4bc1faf', '8e2dc07abf506e8ed42aecef3ab3f1d4',
        '1b6112d11e33ef2bec3684872043afdc', '4753645b9e99d4d70dc90eaefc02d1b0',
        'ee7e7fe46bd8e5682bd6f50327b4368d', '9ed0d8baba7c450055debb40b7230586',
        '0669c0964105cd5a1a512afbaac6a018', '040e12b8a8b0b0aa57d2c13f6c6daa80',
        '0bdbbce9199c9074534bff6a07967014', '3bc38ae36dbc201f1a5f747b4da19a7c',
        '9e32e7d04fe9708d9f02fffd645b4e4c', '092aefebb18fc0b5ff6982f2be12bb77',
        '6b28d3c025c11d7aafa3ca29b56edd24', '9985ab6c71773846eb063417c3892e12',
        '7d7386d112f91ab13b88b88d2c23ff64', 'a1d19015fff1fbd5cbaae371c7c17050',
        '9475852ff66ac0b2267bc7beabeb4210', '1547a7df176d99310cc7e14c85b6ef1f',
        '9dcb54f156f6a9f3f990ea127ab37f8e', 'd9a40713c2b6b1abee949c4ac109d7b4',
        '4a2ed9406520f552b07b02962d85ea12', '70d69f9f689607db024798fbfc6c3106',
        'a3d144cf487d3ce6e197ae664b22ce00', '70015c5d6d84f420665c721b688a471f',
        '0b773eadb6d92c3ce7c69edf5445abd9', '1150d2a5e7fe5f8b6e61715d97d25e3f',
        'b2a0f323d03270ed6c51959afc7d93fc', 'c53df7727cebf63fb550b05922fdc4d2',
        '61cec93ff3335dceda8c25e6a2df25f8', '03f28b0ae66f42af8940fb9e75283cd5',
        'fc85730f9eeacee6361950cd4a82ba8f',
    ],
    'f007cc44bdac94bf15111ec5bc88d006': [  # KICM233 (11 slots)
        '450ad81b7abe55090d0713cef6ad47cc', '267ce876155bd5e1327af18288cccda6',
        '6bc106117f08128516325a349e2afd3b', '01407327489364a555ba6616d700270b',
        '85d527711941ea3f5cda07b04e49ea76', 'e8dace4bca289c4269ce00c48283781e',
        '6366aafbd2835b1d1dd1c4618c45dc75', 'abe6a2f3faaed9ad2cf7bc02ae08c96d',
        'be9536bcc24c370649bce46d29086a22', '7c1ceae26ab14b54f8d6bc9c343a0842',
        '2fff3b985aaaede23cb14469ab6b806b',
    ],
    'd3bc36674d8ec40531c35ffec6f04c91': [  # KICM227 (41 slots)
        '623b5bdb001268a8d9222a53f77a5862', '12ac5f779245f348667a4d8e920c75ec',
        '76a0662fba752962599b8f3d069b557d', 'adfd271e057f36081851e873dd23db4b',
        'b60536c7295629810bd01ccca1e3d9c3', 'd589d51e272774e71e9c023ed96c59a0',
        '0d714065d179077ed8fc2cff1d9a6116', '76b37d60deeebdb3a6d06ac84930204e',
        'cda78fd72532c26f7560032d99242d7d', '3e9107a77dc42c9bacde4b41ad028fa9',
        'eced9a4e9fdfc209d4ee7e0fefb02040', '2ca2cdcbd0a8e0ce86a59fe6a20698e0',
        '021bc970a532cf53d8b7c036bf67954d', '876646364a9372a074863e3b324f7136',
        'b5e9cdabc096a2d393d8858795bbef91', '7f080a1b3233526cd14f1b6a0b4f8971',
        '0dd8298671ad48d8765c16280cdb111a', '53248dd28fa5847605bf23766323f10b',
        'dab90268287301523cdf1e8033943202', 'b2cdc505c8b40cf4e8af3f31d67e5e5b',
        '0a8879bb8e536586381f15674642b1f8', 'f4cd8b585bd33d58ad9d9fabda529284',
        '48d4dbe67af3a993fe34ca5291f2be4f', 'e7593b8e6b8f9bed6d45544dbd4fd6c7',
        '969a88ae96880d879ef103f752d2ac4d', '9b2fb6674d654c951bc70ad9590c1402',
        'dcbf03abc7bfc0fbd5dcc9a29227b524', '40e2f42f8785b13d002851e4f39d15b8',
        '4d814e6ac5c8c1f0fe892201439ec060', '6271d4ba6d5d29280888fabd829b2ae5',
        'c37e5843f4f1eb583294a727855fb73c', 'ac9a9b341c5f17f1976de99c9e59f57e',
        '9b020696b24b75d1c5f298d62fe991ff', 'f9e0db0de45c92ef4fee35ae6e26ad6a',
        '0902be4c98694fd766e33ed5b40ae452', 'd21163308ac39bf247a2d1335d483603',
        '4e6d5a468b6bc63bc91cdd5f80cd0d10', 'e15987ab83a3c4c869435785cc90e786',
        '08076d1eb9133baed106dfa415c05db3', '62f52eb4f71660678b22d294c75e230b',
        '4096284eededc7b4c7426ec2ebe3b358',
    ],
    'ba5cb781c2ac883db41f1636aeb804d5': [  # K5A4 (21 slots)
        '60c2501995a3972a8d9468ad1826ada0', '17a10fbc423d1337f64b9368a754a957',
        'a458cfffd73c480d709f828048b934e1', '7411f9a42b261bcc3c9f464a5437f770',
        '01e91c35f9009b78b6fdf626b7e049d3', '1efeb7d46a45e07898214bd48eaa7a07',
        '5664fdcd7ccbfb334130f925ddb027be', '3cf33501917c4d589daf532adba692a8',
        '82872c369406f10888246377578bafff', '6d484e8bf747958cb446bda715ddb3c0',
        '23301cde6df5690a4d1cc45ec39f404c', 'e202a9a1756413eeb7860d1a84675803',
        '1acd9b77d848f0e363913faf48622d7c', 'b1d385930e217f898a7b0d05b2eac789',
        'e738f5ce7771c8f6e767af8cc3463fa2', '176111ded59b41a244a84a2dfc735d49',
        'e917002e78b638550eb0370700e174ff', 'b2c6c3e33d07d5d3172bf86112249401',
        '68033dd45a8d094c260f22e9391e7f94', '3078d57be0c4491d96e53f6943835e27',
        'd432589eb415fe29b47dc77732441230',
    ],
    'fea75da1118971de1d3d621be63ea23e': [  # K5A5 (41 slots)
        '6690eb19b06b8a68f521ed275e5e2bcd', '372b970d41176c7a726e361a37d5a8e3',
        '956113dc912df37ea8599ede2b12b81f', '56c7c0f9b54b6776c2a323c7a6e36988',
        'f24bf1118dd33ffd4e73eeddfa88ef87', 'a6df189b5ecec5629d67f64448caee2c',
        '2e8b401f57302354262b7009f2501818', 'feca6d7cde469c56dcee1d4de88d8223',
        '94f37acd01161d28f5476b7417081e10', '1f33c53afbf31a78ac015fa447ec6db8',
        '9dba2abb466f1bea7f50986f56a0ce2b', 'acf941bc7d6e7c4ac5b6ba82af51a117',
        '4884839e35266c57cf270d37df64acc9', '8b6984d8d567bb1561e797f240c825dd',
        'd5edf3459dac3d0eab1c4125108ddb8b', '6ef1fa23a9e21c1db9d9b46aefb2e60e',
        'e478140508180197915e65f092e5c67f', '0fff4b3c4c485ccf135323763bbcaafb',
        '967e0321edce08b38c1bcc367475cf47', '955d49bb6102db976eafc63289bef9b1',
        '1314b184cac0643852472ae7c8e3930f', 'c21559795358cceff6303f174b0f3db4',
        '39e7d81c8bf2903a5691c6c02bd4edda', '8aaff48d388a1ce225ab27383cf06b85',
        '33bc05213819d7bfdcecc7dfdf434785', 'f1c37f225d78e1ef1766b0ce83a500ba',
        '987e92a29db95a0b9eb21e4959889e70', '9ae704c8eb7e6cdf13dc937e58d684ab',
        '1b453b2e1a389849dac77d8c877bb1d7', '01b5b83313256c68baf7a2447f28dc36',
        'eed1f4a928c49462cce897eb3f4c535c', 'ccf75f971bf31621192a61a18bda40e5',
        '01d60871291dc25880ea5eed4f896985', '7a4af01f2126da7c7f95115465cca62a',
        'f2ce408c0a0e7a6b6629e001153eb84b', '9e35d103104b3351787c68724dc563f0',
        'a5baa46a6b77479611a7236a0e15102c', '16d373946c47d08cd35e1980358225c9',
        '24647a9bf04445c1f1717341a201cd58', 'b3c4c01195c7fa183b748163523dfd55',
        '0fe69ae54b141bdd337b680730dd53ef',
    ],
    '599612320ef0007cb3544dd74c99bd00': [  # K5A8 (41 slots)
        'afa0f53f8e239e681278a7e72919a84c', 'b823a27440e0f58f18654f84b2cbbeb3',
        '53fb74f8681c7c06066f1d5f75fbfd4e', '97412119a43289a08b1e8434b562b5af',
        '9e2ee5617e3b399e22aa929c10e19ee1', 'a85efce38801426650f2d821690ee7d2',
        '9ac565068324c40afcb8ee23238fd6c0', '9cdfe2d031ee33ec7ec561baddaff1fc',
        '4b9c42284472b42eab19c78b2a8e19b0', '6aaf3f044e8e96b7a62245ac9b29344d',
        '87e51dca3a64115240d96a5ecf3a1299', 'f539effb3fc198118bf3a5b0a780a492',
        'c3ef2a433cc16f37c686c53a95febdfe', 'b4fd200ef5b7919e3c9d83d922fbe0fb',
        '655acb92572f9a4efdc2ae27a7fce8e2', 'fd3caf1fe1440c6ca9b4b698db2c3106',
        'ec3180853e81a3e42fbe2fa39a721947', '115189d8df2073731ca9c331e04d932d',
        '809e1b601297597f549e0768965c22c2', '821d28d493fe291dac44d0ce0c8e2878',
        '74583d6f22a8f9203c7d0c7811fbb3f0', 'e6f95ec6c4c08e34e72f7c75b3fd5709',
        '124f43f867852d2f11f2b8a3cb165cf5', 'c4e247c71433028cf49d5acc014034df',
        '4420fc18f4ef7c0c29e4b89a7bc4aa37', 'bc0b728b3d6e3a250ff0a1955aceed17',
        '7444028db18e6d9d29035252078d95e7', '478823df186288405ec618f3e4fb7c8a',
        'bc4c238bc55b573a43f9cc1100752463', '3565c1f4bd587a89feba75105690b568',
        '101a6400d6dfbdb0352231c0b6aeab8f', '3ab9aa6886ce16254996e15d99469bf5',
        '3d96bfb64f12841a448084d6b3ef6b13', '625ac33a63af12f8942db504d5026b61',
        '0ca456e07c75dd3748f26a0523004724', '38278d964ed39e95bbdb193c070d5970',
        'e87f4d8cd8f0f908a210bab6bc6fc57a', 'e71108adb23a4eb9a84dafad0908b212',
        'd9f3899f8bbc0588424222ad122c4a73', '34149483ac8f06cbc87da6a2c958cb8f',
        '74656ff92e0e00091ffbf85131597f35',
    ],
}

# Per-generation F1 and Tag cycle values (descrambled).
# Extracted from actual dumps — these differ between gen1 and gen2.
GEN_CONSTANTS = {
    'gen1_kicm': {
        'f1': [
            bytes.fromhex('f829f35e3cedaaf8c2e188cf1f8da80c'),
            bytes.fromhex('c0ef6437d580e5092e22d437cce1c59d'),
            bytes.fromhex('b2414f31089004514ab6dfc3ddb6590b'),
            bytes.fromhex('5aa35d3de5a5b3ed7f90f04a5320a91d'),
            bytes.fromhex('26d65d98e900b57e5938e2e89b36dac8'),
            bytes.fromhex('832df14e02afe17937b5244a0ab3b30b'),
            bytes.fromhex('58ab45c7e151015a5e54ca78bdd5ce60'),
            bytes.fromhex('f9c854781921c1c9451445d7d5bf2dc0'),
        ],
        'tags': [
            bytes.fromhex('c0ef6437d580e5092e22d437cce1c59d'),
            bytes.fromhex('b2717c3108a46451a2aedc2bfdb25319'),
            bytes.fromhex('5ab36e3de591d3ed9792f34c727aa307'),
            bytes.fromhex('26c65e98f900357ea1207b47833cd0c8'),
            bytes.fromhex('832d934e120fbb79d1b72da402b9f90b'),
            bytes.fromhex('58abcdc771f18a715a7c89dcbdd5de60'),
            bytes.fromhex('f9c85d783e2b60c9bd76c6d9fdbf7dd0'),
            bytes.fromhex('3ae6a7406cdd1dc876b1f9bc64fc4b05'),
        ],
    },
    'gen2_k5a': {
        'f1': [
            bytes.fromhex('6fb7aecf0e5fc6e10688cf254c132dc8'),
            bytes.fromhex('0708032a628e175a77c470d419b3bd62'),
            bytes.fromhex('351e617c228d35e15383418bb9ca451d'),
            bytes.fromhex('0ad13543ee2ad6d01a6cf365658979e6'),
            bytes.fromhex('3631d2b37c4c1242b1e10eac62729057'),
            bytes.fromhex('fcd3e1ba7099e581acc5c8ef30ce930c'),
        ],
        'tags': [
            bytes.fromhex('c0ef6437d580e5092e22d437cce1c59d'),
            bytes.fromhex('b2717c3108a46451a2aedc2bfdb25319'),
            bytes.fromhex('5ab36e3de591d3ed9792f34c727aa307'),
            bytes.fromhex('26c65e98f900357ea1207b47833cd0c8'),
            bytes.fromhex('832d934e120fbb79d1b72da402b9f90b'),
            bytes.fromhex('58abcdc771f18a715a7c89dcbdd5de60'),
            bytes.fromhex('f9c85d783e2b60c9bd76c6d9fdbf7dd0'),
            bytes.fromhex('3ae6a7406cdd1dc876b1f9bc64fc4b05'),
            bytes.fromhex('44da6d6dc3ee23281e6be504d052b6a0'),
            bytes.fromhex('6fe76007fa6400d603c36a359c1c308b'),
        ],
    },
}


def get_gen_constants(f2_hex):
    """Get the F1/Tag cycle constants for a chip's generation."""
    gen = get_chip_generation(f2_hex)
    return GEN_CONSTANTS.get(gen)


def get_header_slot_count(f2_hex):
    """Get expected header slot count for a chip type from its F3 table."""
    table = F3_TABLES.get(f2_hex)
    if table:
        return len(table)
    return 41  # default


# ── Scramble/Descramble (fast) ────────────────────────────────────────────────

def _make_key_tile(length: int) -> bytes:
    """Tile the 16-byte scramble key to match a given data length."""
    reps = (length + 15) // 16
    return (SCRAMBLE_KEY * reps)[:length]


def scramble(data: bytes) -> bytes:
    """Apply or remove NAND XOR scrambling (same operation both ways)."""
    key = _make_key_tile(len(data))
    return bytes(a ^ b for a, b in zip(data, key))


descramble = scramble  # XOR is its own inverse

try:
    import numpy as np

    def scramble(data: bytes) -> bytes:
        """Fast NumPy XOR scramble/descramble."""
        arr = np.frombuffer(data, dtype=np.uint8)
        key = np.frombuffer(_make_key_tile(len(data)), dtype=np.uint8)
        return bytes(arr ^ key)

    descramble = scramble
except ImportError:
    pass  # fall back to pure-Python version above


# ── Dump Parsing ──────────────────────────────────────────────────────────────

class NANDDump:
    """Parsed representation of a NAND programmer dump file."""

    def __init__(self, filepath, strict=True):
        self.filepath = Path(filepath)
        with open(filepath, 'rb') as f:
            self.raw = f.read()

        if len(self.raw) < 0x50:
            raise ValueError(f"File too small: {len(self.raw)} bytes")

        self.file_header = self.raw[0x00:0x10]
        if strict and self.file_header != FILE_HEADER:
            raise ValueError(f"Invalid file header: {self.file_header.hex()}")

        self.nand_data = self.raw[0x10:]
        self.descrambled = descramble(self.nand_data)
        self.data_size = len(self.nand_data)
        self.num_slots = self.data_size // 0x200

        # Extract chip type (F2)
        self.f2 = self.descrambled[0x10:0x20]
        self.chip_type = KNOWN_CHIPS.get(self.f2.hex(), f'Unknown ({self.f2.hex()})')
        self.generation = get_chip_generation(self.f2.hex())

        # Find header/dense boundary
        self.num_header_slots = self._find_header_boundary()

        # Parse header slots
        self.header_records = self._parse_header()

    def _find_header_boundary(self):
        """Find where header slots end and dense data begins."""
        # Use known count if F2 matches
        known_count = get_header_slot_count(self.f2.hex())
        if self.f2.hex() in F3_TABLES and known_count <= self.num_slots:
            # Verify the known count is plausible
            if known_count < self.num_slots:
                offset = known_count * 0x200
                middle = self.descrambled[offset + 0x30:offset + 0x1F0]
                if not all(b == 0xFF for b in middle):
                    return known_count  # confirmed: dense data starts here
            return known_count

        # Heuristic fallback
        for slot_idx in range(min(100, self.num_slots)):
            offset = slot_idx * 0x200
            middle = self.descrambled[offset + 0x30:offset + 0x1F0]
            if not all(b == 0xFF for b in middle):
                return slot_idx
        return min(100, self.num_slots)

    def _parse_header(self):
        """Parse header slot records."""
        records = []
        for slot_idx in range(self.num_header_slots):
            offset = slot_idx * 0x200
            slot = self.descrambled[offset:offset + 0x200]
            records.append({
                'slot': slot_idx,
                'f1': slot[0x000:0x010],
                'f2': slot[0x010:0x020],
                'f3': slot[0x020:0x030],
                'tag': slot[0x1F0:0x200],
            })
        return records

    def get_dense_data(self):
        """Get the dense data region (scrambled, as stored in file)."""
        start = 0x10 + self.num_header_slots * 0x200
        return self.raw[start:]

    def get_dense_data_descrambled(self):
        """Get descrambled dense data."""
        start = self.num_header_slots * 0x200
        return self.descrambled[start:]

    def info(self):
        """Print summary info about this dump."""
        print(f"File:            {self.filepath.name}")
        print(f"Size:            {len(self.raw)} bytes "
              f"({self.data_size / 1024 / 1024:.1f} MB NAND data)")
        print(f"Chip type:       {self.chip_type}")
        print(f"Generation:      {self.generation}")
        print(f"F2 identifier:   {self.f2.hex()}")
        print(f"Header slots:    {self.num_header_slots}")
        print(f"Dense data at:   slot {self.num_header_slots} "
              f"(offset 0x{self.num_header_slots * 0x200:X})")
        print(f"Total slots:     {self.num_slots}")

        # Count erased vs non-erased in dense region
        erased_slots = 0
        data_slots = 0
        for i in range(self.num_header_slots, self.num_slots):
            offset = i * 0x200
            slot = self.nand_data[offset:offset + 0x200]
            if slot == ERASED_PATTERN * (0x200 // 16):
                erased_slots += 1
            else:
                data_slots += 1

        total_dense = erased_slots + data_slots
        if total_dense > 0:
            print(f"Dense data:      {data_slots} data slots, "
                  f"{erased_slots} erased slots "
                  f"({data_slots / total_dense * 100:.1f}% utilized)")

        print(f"Raw 0x20-0x2F:   {self.raw[0x20:0x30].hex()}")
        print(f"Raw 0x30-0x3F:   {self.raw[0x30:0x40].hex()}")

    def header_detail(self):
        """Print detailed header slot analysis."""
        print(f"\n{'Slot':>4}  {'F1':^34}  {'F2':^34}  {'F3':^34}  {'Tag':^34}")
        print("-" * 150)
        for rec in self.header_records:
            print(f"{rec['slot']:4d}  {rec['f1'].hex()}  {rec['f2'].hex()}  "
                  f"{rec['f3'].hex()}  {rec['tag'].hex()}")


# ── Dump Scanning (for wiped/corrupted chips) ────────────────────────────────

def scan_dump(filepath):
    """
    Scan a potentially wiped or corrupted dump file.
    Tries to identify chip type and extract any surviving header data,
    even from files that don't have a valid programmer tool header.
    """
    with open(filepath, 'rb') as f:
        raw = f.read()

    size = len(raw)
    name = os.path.basename(filepath)
    print(f"Scanning: {name} ({size} bytes, {size / 1024 / 1024:.1f} MB)")

    # Check for valid file header
    has_header = raw[0x00:0x10] == FILE_HEADER
    print(f"  Tool header:     {'VALID' if has_header else 'MISSING/INVALID'}")

    # Try parsing as a valid dump first
    if has_header:
        try:
            dump = NANDDump(filepath)
            print(f"  Chip type:       {dump.chip_type}")
            print(f"  Generation:      {dump.generation}")
            print(f"  F2:              {dump.f2.hex()}")
            print(f"  Header slots:    {dump.num_header_slots}")
            print(f"  Status:          VALID DUMP")
            return dump
        except Exception as e:
            print(f"  Parse error:     {e}")

    # Count erased vs data bytes
    erased_16 = ERASED_PATTERN
    ff_16 = b'\xFF' * 16
    zero_16 = b'\x00' * 16
    erased_count = 0
    ff_count = 0
    zero_count = 0
    data_count = 0
    total_blocks = size // 16

    for offset in range(0, size - 15, 16):
        block = raw[offset:offset + 16]
        if block == erased_16:
            erased_count += 1
        elif block == ff_16:
            ff_count += 1
        elif block == zero_16:
            zero_count += 1
        else:
            data_count += 1

    print(f"  Erased (scrambled 0xFF): {erased_count}/{total_blocks} "
          f"({erased_count / total_blocks * 100:.1f}%)")
    print(f"  Raw 0xFF blocks:        {ff_count}/{total_blocks} "
          f"({ff_count / total_blocks * 100:.1f}%)")
    print(f"  Raw 0x00 blocks:        {zero_count}/{total_blocks} "
          f"({zero_count / total_blocks * 100:.1f}%)")
    print(f"  Data blocks:            {data_count}/{total_blocks} "
          f"({data_count / total_blocks * 100:.1f}%)")

    if erased_count + ff_count + zero_count == total_blocks:
        print(f"  Status:          COMPLETELY WIPED (no recoverable data)")
        if ff_count > erased_count:
            print(f"  Note:            Raw 0xFF dominant — chip was erased by programmer")
            print(f"                   (not crypto-wiped by SoC)")
        elif erased_count > ff_count:
            print(f"  Note:            Scrambled-erased dominant — this may be a valid")
            print(f"                   blank with no dense data, or a partial read")
        return None

    # Search for known F2 values in both scrambled and descrambled forms
    print(f"\n  Searching for known chip identifiers...")
    data_start = 0x10 if has_header else 0
    desc = descramble(raw[data_start:]) if data_count > 0 else b''

    found_f2 = set()
    for f2_hex, chip_name in KNOWN_CHIPS.items():
        f2_bytes = bytes.fromhex(f2_hex)
        # Search in descrambled data
        if f2_bytes in desc:
            pos = desc.index(f2_bytes)
            found_f2.add(f2_hex)
            print(f"  FOUND: {chip_name} at descrambled offset 0x{pos:X}")
        # Search in raw data (in case header wasn't descrambled)
        f2_scrambled = scramble(f2_bytes)
        if f2_scrambled in raw:
            pos = raw.index(f2_scrambled)
            found_f2.add(f2_hex)
            print(f"  FOUND: {chip_name} at raw offset 0x{pos:X} (scrambled)")

    if not found_f2:
        # Try to extract potential F2 from slot 0 position
        for offset in [0x10, 0x20, 0x00]:
            candidate_desc = descramble(raw[offset:offset + 0x200])
            f2_cand = candidate_desc[0x10:0x20]
            if any(b != 0xFF and b != 0x00 for b in f2_cand):
                print(f"  Potential F2 at offset 0x{offset:X}: {f2_cand.hex()}")
                gen = get_chip_generation(f2_cand.hex())
                known = KNOWN_CHIPS.get(f2_cand.hex())
                if known:
                    print(f"    -> {known} [{gen}]")
                elif gen != 'unknown':
                    print(f"    -> Unknown chip, generation {gen}")
                else:
                    print(f"    -> Not in database (new chip type?)")
                found_f2.add(f2_cand.hex())

    if found_f2:
        print(f"\n  Status:          PARTIALLY READABLE")
        print(f"  Identified as:   {', '.join(KNOWN_CHIPS.get(f, f[:16]+'...') for f in found_f2)}")
    else:
        print(f"\n  Status:          UNIDENTIFIABLE")
        print(f"  Try: python3 nand_tool.py register-chip {filepath} 'ChipName'")

    return None


# ── Dump Generation ───────────────────────────────────────────────────────────

def build_header_slot(f1: bytes, f2: bytes, f3: bytes, tag: bytes) -> bytes:
    """Build a single 512-byte header slot (descrambled)."""
    slot = bytearray(b'\xFF' * 0x200)
    slot[0x000:0x010] = f1
    slot[0x010:0x020] = f2
    slot[0x020:0x030] = f3
    slot[0x1F0:0x200] = tag
    return bytes(slot)


def adapt_dump(donor: NANDDump, target_f2: bytes, output_path: str):
    """
    Adapt a donor dump for a different NAND type by replacing F2 and F3 fields.

    Uses the correct F3 auth tags for the target chip type when available.
    Dense data is kept from the donor unchanged.
    """
    target_name = KNOWN_CHIPS.get(target_f2.hex(), 'Unknown')
    donor_gen = get_chip_generation(donor.f2.hex())
    target_gen = get_chip_generation(target_f2.hex())

    print(f"Adapting {donor.filepath.name} -> {target_name}")
    print(f"  Donor F2:  {donor.f2.hex()} ({donor.chip_type})")
    print(f"  Target F2: {target_f2.hex()} ({target_name})")
    print(f"  Donor gen: {donor_gen}, Target gen: {target_gen}")

    if donor_gen != target_gen and donor_gen != 'unknown' and target_gen != 'unknown':
        print(f"  !! WARNING: Cross-generation adapt ({donor_gen} -> {target_gen})")
        print(f"  !! F1 values differ between generations — this will almost certainly FAIL.")
        print(f"  !! Use a donor from the same generation ({target_gen}) instead.")
    elif donor_gen == target_gen and donor_gen != 'unknown':
        print(f"  OK: Same generation ({donor_gen}) — F1 values compatible.")

    # Look up correct F3 table for target type
    target_f3_table = F3_TABLES.get(target_f2.hex())
    if target_f3_table:
        print(f"  F3 table: found ({len(target_f3_table)} entries) — will set correct auth tags")
    else:
        print(f"  F3 table: NOT FOUND — keeping donor F3 tags (may not work)")

    # Build new NAND data
    new_nand = bytearray(donor.nand_data)

    # Descramble, modify F2 (and F3 if known), re-scramble for each header slot
    for slot_idx in range(donor.num_header_slots):
        offset = slot_idx * 0x200
        slot = bytearray(descramble(bytes(new_nand[offset:offset + 0x200])))
        slot[0x010:0x020] = target_f2
        if target_f3_table and slot_idx < len(target_f3_table):
            slot[0x020:0x030] = bytes.fromhex(target_f3_table[slot_idx])
        new_nand[offset:offset + 0x200] = scramble(bytes(slot))

    output = FILE_HEADER + bytes(new_nand)
    with open(output_path, 'wb') as f:
        f.write(output)

    print(f"  Written: {output_path} ({len(output)} bytes)")
    return output_path


def generate_erased_dump(target_f2: bytes, num_dies: int, die_sizes_mb: list,
                         output_dir: str, chip_name: str = "CUSTOM"):
    """
    Generate a minimal "all-erased" dump set with only the header table populated.
    Uses the correct F1/Tag cycle for the chip's generation and real F3 tables
    when available.
    """
    os.makedirs(output_dir, exist_ok=True)
    f2_hex = target_f2.hex()
    target_name = KNOWN_CHIPS.get(f2_hex, chip_name)
    gen = get_chip_generation(f2_hex)

    # Get correct generation constants
    gen_consts = get_gen_constants(f2_hex)
    if gen_consts:
        f1_cycle = gen_consts['f1']
        tag_cycle = gen_consts['tags']
        print(f"  Using {gen} F1/Tag constants")
    else:
        # Fallback to gen1 values with warning
        f1_cycle = GEN_CONSTANTS['gen1_kicm']['f1']
        tag_cycle = GEN_CONSTANTS['gen1_kicm']['tags']
        print(f"  WARNING: Unknown generation for {f2_hex}, using gen1 defaults")

    # Get header slot count and F3 table
    num_header = get_header_slot_count(f2_hex)
    f3_table = F3_TABLES.get(f2_hex)

    print(f"Generating minimal erased dumps for: {target_name}")
    print(f"  Dies: {num_dies}, sizes: {die_sizes_mb} MB, header slots: {num_header}")
    if f3_table:
        print(f"  F3 table: found ({len(f3_table)} entries) — using real auth tags")
    else:
        print(f"  F3 table: NOT FOUND — using hash placeholders")

    for die_idx in range(num_dies):
        die_size = die_sizes_mb[die_idx % len(die_sizes_mb)]
        total_nand_bytes = die_size * 1024 * 1024

        # Build descrambled NAND image (all 0xFF = erased)
        nand_desc = bytearray(b'\xFF' * total_nand_bytes)

        # Populate header slots
        for slot_idx in range(num_header):
            offset = slot_idx * 0x200
            if offset + 0x200 > total_nand_bytes:
                break

            f1 = f1_cycle[slot_idx % len(f1_cycle)]
            f2 = target_f2

            if f3_table and slot_idx < len(f3_table):
                f3 = bytes.fromhex(f3_table[slot_idx])
            else:
                f3_seed = f2 + struct.pack('<I', slot_idx) + struct.pack('<I', die_idx)
                f3 = hashlib.sha256(f3_seed).digest()[:16]

            tag = tag_cycle[slot_idx % len(tag_cycle)]
            nand_desc[offset:offset + 0x200] = build_header_slot(f1, f2, f3, tag)

        nand_scrambled = scramble(bytes(nand_desc))
        output = FILE_HEADER + nand_scrambled
        fname = f"{chip_name}_BLANK_{die_idx + 1}.bin"
        fpath = os.path.join(output_dir, fname)

        with open(fpath, 'wb') as f:
            f.write(output)

        print(f"  Die {die_idx + 1}: {fpath} ({len(output)} bytes)")


def clone_with_new_f2(donor_path: str, target_f2: bytes, output_dir: str,
                      chip_name: str = "CUSTOM"):
    """
    Clone die files from a donor (file or directory), replacing the F2/F3 fields.
    Accepts either a single .bin file or a directory of .bin files.
    """
    os.makedirs(output_dir, exist_ok=True)
    target_name = KNOWN_CHIPS.get(target_f2.hex(), chip_name)

    print(f"Cloning from {donor_path} -> {target_name}")
    print(f"  Target F2: {target_f2.hex()}")

    if os.path.isfile(donor_path):
        donor = NANDDump(donor_path)
        out_name = f"{chip_name}_BLANK_1.bin"
        out_path = os.path.join(output_dir, out_name)
        adapt_dump(donor, target_f2, out_path)
        print(f"\nDone. 1 file written to {output_dir}")
    else:
        bin_files = sorted([f for f in os.listdir(donor_path) if f.endswith('.bin')])
        for i, fname in enumerate(bin_files):
            donor = NANDDump(os.path.join(donor_path, fname))
            out_name = f"{chip_name}_BLANK_{i + 1}.bin"
            out_path = os.path.join(output_dir, out_name)
            adapt_dump(donor, target_f2, out_path)
        print(f"\nDone. {len(bin_files)} die files written to {output_dir}")


def compare_dumps(dump1: NANDDump, dump2: NANDDump):
    """Compare two dumps in detail."""
    print(f"\n{'Field':<20} {'Dump 1':^36} {'Dump 2':^36} {'Match':>5}")
    print("-" * 100)

    for name, v1, v2 in [
        ("File header", dump1.file_header.hex(), dump2.file_header.hex()),
        ("Chip type", dump1.f2.hex(), dump2.f2.hex()),
        ("Data size", f"{dump1.data_size}", f"{dump2.data_size}"),
        ("Header slots", f"{dump1.num_header_slots}", f"{dump2.num_header_slots}"),
        ("Generation", dump1.generation, dump2.generation),
    ]:
        match = "YES" if v1 == v2 else "NO"
        print(f"{name:<20} {v1:^36} {v2:^36} {match:>5}")

    # Compare header records
    print(f"\nHeader record comparison:")
    min_headers = min(dump1.num_header_slots, dump2.num_header_slots)
    f1_match = f2_match = f3_match = tag_match = 0
    for i in range(min_headers):
        r1 = dump1.header_records[i]
        r2 = dump2.header_records[i]
        f1_match += r1['f1'] == r2['f1']
        f2_match += r1['f2'] == r2['f2']
        f3_match += r1['f3'] == r2['f3']
        tag_match += r1['tag'] == r2['tag']

    print(f"  F1 matches: {f1_match}/{min_headers} (page IDs)")
    print(f"  F2 matches: {f2_match}/{min_headers} (chip type)")
    print(f"  F3 matches: {f3_match}/{min_headers} (auth tags)")
    print(f"  Tag matches: {tag_match}/{min_headers} (cycling tags)")

    # Compare dense data
    min_len = min(dump1.data_size, dump2.data_size)
    dense_start = max(dump1.num_header_slots, dump2.num_header_slots) * 0x200
    same = diff = both_erased = 0
    for offset in range(dense_start, min_len, 16):
        b1 = dump1.nand_data[offset:offset + 16]
        b2 = dump2.nand_data[offset:offset + 16]
        if b1 == ERASED_PATTERN and b2 == ERASED_PATTERN:
            both_erased += 1
        elif b1 == b2:
            same += 1
        else:
            diff += 1

    total = same + diff + both_erased
    if total > 0:
        print(f"\n  Dense data ({min_len - dense_start} bytes):")
        print(f"    Identical:    {same} blocks ({same / total * 100:.2f}%)")
        print(f"    Different:    {diff} blocks ({diff / total * 100:.2f}%)")
        print(f"    Both erased:  {both_erased} blocks ({both_erased / total * 100:.2f}%)")



def generate_true_blank(size_mb: int, output_path: str):
    """
    Generate a dump file that represents a truly blank/erased NAND.
    Every page is the erased pattern (scrambled 0xFF).
    The ANS firmware should see namespace_count = 0 → "Blank NAND".
    """
    total = size_mb * 1024 * 1024
    # Erased NAND = repeating ERASED_PATTERN (scrambled 0xFF)
    erased_page = ERASED_PATTERN * (0x200 // 16)
    num_slots = total // 0x200
    nand_data = erased_page * num_slots

    output = FILE_HEADER + nand_data
    with open(output_path, 'wb') as f:
        f.write(output)
    print(f"Generated true-blank image: {output_path} ({len(output)} bytes)")
    print(f"  All pages = erased pattern (scrambled 0xFF)")
    print(f"  ANS firmware should see: namespace_count = 0 → blank")


def generate_minimal_ftl(f2_hex: str, size_mb: int, output_path: str):
    """
    Generate a dump with ONLY the minimum FTL header (header slots with
    correct F2/F3) and everything else erased. This is the smallest
    possible image that should make the ANS firmware see a valid non-blank
    NAND with namespace metadata.
    """
    f3_table = F3_TABLES.get(f2_hex)
    gen_consts = get_gen_constants(f2_hex)
    chip_name = KNOWN_CHIPS.get(f2_hex, f'Unknown ({f2_hex[:16]}...)')

    if not f3_table:
        print(f"ERROR: No F3 table for {f2_hex}. Need a dump to register this chip type.")
        return
    if not gen_consts:
        print(f"ERROR: No generation constants for {f2_hex}.")
        return

    num_header = len(f3_table)
    total = size_mb * 1024 * 1024
    target_f2 = bytes.fromhex(f2_hex)

    print(f"Generating minimal FTL header for: {chip_name}")
    print(f"  F2: {f2_hex}")
    print(f"  Header slots: {num_header} ({num_header * 512} bytes)")
    print(f"  Total image: {size_mb} MB")

    # Build descrambled image: header slots + all-0xFF (erased)
    nand_desc = bytearray(b'\xFF' * total)

    f1_cycle = gen_consts['f1']
    tag_cycle = gen_consts['tags']

    for slot_idx in range(num_header):
        offset = slot_idx * 0x200
        f1 = f1_cycle[slot_idx % len(f1_cycle)]
        f2 = target_f2
        f3 = bytes.fromhex(f3_table[slot_idx])
        tag = tag_cycle[slot_idx % len(tag_cycle)]
        nand_desc[offset:offset + 0x200] = build_header_slot(f1, f2, f3, tag)

    nand_scrambled = scramble(bytes(nand_desc))
    output = FILE_HEADER + nand_scrambled

    with open(output_path, 'wb') as f:
        f.write(output)
    print(f"  Written: {output_path} ({len(output)} bytes)")
    print(f"  Header: {num_header} slots with valid F2+F3")
    print(f"  Dense data: all erased")
    print(f"  ANS firmware should see: namespace_count > 0 → non-blank → load FTL")


# ── H7 Format Conversion ─────────────────────────────────────────────────────

H7_PAGE_SIZE = 18336       # 16384 data + 1952 spare
H7_DATA_PER_PAGE = 16384
H7_SPARE_PER_PAGE = 1952
H7_PAGES_PER_BLOCK = 384


def convert_std_to_h7(std_path: str, blank_h7_path: str, output_path: str):
    """
    Convert a standard programmer dump to H7 format using a blank H7 dump
    as the NAND randomizer source.

    The blank H7 dump of the TARGET chip type provides the per-page
    randomizer sequences needed for the conversion.

    Args:
        std_path: Standard format dump file (.bin with 16-byte header + XOR scrambled data)
        blank_h7_path: H7 format blank dump of the TARGET chip type (provides randomizer)
        output_path: Output H7 format file
    """
    with open(std_path, 'rb') as f:
        std_raw = f.read()
    with open(blank_h7_path, 'rb') as f:
        blank_h7 = f.read()

    if std_raw[:16] != FILE_HEADER:
        print(f"ERROR: {std_path} is not a standard format dump (wrong header)")
        return

    # Descramble standard data to get logical data
    std_data = std_raw[16:]
    logical = descramble(std_data)

    # Build H7 pages using blank dump as randomizer
    h7_pages = bytearray()
    for page_idx in range(H7_PAGES_PER_BLOCK):
        # Randomizer = blank_page XOR 0xFF
        rand_start = 16 + page_idx * H7_PAGE_SIZE
        rand_page = blank_h7[rand_start:rand_start + H7_PAGE_SIZE]
        randomizer_data = bytes(b ^ 0xFF for b in rand_page[:H7_DATA_PER_PAGE])

        # Logical data for this page
        data_start = page_idx * H7_DATA_PER_PAGE
        if data_start + H7_DATA_PER_PAGE <= len(logical):
            page_logical = logical[data_start:data_start + H7_DATA_PER_PAGE]
        else:
            page_logical = b'\xFF' * H7_DATA_PER_PAGE

        # XOR logical data with randomizer
        h7_data = bytes(a ^ b for a, b in zip(page_logical, randomizer_data))
        # Spare area from blank (keeps erased spare)
        h7_spare = rand_page[H7_DATA_PER_PAGE:H7_PAGE_SIZE]

        h7_pages.extend(h7_data)
        h7_pages.extend(h7_spare)

    # Header (zeros — the H7 will regenerate this)
    h7_header = b'\x00' * 16

    # Block address table from blank dump
    trailer_start = 16 + H7_PAGES_PER_BLOCK * H7_PAGE_SIZE
    h7_trailer = blank_h7[trailer_start:trailer_start + 512]
    if len(h7_trailer) < 512:
        h7_trailer += b'\x00' * (512 - len(h7_trailer))

    output = h7_header + bytes(h7_pages) + h7_trailer
    with open(output_path, 'wb') as f:
        f.write(output)

    print(f"Converted: {os.path.basename(std_path)} → {output_path}")
    print(f"  Standard format: {len(std_raw)} bytes")
    print(f"  H7 format: {len(output)} bytes")
    print(f"  Randomizer source: {os.path.basename(blank_h7_path)}")


def convert_h7_to_std(h7_path: str, blank_h7_path: str, output_path: str):
    """
    Convert an H7 format dump to standard programmer format.

    Args:
        h7_path: H7 format dump file
        blank_h7_path: H7 format blank dump of the SAME chip type (provides randomizer)
        output_path: Output standard format file
    """
    with open(h7_path, 'rb') as f:
        h7_data = f.read()
    with open(blank_h7_path, 'rb') as f:
        blank_h7 = f.read()

    # Extract logical data by removing randomizer
    logical = bytearray()
    for page_idx in range(H7_PAGES_PER_BLOCK):
        h7_start = 16 + page_idx * H7_PAGE_SIZE
        h7_page = h7_data[h7_start:h7_start + H7_DATA_PER_PAGE]

        rand_start = 16 + page_idx * H7_PAGE_SIZE
        rand_page = blank_h7[rand_start:rand_start + H7_DATA_PER_PAGE]
        randomizer = bytes(b ^ 0xFF for b in rand_page)

        page_logical = bytes(a ^ b for a, b in zip(h7_page, randomizer))
        logical.extend(page_logical)

    # Apply standard scramble
    std_data = scramble(bytes(logical))
    output = FILE_HEADER + std_data

    with open(output_path, 'wb') as f:
        f.write(output)

    print(f"Converted: {os.path.basename(h7_path)} → {output_path}")
    print(f"  H7 format: {len(h7_data)} bytes")
    print(f"  Standard format: {len(output)} bytes")

# ── CLI ───────────────────────────────────────────────────────────────────────

USAGE = """
NandX — Apple Silicon NAND Image Tool
====================================

Usage:
  nand_tool.py info <file.bin>              Analyze a dump file
  nand_tool.py info-all <directory>         Analyze all .bin in directory
  nand_tool.py detail <file.bin>            Show header slot table
  nand_tool.py compare <a.bin> <b.bin>      Compare two dumps
  nand_tool.py scan <file.bin>              Scan wiped/corrupted dump for surviving data
  nand_tool.py adapt <src> <f2> <out> [n]   Adapt file or dir to new chip type (F2+F3)
  nand_tool.py generate <f2> <dies> <mb> <out> [name]
                                            Generate erased dumps with header metadata
  nand_tool.py true-blank <size_mb> <out>   Generate fully erased image (no metadata)
  nand_tool.py min-ftl <f2> <size_mb> <out> Generate minimal FTL header + erased data
  nand_tool.py list-chips                   Show known chip types and generations
  nand_tool.py register-chip <f.bin> <name> Register new chip type from a dump
  nand_tool.py to-h7 <std.bin> <blank_h7.bin> <out.bin>
                                            Convert standard → H7 format (for LB H7 programmer)
  nand_tool.py from-h7 <h7.bin> <blank_h7.bin> <out.bin>
                                            Convert H7 → standard format (for analysis)
  nand_tool.py descramble <in> <out>        Remove XOR scrambling for analysis
  nand_tool.py scramble <in> <out>          Re-apply XOR scrambling

Experimental (for wiped/stuck chips):
  true-blank   Generates an image where every page is the erased pattern.
               The ANS firmware should see namespace_count=0 → "Blank NAND".
               Use this when a chip erase didn't fully clear metadata.
  min-ftl      Generates the smallest valid FTL header (~5-20KB) with correct
               F2+F3 auth tags. Everything else is erased. The ANS firmware
               should see valid namespace metadata and proceed to clean_NAND.

Notes:
  - 'adapt' sets correct F3 auth tags when the target chip type is known.
  - 'scan' can identify chip types from partially wiped or corrupted dumps.
  - 'generate' uses correct per-generation F1/Tag cycles.
  - After flashing any image, a DFU restore is required.
"""


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return

    cmd = sys.argv[1]

    if cmd == 'info':
        if len(sys.argv) < 3:
            print("Usage: nand_tool.py info <file.bin>")
            return
        dump = NANDDump(sys.argv[2])
        dump.info()

    elif cmd == 'info-all':
        if len(sys.argv) < 3:
            print("Usage: nand_tool.py info-all <directory>")
            return
        d = sys.argv[2]
        for fname in sorted(os.listdir(d)):
            if fname.endswith('.bin'):
                try:
                    dump = NANDDump(os.path.join(d, fname))
                    dump.info()
                    print()
                except Exception as e:
                    print(f"{fname}: ERROR - {e}\n")

    elif cmd == 'detail':
        if len(sys.argv) < 3:
            print("Usage: nand_tool.py detail <file.bin>")
            return
        dump = NANDDump(sys.argv[2])
        dump.info()
        dump.header_detail()

    elif cmd == 'compare':
        if len(sys.argv) < 4:
            print("Usage: nand_tool.py compare <a.bin> <b.bin>")
            return
        d1 = NANDDump(sys.argv[2])
        d2 = NANDDump(sys.argv[3])
        print(f"Dump 1: {d1.filepath.name} ({d1.chip_type})")
        print(f"Dump 2: {d2.filepath.name} ({d2.chip_type})")
        compare_dumps(d1, d2)

    elif cmd == 'scan':
        if len(sys.argv) < 3:
            print("Usage: nand_tool.py scan <file.bin>")
            return
        scan_dump(sys.argv[2])

    elif cmd == 'adapt':
        if len(sys.argv) < 5:
            print("Usage: nand_tool.py adapt <donor_file_or_dir> <target_f2_hex> <output_dir> [chip_name]")
            return
        donor_path = sys.argv[2]
        target_f2 = bytes.fromhex(sys.argv[3])
        output_dir = sys.argv[4]
        chip_name = sys.argv[5] if len(sys.argv) > 5 else "ADAPTED"
        clone_with_new_f2(donor_path, target_f2, output_dir, chip_name)

    elif cmd == 'generate':
        if len(sys.argv) < 6:
            print("Usage: nand_tool.py generate <f2_hex> <num_dies> <die_size_mb> <output_dir> [name]")
            return
        target_f2 = bytes.fromhex(sys.argv[2])
        num_dies = int(sys.argv[3])
        die_sizes = [int(x) for x in sys.argv[4].split(',')]
        output_dir = sys.argv[5]
        chip_name = sys.argv[6] if len(sys.argv) > 6 else "GENERATED"
        generate_erased_dump(target_f2, num_dies, die_sizes, output_dir, chip_name)

    elif cmd == 'list-chips':
        print("Known NAND chip types:\n")
        for gen_name, gen_chips in CHIP_GENERATIONS.items():
            print(f"  [{gen_name}]")
            for f2_hex in gen_chips:
                name = KNOWN_CHIPS.get(f2_hex, 'Unknown')
                slots = get_header_slot_count(f2_hex)
                print(f"    {f2_hex}  {name}  ({slots} hdr slots)")
            print()
        print("  Cross-generation adaptation will NOT work.")
        print("  Same-generation chips share F1 values and have compatible dense data.")

    elif cmd == 'register-chip':
        if len(sys.argv) < 4:
            print("Usage: nand_tool.py register-chip <file.bin> <chip_name>")
            return
        dump = NANDDump(sys.argv[2])
        f2_hex = dump.f2.hex()
        name = sys.argv[3]
        if f2_hex in KNOWN_CHIPS:
            print(f"Already registered: {KNOWN_CHIPS[f2_hex]}")
        else:
            print(f"New chip type found!")
            print(f"  F2:           {f2_hex}")
            print(f"  Name:         {name}")
            print(f"  Header slots: {dump.num_header_slots}")
            print(f"  Generation:   {dump.generation}")
            print(f"\nAdd to KNOWN_CHIPS in nand_tool.py:")
            print(f"    '{f2_hex}': '{name}',")
            print(f"\nF3 table ({dump.num_header_slots} entries):")
            print(f"    '{f2_hex}': [  # {name}")
            for rec in dump.header_records:
                print(f"        '{rec['f3'].hex()}',")
            print(f"    ],")

    elif cmd == 'true-blank':
        if len(sys.argv) < 4:
            print("Usage: nand_tool.py true-blank <size_mb> <output.bin>")
            print("  Generates a fully erased image (every page = erased pattern).")
            print("  Flash this to make the ANS firmware see a blank NAND.")
            return
        size_mb = int(sys.argv[2])
        generate_true_blank(size_mb, sys.argv[3])

    elif cmd == 'min-ftl':
        if len(sys.argv) < 5:
            print("Usage: nand_tool.py min-ftl <f2_hex> <size_mb> <output.bin>")
            print("  Generates minimal valid FTL header with everything else erased.")
            print("  Use 'list-chips' to find F2 values for known chip types.")
            return
        generate_minimal_ftl(sys.argv[2], int(sys.argv[3]), sys.argv[4])

    elif cmd == 'to-h7':
        if len(sys.argv) < 5:
            print("Usage: nand_tool.py to-h7 <standard.bin> <blank_h7.bin> <output.bin>")
            print("  Convert standard format dump to H7 format for LB H7 programmer.")
            print("  blank_h7.bin = H7 blank dump of the TARGET chip (provides randomizer).")
            return
        convert_std_to_h7(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == 'from-h7':
        if len(sys.argv) < 5:
            print("Usage: nand_tool.py from-h7 <h7.bin> <blank_h7.bin> <output.bin>")
            print("  Convert H7 format dump to standard format for analysis.")
            print("  blank_h7.bin = H7 blank dump of the SAME chip (provides randomizer).")
            return
        convert_h7_to_std(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == 'descramble':
        if len(sys.argv) < 4:
            print("Usage: nand_tool.py descramble <in.bin> <out.bin>")
            return
        with open(sys.argv[2], 'rb') as f:
            raw = f.read()
        desc = raw[:0x10] + descramble(raw[0x10:])
        with open(sys.argv[3], 'wb') as f:
            f.write(desc)
        print(f"Descrambled {len(raw)} bytes -> {sys.argv[3]}")

    elif cmd == 'scramble':
        if len(sys.argv) < 4:
            print("Usage: nand_tool.py scramble <in.bin> <out.bin>")
            return
        with open(sys.argv[2], 'rb') as f:
            raw = f.read()
        scr = raw[:0x10] + scramble(raw[0x10:])
        with open(sys.argv[3], 'wb') as f:
            f.write(scr)
        print(f"Scrambled {len(raw)} bytes -> {sys.argv[3]}")

    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)


if __name__ == '__main__':
    main()



