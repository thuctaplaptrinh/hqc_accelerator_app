from pynq import Overlay
import time
import struct

# ============================================================
# MMIO OFFSETS
# ============================================================
CTRL_OFFSET  = 0x00
ADDR_OFFSET  = 0x04
WDATA_OFFSET = 0x08
RDATA_OFFSET = 0x0C

# ============================================================
# CONTROL BIT DEFINITIONS
# ============================================================
BIT_RESET  = (1 << 0)
BIT_START  = (1 << 1)
BIT_WR_EN  = (1 << 6)
BIT_RD_EN  = (1 << 12)
OP_DECAP   = (0b10 << 2)   # OP = 10 → Decap mode

# RAM selection (bits [9:7])
RAM_H = 0
RAM_S = 1
RAM_U = 2
RAM_V = 3
RAM_Y = 4
RAM_D = 5

def RAM_SEL_BITS(sel: int) -> int:
    return (sel & 0x7) << 7

def WORD_SEL_BITS(w: int) -> int:
    return (w & 0x3) << 10

# Status register
ADDR_STATUS = 0xFFFFFFFF

# HQC-128 parameters
N_MEM_WIDTH = 139
Y_DEPTH     = 66
D_DEPTH     = 16
SS_WORDS    = 16


# ============================================================
# HELPER
# ============================================================
def _swap_endian_32(val: int) -> int:
    """Swap byte order of a 32-bit word"""
    return (((val & 0x000000FF) << 24) |
            ((val & 0x0000FF00) <<  8) |
            ((val & 0x00FF0000) >>  8) |
            ((val & 0xFF000000) >> 24))


# ============================================================
# DECAP DRIVER
# ============================================================
class HQCDecapDriver:
    """
    PYNQ driver for HQC Decapsulation.

    Input : H, S, U, V, Y, D
    Output: Shared Secret (SS)
    """

    def __init__(self, bitfile_path: str):
        print("[DECAP] Loading overlay...")
        self.ol = Overlay(bitfile_path)
        self.ip = self.ol.axi_wrapper_0
        print(f"[DECAP] Base address : 0x{self.ip.mmio.base_addr:08X}")
        print(f"[DECAP] Address range: {self.ip.mmio.length} bytes")
        self._ctrl = 0

    # ----------------------------------------------------------
    # LOW-LEVEL REGISTER ACCESS
    # ----------------------------------------------------------
    def _write_ctrl(self, value: int):
        self._ctrl = value & 0xFFFFFFFF
        self.ip.write(CTRL_OFFSET, self._ctrl)

    def _write_addr(self, value: int):
        self.ip.write(ADDR_OFFSET, value & 0xFFFFFFFF)

    def _write_wdata(self, value: int):
        self.ip.write(WDATA_OFFSET, value & 0xFFFFFFFF)

    def _read_rdata(self) -> int:
        return self.ip.read(RDATA_OFFSET)

    # ----------------------------------------------------------
    # STEP 1: RESET
    # ----------------------------------------------------------
    def reset(self):
        """Pulse RESET bit"""
        print("[DECAP] Resetting core...")
        self._write_ctrl(BIT_RESET)
        self._write_ctrl(0x00)
        print("[DECAP] Reset done.")

    # ----------------------------------------------------------
    # STEP 2: LOAD RAM 128-bit (H, S, U, V)
    # ----------------------------------------------------------
    def _load_ram_128(self, data: list, ram_sel: int, label: str):
        """
        Load 128-bit entries into RAM.

        Protocol:
          - Write 4 words using WORD_SEL
          - Commit using WR_EN with RAM_SEL
        """
        print(f"[DECAP] Loading {label} ({len(data)} × 128-bit)...")

        for i, val_128 in enumerate(data):
            self._write_addr(i)
            tmp = val_128

            for w in range(4):
                self._write_wdata(tmp & 0xFFFFFFFF)
                self._write_ctrl(WORD_SEL_BITS(w))
                tmp >>= 32

            self._write_ctrl(
                WORD_SEL_BITS(3) | RAM_SEL_BITS(ram_sel) | BIT_WR_EN
            )
            self._write_ctrl(0)

        print(f"[DECAP] {label} loaded.")

    def load_h(self, data: list): self._load_ram_128(data, RAM_H, "H")
    def load_s(self, data: list): self._load_ram_128(data, RAM_S, "S")
    def load_u(self, data: list): self._load_ram_128(data, RAM_U, "U")
    def load_v(self, data: list): self._load_ram_128(data, RAM_V, "V")

    # ----------------------------------------------------------
    # STEP 3: LOAD RAM 32-bit (Y, D)
    # ----------------------------------------------------------
    def _load_ram_32(self, data: list, ram_sel: int, label: str):
        """
        Load 32-bit entries into RAM.

        Protocol:
          ctrl = WORD_SEL(0)
          ctrl = WORD_SEL(0) | RAM_SEL | WR_EN
          ctrl = 0
        """
        print(f"[DECAP] Loading {label} ({len(data)} × 32-bit)...")

        for i, word in enumerate(data):
            self._write_wdata(word)
            self._write_addr(i)

            self._write_ctrl(WORD_SEL_BITS(0))
            self._write_ctrl(
                WORD_SEL_BITS(0) | RAM_SEL_BITS(ram_sel) | BIT_WR_EN
            )
            self._write_ctrl(0)

        print(f"[DECAP] {label} loaded.")

    def load_y(self, data: list):
        assert len(data) == Y_DEPTH
        self._load_ram_32(data, RAM_Y, "Y")

    def load_d(self, data: list):
        assert len(data) == D_DEPTH
        self._load_ram_32(data, RAM_D, "D")

    # ----------------------------------------------------------
    # STEP 4: START DECAP
    # ----------------------------------------------------------
    def start_decap(self):
        """
        Trigger decapsulation:
          OP=10, pulse START
        """
        print("[DECAP] Starting decapsulation...")
        self._write_ctrl(OP_DECAP | BIT_START)
        self._write_ctrl(OP_DECAP)
        print("[DECAP] Start pulse sent.")

    # ----------------------------------------------------------
    # STEP 5: POLL DONE
    # ----------------------------------------------------------
    def wait_done(self, timeout_sec: float = 60.0) -> bool:
        print("[DECAP] Waiting for DONE...")
        self._write_addr(ADDR_STATUS)

        t0 = time.time()
        count = 0

        while True:
            if self._read_rdata() & 0x1:
                print(f"[DECAP] DONE ({count} polls, "
                      f"{time.time()-t0:.3f}s)")
                return True

            count += 1
            time.sleep(0.001)

            if time.time() - t0 > timeout_sec:
                print(f"[DECAP] TIMEOUT after {timeout_sec}s!")
                return False

    # ----------------------------------------------------------
    # STEP 6: READ SHARED SECRET
    # ----------------------------------------------------------
    def read_ss(self) -> bytes:
        """
        Read Shared Secret: 16 × 32-bit.

        Control: RD_EN | OP_DECAP
        Each word is endian-swapped.
        """
        print("[DECAP] Reading Shared Secret...")

        result = b""
        for i in range(SS_WORDS):
            self._write_addr(i)
            self._write_ctrl(BIT_RD_EN | OP_DECAP)

            raw = self._read_rdata()
            swapped = _swap_endian_32(raw)
            result += struct.pack('>I', swapped)

        print(f"[DECAP] SS = {result.hex()[:32]}...")
        return result

    # ----------------------------------------------------------
    # FULL FLOW
    # ----------------------------------------------------------
    def run_decap(self,
                  h_data: list,
                  s_data: list,
                  u_data: list,
                  v_data: list,
                  y_words: list,
                  d_words: list) -> bytes:

        print("\n" + "="*50)
        print("  HQC DECAPSULATION")
        print("="*50)

        t0 = time.time()

        self.reset()
        self.load_h(h_data)
        self.load_s(s_data)
        self.load_u(u_data)
        self.load_v(v_data)
        self.load_y(y_words)
        self.load_d(d_words)

        self.start_decap()

        if not self.wait_done(timeout_sec=60.0):
            raise RuntimeError("HQC Decap TIMEOUT!")

        ss = self.read_ss()

        print(f"\n[DECAP] Total: {time.time()-t0:.3f}s")
        print("="*50)

        return ss


# ============================================================
# FILE UTILITIES
# ============================================================
def parse_128bit_file(filepath: str) -> list:
    """Read 128-bit binary file"""
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(int(line, 2))
    return data


def parse_32bit_bin_file(filepath: str) -> list:
    """Read 32-bit binary file"""
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(int(line, 2))
    return data


def parse_32bit_hex_file(filepath: str) -> list:
    """Read 32-bit hex file"""
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(int(line, 16))
    return data


def save_ss(ss_bytes: bytes, filepath: str):
    with open(filepath, 'w') as f:
        f.write(ss_bytes.hex())
    print(f"[SAVE] {filepath} ({len(ss_bytes)} bytes)")


if __name__ == "__main__":

    BITFILE = "hqc_accelerator.bit"
    driver = HQCDecapDriver(BITFILE)

    h_data  = parse_128bit_file("h_128.in")
    s_data  = parse_128bit_file("s_128.in")
    u_data  = parse_128bit_file("u_128.in")
    v_data  = parse_128bit_file("v_128.in")
    y_words = parse_32bit_bin_file("y_128.in")
    d_words = parse_32bit_hex_file("d_128.in")

    ss = driver.run_decap(h_data, s_data, u_data, v_data, y_words, d_words)

    save_ss(ss, "ss_decap_output.out")

    print(f"\n[RESULT] SS = {ss.hex()}")