from pynq import Overlay
import time
import struct

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
BIT_RESET    = (1 << 0)
BIT_START    = (1 << 1)
BIT_OP_ENCAP = (1 << 2)   # OP = 1 → Encap mode
BIT_WR_EN    = (1 << 6)   # Write enable pulse
BIT_RD_EN    = (1 << 12)  # Read enable (encap_out_en)
BIT_RD_SRC   = (1 << 15)  # Read source: 1 = wrapper RAM

# RAM selection (bits [9:7])
RAM_H   = 0
RAM_S   = 1
RAM_U   = 2
RAM_V   = 3
RAM_D   = 5
RAM_MSG = 6

def RAM_SEL_BITS(sel: int) -> int:
    return (sel & 0x7) << 7

# WORD selection (bits [11:10])
def WORD_SEL_BITS(w: int) -> int:
    return (w & 0x3) << 10

# ============================================================
# ENCAP OUTPUT TYPE (bits [14:13] = encap_out_type)
# ============================================================
OUT_TYPE_SS = 0   # Shared Secret (32-bit)
OUT_TYPE_D  = 1   # D (32-bit)
OUT_TYPE_U  = 2   # U (128-bit)
OUT_TYPE_V  = 3   # V (128-bit)

def OUT_TYPE_BITS(t: int) -> int:
    return (t & 0x3) << 13

# Status register
ADDR_STATUS = 0xFFFFFFFF

# HQC-128 parameters
N_MEM_WIDTH = 139
MSG_DEPTH   = 4
SS_WORDS    = 16
D_WORDS     = 16


class HQCEncapDriver:
    def __init__(self, bitfile_path: str):
        print("[ENCAP] Loading overlay...")
        self.ol = Overlay(bitfile_path)
        self.ip = self.ol.axi_wrapper_0
        print(f"[ENCAP] Base address : 0x{self.ip.mmio.base_addr:08X}")
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
        print("[ENCAP] Resetting core...")
        self._write_ctrl(BIT_RESET)
        self._write_ctrl(0x00)
        print("[ENCAP] Reset done.")

    # ----------------------------------------------------------
    # STEP 2: LOAD H / S (128-bit × 139)
    #
    # ĐÃ SỬA: Ghi ctrl (word_sel) TRƯỚC, rồi mới ghi wdata SAU.
    # 
    # Lý do: buffer_128 cập nhật trên MỌI clock edge dựa trên
    # word_sel và gpio_wdata. Nếu ghi wdata trước, wdata mới
    # sẽ bị buffer bắt vào vị trí word_sel CŨ (từ iteration trước)
    # trong khoảng thời gian giữa 2 AXI transaction.
    #
    # Khi ghi ctrl trước: word_sel đã đúng vị trí, wdata cũ bị
    # bắt tạm (transient), nhưng ngay sau đó wdata mới ghi đè
    # đúng → giá trị cuối cùng ĐÚNG.
    # ----------------------------------------------------------
    def _load_ram_128(self, data: list, ram_sel: int, label: str):
        assert len(data) == N_MEM_WIDTH, \
            f"{label}: expected {N_MEM_WIDTH}, got {len(data)}"

        print(f"[ENCAP] Loading {label} ({N_MEM_WIDTH} × 128-bit)...")

        for i, val_128 in enumerate(data):
            self._write_addr(i)

            tmp = val_128
            for w in range(4):
                # ★ QUAN TRỌNG: ctrl (word_sel) TRƯỚC, wdata SAU
                self._write_ctrl(WORD_SEL_BITS(w))
                self._write_wdata(tmp & 0xFFFFFFFF)
                tmp >>= 32

            self._write_ctrl(
                WORD_SEL_BITS(3) | RAM_SEL_BITS(ram_sel) | BIT_WR_EN
            )
            self._write_ctrl(0)

        print(f"[ENCAP] {label} loaded.")

    def load_h(self, h_data: list):
        self._load_ram_128(h_data, RAM_H, "H")

    def load_s(self, s_data: list):
        self._load_ram_128(s_data, RAM_S, "S")

    # ----------------------------------------------------------
    # STEP 3: LOAD MESSAGE (32-bit × 4)
    # Message không dùng buffer_128, ghi trực tiếp → không bị race
    # ----------------------------------------------------------
    def load_message(self, msg_words: list):
        assert len(msg_words) == MSG_DEPTH, \
            f"Message: expected {MSG_DEPTH}, got {len(msg_words)}"

        print(f"[ENCAP] Loading Message ({MSG_DEPTH} × 32-bit)...")

        for i, word in enumerate(msg_words):
            self._write_wdata(word)
            self._write_addr(i)
            self._write_ctrl(RAM_SEL_BITS(RAM_MSG) | BIT_WR_EN)
            self._write_ctrl(0)

        print("[ENCAP] Message loaded.")

    # ----------------------------------------------------------
    # STEP 4: START ENCAP
    # ----------------------------------------------------------
    def start_encap(self):
        print("[ENCAP] Starting encapsulation...")
        self._write_ctrl(BIT_OP_ENCAP | BIT_START)
        self._write_ctrl(BIT_OP_ENCAP)
        print("[ENCAP] Start pulse sent.")

    # ----------------------------------------------------------
    # STEP 5: POLL DONE
    # ----------------------------------------------------------
    def wait_done(self, timeout_sec: float = 30.0) -> bool:
        print("[ENCAP] Waiting for DONE...")
        self._write_addr(ADDR_STATUS)

        t0 = time.time()
        count = 0

        while True:
            if self._read_rdata() & 0x1:
                print(f"[ENCAP] DONE ({count} polls, "
                      f"{time.time()-t0:.3f}s)")
                return True

            count += 1
            time.sleep(0.001)

            if time.time() - t0 > timeout_sec:
                print(f"[ENCAP] TIMEOUT after {timeout_sec}s!")
                return False

    # ----------------------------------------------------------
    # STEP 6: READ OUTPUTS — qua encap_out interface
    # ----------------------------------------------------------

    def read_ss(self) -> bytes:
        print("[ENCAP] Reading Shared Secret...")
        result = b""
        for i in range(SS_WORDS):
            self._write_addr(i)
            self._write_ctrl(BIT_RD_EN | BIT_OP_ENCAP | OUT_TYPE_BITS(OUT_TYPE_SS))
            raw = self._read_rdata()
            swapped = _swap_endian_32(raw)
            result += struct.pack('>I', swapped)
        print(f"[ENCAP] SS = {result.hex()}")
        return result

    def read_d(self) -> list:
        print("[ENCAP] Reading D (via encap_out_type=1)...")
        result = []
        for i in range(D_WORDS):
            self._write_addr(i)
            self._write_ctrl(BIT_RD_EN | BIT_OP_ENCAP | OUT_TYPE_BITS(OUT_TYPE_D))
            result.append(self._read_rdata())
        print(f"[ENCAP] D[0] = 0x{result[0]:08X}")
        return result

    def _read_encap_128(self, out_type: int, n_entries: int, label: str) -> list:
        print(f"[ENCAP] Reading {label} ({n_entries} × 128-bit, out_type={out_type})...")
        results = []
        for i in range(n_entries):
            self._write_addr(i)
            val_128 = 0
            for w in range(4):
                ctrl_val = (BIT_RD_EN
                            | BIT_OP_ENCAP
                            | OUT_TYPE_BITS(out_type)
                            | WORD_SEL_BITS(w))
                self._write_ctrl(ctrl_val)
                val_128 |= (self._read_rdata() << (32 * w))
            results.append(val_128 & ((1 << 128) - 1))
        print(f"[ENCAP] {label} read done.")
        return results

    def read_u(self) -> list:
        return self._read_encap_128(OUT_TYPE_U, N_MEM_WIDTH, "U")

    def read_v(self) -> list:
        return self._read_encap_128(OUT_TYPE_V, N_MEM_WIDTH, "V")

    # ----------------------------------------------------------
    # FULL FLOW
    # ----------------------------------------------------------
    def run_encap(self, h_data: list, s_data: list, msg_words: list) -> dict:
        print("\n" + "="*50)
        print("  HQC ENCAPSULATION")
        print("="*50)

        t0 = time.time()

        self.reset()
        self.load_h(h_data)
        self.load_s(s_data)
        self.load_message(msg_words)
        self.start_encap()

        if not self.wait_done(timeout_sec=30.0):
            raise RuntimeError("HQC Encap TIMEOUT!")

        ss = self.read_ss()
        d  = self.read_d()
        u  = self.read_u()
        v  = self.read_v()

        print(f"\n[ENCAP] Total: {time.time()-t0:.3f}s")
        print("="*50)

        return {'ss': ss, 'd': d, 'u': u, 'v': v}


# ============================================================
# UTILITIES
# ============================================================
def _swap_endian_32(val: int) -> int:
    return (((val & 0xFF)       << 24) |
            ((val & 0xFF00)     <<  8) |
            ((val & 0xFF0000)   >>  8) |
            ((val & 0xFF000000) >> 24))


def parse_128bit_file(filepath: str) -> list:
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(int(line, 2))
    return data


def parse_msg_file(filepath: str) -> list:
    words = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                words.append(int(line, 2))

    assert len(words) == MSG_DEPTH, \
        f"Message file must contain {MSG_DEPTH} words"

    return words


def save_ss(ss_bytes: bytes, filepath: str):
    with open(filepath, 'w') as f:
        f.write(ss_bytes.hex())
    print(f"[SAVE] {filepath} ({len(ss_bytes)} bytes)")


def save_d(d_words: list, filepath: str):
    with open(filepath, 'w') as f:
        for w in d_words:
            f.write(f"{w:032b}\n")
    print(f"[SAVE] {filepath} ({len(d_words)} words)")


def save_128bit(data: list, filepath: str):
    with open(filepath, 'w') as f:
        for val in data:
            f.write(f"{val:0128b}\n")
    print(f"[SAVE] {filepath} ({len(data)} entries)")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":

    BITFILE = "hqc_accelerator.bit"
    driver = HQCEncapDriver(BITFILE)

    h_data    = parse_128bit_file("h_128_pynq.out")
    s_data    = parse_128bit_file("s_128_pynq.out")
    msg_words = parse_msg_file("msg_128.in")

    result = driver.run_encap(h_data, s_data, msg_words)

    save_ss(result['ss'], "ss_output_128.out")
    save_d(result['d'],   "d_128.in")
    save_128bit(result['u'], "u_128.in")
    save_128bit(result['v'], "v_128.in")

    print(f"\n[RESULT] SS = {result['ss'].hex()}")
    print(f"[RESULT] D[0] = 0x{result['d'][0]:08X}")
    print(f"[RESULT] U[0] = {result['u'][0]:0128b}")