from pynq import Overlay
import time

# ============================================================
# REGISTER OFFSETS
# ============================================================
CTRL_OFFSET  = 0x00
ADDR_OFFSET  = 0x04
WDATA_OFFSET = 0x08
RDATA_OFFSET = 0x0C

# ============================================================
# CONTROL BIT DEFINITIONS
# ============================================================
BIT_RESET      = (1 << 0)
BIT_START      = (1 << 1)
BIT_WR_SK_SEED = (1 << 4)
BIT_WR_PK_SEED = (1 << 5)
BIT_RD_EN      = (1 << 12)

# Output selection (bits [14:13])
OUT_X = 0
OUT_Y = 1
OUT_H = 2
OUT_S = 3

# Status register address
ADDR_STATUS = 0xFFFFFFFF

# HQC parameters
SEED_WORDS  = 10
H_S_ENTRIES = 139
X_Y_ENTRIES = 66


class HQCKeygenDriver:

    def __init__(self, bitfile_path: str):
        print("[HQC] Loading overlay...")
        self.ol = Overlay(bitfile_path)
        self.ip = self.ol.axi_wrapper_0
        print(f"[HQC] Base address : 0x{self.ip.mmio.base_addr:08X}")
        print(f"[HQC] Address range: {self.ip.mmio.length} bytes")
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
        """
        Pulse reset: set bit[0]=1 then clear.
        No sleep needed since subsequent ip.write already introduces delay.
        """
        print("[HQC] Resetting core...")
        self._write_ctrl(BIT_RESET)
        self._write_ctrl(0x00)
        print("[HQC] Reset done.")

    # ----------------------------------------------------------
    # STEP 2: LOAD SEEDS
    # ----------------------------------------------------------
    def _load_seed_array(self, seed_words: list, wr_bit: int, label: str):
        assert len(seed_words) == SEED_WORDS, \
            f"Expected {SEED_WORDS} words, got {len(seed_words)}"

        print(f"[HQC] Loading {label} ({SEED_WORDS} words)...")
        for i, word in enumerate(seed_words):
            self._write_wdata(word)
            self._write_addr(i)

            # Pulse write bit (no sleep required)
            self._write_ctrl(self._ctrl | wr_bit)
            self._write_ctrl(self._ctrl & ~wr_bit)

        print(f"[HQC] {label} loaded.")

    def load_seeds(self, sk_seed: list, pk_seed: list):
        self._load_seed_array(sk_seed, BIT_WR_SK_SEED, "SK seed")
        self._load_seed_array(pk_seed, BIT_WR_PK_SEED, "PK seed")

    # ----------------------------------------------------------
    # STEP 3: START
    # ----------------------------------------------------------
    def start_keygen(self):
        """Set MODE=00 and pulse START bit"""
        print("[HQC] Starting key generation...")
        ctrl = self._ctrl & ~(0b11 << 2)
        self._write_ctrl(ctrl | BIT_START)
        self._write_ctrl(ctrl & ~BIT_START)
        print("[HQC] Start pulse sent.")

    # ----------------------------------------------------------
    # STEP 4: POLL DONE
    # ----------------------------------------------------------
    def wait_done(self, timeout_sec: float = 10.0) -> bool:
        """
        Poll status register until rdata[0] == 1.
        Use sleep(1ms) between polls to avoid CPU overuse.
        """
        print("[HQC] Waiting for DONE...")
        self._write_addr(ADDR_STATUS)

        t_start = time.time()
        count = 0

        while True:
            if self._read_rdata() & 0x1:
                print(f"[HQC] DONE after {count} polls "
                      f"({time.time()-t_start:.3f}s)")
                return True

            count += 1
            time.sleep(0.001)

            if time.time() - t_start > timeout_sec:
                print(f"[HQC] TIMEOUT after {timeout_sec}s!")
                return False

    # ----------------------------------------------------------
    # STEP 5: READ OUTPUTS
    # ----------------------------------------------------------
    def _read_128bit_array(self, out_sel: int, n_entries: int, label: str) -> list:
        """
        Read n_entries × 128-bit values.
        Each entry consists of 4 × 32-bit words.
        No sleep needed due to Python overhead.
        """
        print(f"[HQC] Reading {label} ({n_entries} × 128-bit)...")
        results = []

        for i in range(n_entries):
            self._write_addr(i)
            val_128 = 0

            for w in range(4):
                ctrl_val = (out_sel << 13) | BIT_RD_EN | (w << 10)
                self._write_ctrl(ctrl_val)
                val_128 |= (self._read_rdata() << (32 * w))

            results.append(val_128 & ((1 << 128) - 1))

        print(f"[HQC] {label} done.")
        return results

    def _read_15bit_array(self, out_sel: int, n_entries: int, label: str) -> list:
        """Read n_entries × 15-bit values (X or Y)"""
        print(f"[HQC] Reading {label} ({n_entries} × 15-bit)...")
        results = []

        for i in range(n_entries):
            self._write_addr(i)
            ctrl_val = (out_sel << 13) | BIT_RD_EN
            self._write_ctrl(ctrl_val)
            results.append(self._read_rdata() & 0x7FFF)

        print(f"[HQC] {label} done.")
        return results

    def read_h(self): return self._read_128bit_array(OUT_H, H_S_ENTRIES, "H")
    def read_s(self): return self._read_128bit_array(OUT_S, H_S_ENTRIES, "S")
    def read_x(self): return self._read_15bit_array(OUT_X, X_Y_ENTRIES, "X")
    def read_y(self): return self._read_15bit_array(OUT_Y, X_Y_ENTRIES, "Y")

    # ----------------------------------------------------------
    # FULL FLOW
    # ----------------------------------------------------------
    def run_keygen(self, sk_seed: list, pk_seed: list) -> dict:
        print("\n" + "="*50)
        print("  HQC KEY GENERATION")
        print("="*50)

        t0 = time.time()

        self.reset()
        self.load_seeds(sk_seed, pk_seed)
        self.start_keygen()

        if not self.wait_done(timeout_sec=10.0):
            raise RuntimeError("HQC Keygen TIMEOUT!")

        result = {
            'h': self.read_h(),
            's': self.read_s(),
            'x': self.read_x(),
            'y': self.read_y(),
        }

        print(f"\n[HQC] Total time: {time.time()-t0:.3f}s")
        print("="*50)

        return result


# ============================================================
# UTILITIES
# ============================================================
def parse_seed_file(filepath: str) -> list:
    """Read binary seed file (from $readmemb)"""
    words = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                words.append(int(line, 2))

    assert len(words) == 10, f"Seed file must contain 10 words, got {len(words)}"
    return words


def save_128bit(data: list, filepath: str):
    with open(filepath, 'w') as f:
        for v in data:
            f.write(f"{v:0128b}\n")
    print(f"[SAVE] {filepath} ({len(data)} entries)")


def save_15bit(data: list, filepath: str):
    with open(filepath, 'w') as f:
        for v in data:
            f.write(f"{v:015b}\n")
    print(f"[SAVE] {filepath} ({len(data)} entries)")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":

    BITFILE = "hqc_accelerator.bit"
    driver = HQCKeygenDriver(BITFILE)

    sk_seed = parse_seed_file("sk_seed.in")
    pk_seed = parse_seed_file("pk_seed.in")

    result = driver.run_keygen(sk_seed, pk_seed)

    print(f"\nH[0] = 0x{result['h'][0]:032X}")
    print(f"S[0] = 0x{result['s'][0]:032X}")
    print(f"X[0] = {result['x'][0]:015b}")
    print(f"Y[0] = {result['y'][0]:015b}")

    save_128bit(result['h'], "h_128_pynq.out")
    save_128bit(result['s'], "s_128_pynq.out")
    save_15bit(result['x'],  "x_128_pynq.out")
    save_15bit(result['y'],  "y_128_pynq.out")

