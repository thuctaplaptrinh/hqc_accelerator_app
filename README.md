# HQC × AES-256-GCM — Hybrid KEM/DEM Secure Communication

> **Post-Quantum Cryptography** hardware accelerator demo trên **Xilinx Kria KV260**.
> Kết hợp thuật toán **HQC (Hamming Quasi-Cyclic) KEM** chạy trên FPGA với mã hóa đối xứng **AES-256-GCM** trên phần mềm.

---

## 📋 Mục đích

Ứng dụng web Flask mô phỏng luồng truyền thông bảo mật lai (Hybrid KEM/DEM) giữa hai bên:

| Vai trò | Mô tả |
|---------|-------|
| **Alice** (Receiver) | Sinh cặp khóa bất đối xứng trên FPGA, giải đóng gói KEM và giải mã dữ liệu |
| **Bob** (Sender) | Đóng gói KEM trên FPGA, mã hóa dữ liệu (ảnh) bằng AES-256-GCM |
| **Public Channel** | Kênh truyền tải công khai — log JSON real-time |

---

## 🏗️ Kiến trúc hệ thống

```
┌──────────────┐                              ┌──────────────┐
│   ALICE       │        Public Channel        │     BOB       │
│  (Receiver)   │  ◀════════════════════════▶  │   (Sender)    │
├──────────────┤                              ├──────────────┤
│ Phase 0:     │                              │ Phase 1:     │
│  KeyGen (HW) │───── pk ────────────────────▶│  Encap (HW)  │
│              │                              │  → K, c_kem  │
│ Phase 4:     │◀──── c_kem, ciphertext ──────│ Phase 2:     │
│  Decap (HW)  │      nonce, tag              │  AES Enc (SW)│
│  → K'        │                              │              │
│ Phase 5:     │                              │              │
│  AES Dec (SW)│                              │              │
│  → plaintext │                              │              │
└──────────────┘                              └──────────────┘
         │                                            │
         └──────────── Kria KV260 FPGA ───────────────┘
```

---

## 📂 Cấu trúc dự án

```
hqc_accelerator_app/
├── app.py                  # Flask server — API endpoints & background job queue
├── crypto.py               # AES-256-GCM + HKDF key derivation
├── requirements.txt        # Python dependencies
├── hqc_accelerator.bit     # FPGA bitstream file (deploy trên KV260)
│
├── drivers/                # Hardware driver modules (PYNQ MMIO)
│   ├── keygen.py           #   Key Generation driver
│   ├── encap.py            #   Encapsulation driver
│   └── decap.py            #   Decapsulation driver
│
├── templates/
│   └── index.html          # Single-page UI (TailwindCSS + inline JS)
│
├── static/
│   ├── style.css           # Glassmorphism theme & animations
│   └── assets/
│       └── meo.png         # Demo image (plaintext để mã hóa)
│
└── venv/                   # Python virtual environment
```

---

## ⚙️ Cài đặt & Chạy

### Yêu cầu

- **Python** >= 3.11
- **pip** (Python package manager)

### Trên Windows (Development / Demo trên PC)

```powershell
# 1. Tạo môi trường ảo
python -m venv venv

# 2. Kích hoạt môi trường ảo
.\venv\Scripts\Activate.ps1

# 3. Cài đặt thư viện
pip install -r requirements.txt

# 4. Chạy server
python app.py
```

> **Lưu ý:** Trên PC không có module `pynq`, app tự động dùng **Mock Drivers** để giả lập phần cứng FPGA. Mọi chức năng vẫn hoạt động bình thường để kiểm tra giao diện và luồng xử lý.

### Trên Kria KV260 (Production — Hardware thật)
```bash
# 1. Cài thư viện còn thiếu
sudo /usr/local/share/pynq-venv/bin/python3 -m pip install r requirements.txt

# 2. Tạo link đến xclbinutil
sudo ln -s /usr/local/share/pynq-venv/bin/xclbinutil /usr/bin/xclbinutil

# 3. Vào thư mục hqc_accelerator_app
cd hqc_accelerator_app

# 4. Đảm bảo 2 file .bit và .hwh đã được thêm vào thư mục hqc_accelerator_app.

# 5. Chạy server với quyền root (cần cho PYNQ Overlay)
sudo -E /usr/local/share/pynq-venv/bin/python3 app.py
```

### Truy cập giao diện

Mở trình duyệt tại: **http://\<IP_ADDRESS\>:5000**

- Trên PC: `http://127.0.0.1:5000`
- Trên KV260: `http://<IP-của-board>:5000`

---

## 🚀 Hướng dẫn sử dụng

Giao diện chia làm 3 vùng: **Bob (Sender)** — **Public Channel** — **Alice (Receiver)**.

Nhấn **"⚡ Run All Phases"** để chạy toàn bộ demo tự động, hoặc nhấn từng nút **"▶ Phase X"** theo thứ tự:

| Phase | Tên | Bên | Loại | Mô tả |
|-------|-----|-----|------|-------|
| 0 | **KeyGen** | Alice | HW (FPGA) | Sinh cặp khóa công khai `pk = {h, s}` và bí mật `sk = {x, y}` |
| 1 | **Encap** | Bob | HW (FPGA) | Tạo khóa chung `K` và bản mã KEM `c_kem = {u, v, d}` từ `pk` |
| 2 | **AES Encrypt** | Bob | SW (CPU) | Mã hóa ảnh `meo.png` bằng AES-256-GCM với khóa `K` |
| 3 | **Transmit** | Channel | UI only | Mô phỏng truyền `{c_kem, ciphertext, nonce, tag}` qua kênh công khai |
| 4 | **Decap** | Alice | HW (FPGA) | Giải mã KEM bằng `sk` → khôi phục khóa `K'` |
| 5 | **AES Decrypt** | Alice | SW (CPU) | Giải mã ảnh bằng `K'`, xác thực Auth Tag |

---

## 🔐 Bảo mật

| Thành phần | Chi tiết |
|------------|----------|
| **KEM** | HQC-128 (Post-Quantum, NIST Round 4 candidate) |
| **KDF** | HKDF-SHA256 — chuyển đổi Shared Secret 512-bit → AES key 256-bit |
| **DEM** | AES-256-GCM — mã hóa authenticated, nonce 96-bit, tag 128-bit |
| **Non-blocking** | Hardware calls chạy trên background thread với `threading.Lock` |

---

## 🔧 Biến môi trường (tùy chọn)

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `HQC_BITFILE` | `hqc_accelerator.bit` | Đường dẫn file bitstream FPGA |
| `APP_SECRET_KEY` | `hqc-demo-secret` | Flask session secret key |

---

## 📜 License

Graduation Thesis Project — HCMUT 2026.