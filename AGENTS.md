# AGENTS.md


## Project Overview

- Objective: Develop a Python-based Web Interface (Flask) to control the HQC (Hamming Quasi-Cyclic) hardware accelerator on the Xilinx Kria KV260 Starter Kit.
- Target Audience: Primarily designed for a live, visual demonstration of hardware processing results for the Graduation Thesis Defense committee.
- Hardware Platform: Xilinx Kria KV260.


## Environment
- **Python**: >= 3.11
- **Framework**: Flask 3.1.3


## Project structure:

hqc_accelerator_app/
├── drivers/
│   ├── __init__.py
│   ├── keygen.py           
│   ├── encap.py             
│   └── decap.py             
├── static/                  # Chứa CSS, hình ảnh demo
│   └── style.css
├── templates/
│   └── index.html           # Trang chủ chọn tính năng
├── .gitignore
├── app.py
├── hqc_accelerator.bit      # hardware bitstream file
├── requirements.txt
└── venv/


## System Agents (Control Modules)

The system is divided into three primary control modules corresponding to the HQC algorithm stages:

### Key Generation Agent
- Source File: drivers/keygen.py
- Functionality:
+ Loads **SK** and **PK** seeds into the hardware core via **MMIO** (Memory-Mapped I/O).
+ Triggers the hardware to compute public values **(h, s)** and secret values **(x, y)**.
+ Retrieves results from the FPGA's internal RAM banks.

### Encapsulation Agent
- Source File: drivers/encap.py
- Functionality:
+ Receives the public key **(h, s)** and plaintext message **m**.
+ Controls the hardware to generate the ciphertext **(u, v, d)**and the Shared Secret **(SS)**.
+ Manages 128-bit data transfers to the IP core's control registers.

### Decapsulation Agent
- Source File: drivers/decap.py
- Functionality:
+ Loads the ciphertext **(u, v, d)** and the secret key into the designated RAM regions.
+ Initiates the decryption process to recover the Shared Secret.
+ Monitors the hardware Status Register to verify task completion.


## Code Conventions

- Follow **PEP 8**.
- Each app should have its own `base.html` template.
- Avoid using helper function and complex structures when something can easily be solved with a single function.


## Operational Workflow

### Initialization and Web Server Execution:
Activate the Python 3.11 virtual environment on Windows
`.\venv\Scripts\Activate.ps1`
`python app.py`

Activate the virtual environment on Linux (Kria KV260)
`source venv/bin/activate`
`sudo python3 app.py`

### Hardware Communication:
- User interactions on the web UI trigger specific functions within the HQCDriver classes.
- The driver loads the hqc_accelerator.bit bitstream into the Programmable Logic (PL).
- Data exchange occurs via defined MMIO offsets (CTRL, ADDR, WDATA, RDATA).

### Result Rendering: Computed data is processed and rendered onto the HTML interface for real-time visualization.


## Technical Constraints

- Performance: Optimized for the ARM processor on the Kria KV260 by minimizing redundant Overlay loading and using efficient array access methods.