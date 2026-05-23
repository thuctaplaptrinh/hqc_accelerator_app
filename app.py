from __future__ import annotations

import base64
import os
import secrets
import time
import threading
import uuid
from functools import lru_cache
from flask import Flask, jsonify, render_template, request

# Import crypto utilities
from crypto import aes_gcm_decrypt, aes_gcm_encrypt, derive_aes_key

# ---------------------------------------------------------------------------
# Fallback Drivers for Windows / Local PC Testing without PYNQ
# ---------------------------------------------------------------------------
HAS_PYNQ = False
try:
    import pynq
    HAS_PYNQ = True
except ImportError:
    print("[HQC] 'pynq' module not found. Using Mock Drivers for local PC development.")

# Constants
SEED_WORDS = 10
MSG_DEPTH = 4
Y_DEPTH = 66
D_DEPTH = 16
BITFILE_PATH = os.getenv("HQC_BITFILE", "hqc_accelerator.bit")
APP_SECRET = os.getenv("APP_SECRET_KEY", "hqc-demo-secret")
DEMO_IMAGE = os.path.join("static", "assets", "meo.png")

# Define Mock Drivers
class MockKeygenDriver:
    def __init__(self, bitfile_path: str):
        self.bitfile_path = bitfile_path
    def run_keygen(self, sk_seed: list[int], pk_seed: list[int]) -> dict:
        print("[HQC Mock] Running key generation...")
        time.sleep(1.5)  # Simulate FPGA processing time
        return {
            "h": [secrets.randbits(128) for _ in range(139)],
            "s": [secrets.randbits(128) for _ in range(139)],
            "x": [secrets.randbits(15) for _ in range(66)],
            "y": [secrets.randbits(15) for _ in range(66)]
        }

class MockEncapDriver:
    def __init__(self, bitfile_path: str):
        self.bitfile_path = bitfile_path
    def run_encap(self, h_data: list[int], s_data: list[int], msg_words: list[int]) -> dict:
        print("[HQC Mock] Running encapsulation...")
        time.sleep(2.0)  # Simulate FPGA processing time
        ss = secrets.token_bytes(64)  # 512-bit HQC Shared Secret
        return {
            "ss": ss,
            "u": [secrets.randbits(128) for _ in range(139)],
            "v": [secrets.randbits(128) for _ in range(139)],
            "d": [secrets.randbits(32) for _ in range(16)]
        }

class MockDecapDriver:
    def __init__(self, bitfile_path: str):
        self.bitfile_path = bitfile_path
    def run_decap(self, h_data: list[int], s_data: list[int], u_data: list[int], v_data: list[int], y_words: list[int], d_words: list[int]) -> bytes:
        print("[HQC Mock] Running decapsulation...")
        time.sleep(1.8)  # Simulate FPGA processing time
        # Return mock shared secret
        if demo_state.get("mock_ss"):
            return demo_state["mock_ss"]
        return secrets.token_bytes(64)

# Bind Drivers
if HAS_PYNQ:
    try:
        from drivers.keygen import HQCKeygenDriver
        from drivers.encap import HQCEncapDriver
        from drivers.decap import HQCDecapDriver
    except Exception as exc:
        print(f"[HQC] Failed to import real hardware drivers: {exc}. Falling back to mocks.")
        HQCKeygenDriver = MockKeygenDriver
        HQCEncapDriver = MockEncapDriver
        HQCDecapDriver = MockDecapDriver
else:
    HQCKeygenDriver = MockKeygenDriver
    HQCEncapDriver = MockEncapDriver
    HQCDecapDriver = MockDecapDriver

# ---------------------------------------------------------------------------
# Flask Application Setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = APP_SECRET

# Single-user in-memory store for the hybrid demonstration state
demo_state: dict = {}

def _reset_state() -> None:
    demo_state.clear()
    demo_state.update({
        "pk": None,
        "sk": None,
        "K": None,          # 32-byte derived symmetric key
        "c_kem": None,
        "encrypted": None,
        "K_prime": None,    # 32-byte derived recovered key
        "mock_ss": None,    # Used to pass mock SS between Encap & Decap in mock mode
    })

_reset_state()

# ---------------------------------------------------------------------------
# Background Thread / Job Polling Infrastructure
# ---------------------------------------------------------------------------
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
hw_lock = threading.Lock()  # Serialize hardware interface operations

def _create_job(job_id: str) -> None:
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "started_at": time.time(),
            "completed_at": None,
            "hw_time_ms": 0.0
        }

def _update_job_status(job_id: str, status: str, result: any = None, error: str = None, hw_time_ms: float = 0.0) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = status
            jobs[job_id]["result"] = result
            jobs[job_id]["error"] = error
            jobs[job_id]["completed_at"] = time.time()
            jobs[job_id]["hw_time_ms"] = hw_time_ms

# ---------------------------------------------------------------------------
# Driver Instance Cache (Singletons)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_keygen_driver() -> HQCKeygenDriver:
    return HQCKeygenDriver(BITFILE_PATH)

@lru_cache(maxsize=1)
def _get_encap_driver() -> HQCEncapDriver:
    return HQCEncapDriver(BITFILE_PATH)

@lru_cache(maxsize=1)
def _get_decap_driver() -> HQCDecapDriver:
    return HQCDecapDriver(BITFILE_PATH)

# ---------------------------------------------------------------------------
# Format Helpers
# ---------------------------------------------------------------------------
def _to_hex_list(values: list[int], width_bits: int) -> list[str]:
    width = width_bits // 4
    return [f"0x{v:0{width}x}" for v in values]

def _parse_hex_list(items: list[str]) -> list[int]:
    result = []
    for token in items:
        cleaned = token.strip()
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        result.append(int(cleaned, 16))
    return result

def _strip_hex_prefix(hex_str: str) -> str:
    return hex_str[2:] if hex_str.lower().startswith("0x") else hex_str

def _hex_to_bytes(hex_str: str, expected_len: int, field_name: str) -> bytes:
    raw = _strip_hex_prefix(hex_str)
    try:
        result = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid hex string for '{field_name}': {exc}") from exc
    if len(result) != expected_len:
        raise ValueError(
            f"'{field_name}' must be {expected_len} bytes, got {len(result)}."
        )
    return result

# ---------------------------------------------------------------------------
# Background Task Runners
# ---------------------------------------------------------------------------
def _async_keygen_task(job_id: str, sk_seed: list[int], pk_seed: list[int]) -> None:
    _update_job_status(job_id, "running")
    try:
        t0 = time.time()
        # Acquire FPGA access lock to protect underlying overlay registers
        with hw_lock:
            driver = _get_keygen_driver()
            result = driver.run_keygen(sk_seed=sk_seed, pk_seed=pk_seed)
        elapsed_ms = (time.time() - t0) * 1000
        _update_job_status(job_id, "completed", result=result, hw_time_ms=elapsed_ms)
    except Exception as exc:
        _update_job_status(job_id, "failed", error=str(exc))

def _async_encap_task(job_id: str, h_data: list[int], s_data: list[int], msg_words: list[int]) -> None:
    _update_job_status(job_id, "running")
    try:
        t0 = time.time()
        with hw_lock:
            driver = _get_encap_driver()
            result = driver.run_encap(h_data=h_data, s_data=s_data, msg_words=msg_words)
        elapsed_ms = (time.time() - t0) * 1000
        _update_job_status(job_id, "completed", result=result, hw_time_ms=elapsed_ms)
    except Exception as exc:
        _update_job_status(job_id, "failed", error=str(exc))

def _async_decap_task(job_id: str, h_data: list[int], s_data: list[int], u_data: list[int], v_data: list[int], y_words: list[int], d_words: list[int]) -> None:
    _update_job_status(job_id, "running")
    try:
        t0 = time.time()
        with hw_lock:
            driver = _get_decap_driver()
            result_ss = driver.run_decap(
                h_data=h_data, s_data=s_data,
                u_data=u_data, v_data=v_data,
                y_words=y_words, d_words=d_words
            )
        elapsed_ms = (time.time() - t0) * 1000
        _update_job_status(job_id, "completed", result=result_ss, hw_time_ms=elapsed_ms)
    except Exception as exc:
        _update_job_status(job_id, "failed", error=str(exc))

# ---------------------------------------------------------------------------
# HTTP Web Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

# Job status check polling API
@app.route("/api/job/<job_id>", methods=["GET"])
def get_job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    
    # Base output response
    response_data = {
        "job_id": job_id,
        "status": job["status"],
        "error": job["error"]
    }

    if job["status"] == "completed":
        # Handle state mapping when completed
        result = job["result"]
        
        # Determine job type from the structure of the result
        if isinstance(result, dict) and "h" in result and "x" in result:
            # KeyGen Done
            demo_state["pk"] = {"h": result["h"], "s": result["s"]}
            demo_state["sk"] = {"x": result["x"], "y": result["y"]}
            
            response_data.update({
                "pk": {
                    "h": _to_hex_list(result["h"], 128),
                    "s": _to_hex_list(result["s"], 128)
                },
                "sk": {
                    "x": _to_hex_list(result["x"], 16),
                    "y": _to_hex_list(result["y"], 16)
                },
                "hw_time_ms": round(job["hw_time_ms"], 2)
            })
            
        elif isinstance(result, dict) and "ss" in result and "u" in result:
            # Encap Done
            # Derive 32-byte AES key from 64-byte HQC shared secret using HKDF
            ss_key = derive_aes_key(result["ss"])
            demo_state["K"] = ss_key
            demo_state["mock_ss"] = result["ss"] # Keep for mock verification
            demo_state["c_kem"] = {"u": result["u"], "v": result["v"], "d": result["d"]}
            
            response_data.update({
                "K": f"0x{ss_key.hex()}",
                "c_kem": {
                    "u": _to_hex_list(result["u"], 128),
                    "v": _to_hex_list(result["v"], 128),
                    "d": _to_hex_list(result["d"], 32)
                },
                "hw_time_ms": round(job["hw_time_ms"], 2)
            })
            
        elif isinstance(result, bytes):
            # Decap Done
            # Derive 32-byte AES key from recovered shared secret
            ss_prime = derive_aes_key(result)
            demo_state["K_prime"] = ss_prime
            
            keys_match = demo_state["K"] is not None and ss_prime == demo_state["K"]
            
            response_data.update({
                "K_prime": f"0x{ss_prime.hex()}",
                "keys_match": keys_match,
                "hw_time_ms": round(job["hw_time_ms"], 2)
            })

    return jsonify(response_data)

# --- Phase 0: Key Generation (Alice) --------------------------------------
@app.route("/api/keygen", methods=["POST"])
def api_keygen():
    data = request.get_json(force=True) or {}
    
    try:
        sk_seed = (_parse_hex_list(data["sk_seed"]) if data.get("sk_seed")
                   else [secrets.randbits(32) for _ in range(SEED_WORDS)])
        pk_seed = (_parse_hex_list(data["pk_seed"]) if data.get("pk_seed")
                   else [secrets.randbits(32) for _ in range(SEED_WORDS)])
    except Exception as exc:
        return jsonify({"error": f"Invalid seed format: {exc}"}), 400

    job_id = str(uuid.uuid4())
    _create_job(job_id)
    
    # Spawn background worker thread
    thread = threading.Thread(
        target=_async_keygen_task,
        args=(job_id, sk_seed, pk_seed),
        daemon=True
    )
    thread.start()
    
    return jsonify({"job_id": job_id, "status": "pending"}), 202

# --- Phase 1: KEM Encapsulation (Bob) -------------------------------------
@app.route("/api/encap", methods=["POST"])
def api_encap():
    data = request.get_json(force=True) or {}
    
    try:
        if "h" in data and data["h"]:
            h_data = _parse_hex_list(data["h"])
        elif demo_state["pk"]:
            h_data = demo_state["pk"]["h"]
        else:
            return jsonify({"error": "No public key available. Run keygen first."}), 400

        if "s" in data and data["s"]:
            s_data = _parse_hex_list(data["s"])
        elif demo_state["pk"]:
            s_data = demo_state["pk"]["s"]
        else:
            return jsonify({"error": "No public key available. Run keygen first."}), 400

        msg_words = [secrets.randbits(32) for _ in range(MSG_DEPTH)]
    except Exception as exc:
        return jsonify({"error": f"Invalid public key data: {exc}"}), 400

    job_id = str(uuid.uuid4())
    _create_job(job_id)

    thread = threading.Thread(
        target=_async_encap_task,
        args=(job_id, h_data, s_data, msg_words),
        daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202

# --- Phase 2: AES-GCM Encrypt (Bob — Software) ----------------------------
@app.route("/api/encrypt", methods=["POST"])
def api_encrypt():
    data = request.get_json(force=True) or {}

    try:
        if "K" in data and data["K"]:
            key = _hex_to_bytes(data["K"], 32, "K")
        elif demo_state["K"]:
            key = demo_state["K"]
        else:
            return jsonify({"error": "No shared secret available. Run encap first."}), 400

        if not os.path.exists(DEMO_IMAGE):
            # Create a simple default mockup PNG if it doesn't exist, to prevent breaking local testing
            os.makedirs(os.path.dirname(DEMO_IMAGE), exist_ok=True)
            # Write a solid transparent pixel or sample base64 png
            transparent_pixel = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x01\x00\x00\x0c\x00\x01\x04\x0b\xa0\xf9\x00\x00\x00\x00IEND\xaeB`\x82'
            with open(DEMO_IMAGE, "wb") as f:
                f.write(transparent_pixel)

        with open(DEMO_IMAGE, "rb") as f:
            plaintext = f.read()

        t0 = time.time()
        ciphertext, nonce, tag = aes_gcm_encrypt(key, plaintext)
        sw_time_ms = (time.time() - t0) * 1000

        original_b64 = base64.b64encode(plaintext).decode()
        encrypted_b64 = base64.b64encode(ciphertext).decode()

        demo_state["encrypted"] = {
            "data_b64": encrypted_b64,
            "nonce_hex": f"0x{nonce.hex()}",
            "tag_hex": f"0x{tag.hex()}",
        }

        return jsonify({
            "encrypted_b64": encrypted_b64,
            "nonce_hex": f"0x{nonce.hex()}",
            "tag_hex": f"0x{tag.hex()}",
            "original_preview_b64": original_b64,
            "file_size_bytes": len(plaintext),
            "sw_time_ms": round(sw_time_ms, 2),
        })

    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

# --- Phase 4: KEM Decapsulation (Alice) -----------------------------------
@app.route("/api/decap", methods=["POST"])
def api_decap():
    data = request.get_json(force=True) or {}

    try:
        pk = demo_state.get("pk")
        sk = demo_state.get("sk")
        kem = demo_state.get("c_kem")

        # Parse h
        if "h" in data and data["h"]:
            h_data = _parse_hex_list(data["h"])
        elif pk:
            h_data = pk["h"]
        else:
            return jsonify({"error": "No public key h available. Run keygen first."}), 400

        # Parse s
        if "s" in data and data["s"]:
            s_data = _parse_hex_list(data["s"])
        elif pk:
            s_data = pk["s"]
        else:
            return jsonify({"error": "No public key s available. Run keygen first."}), 400

        # Parse u
        if "u" in data and data["u"]:
            u_data = _parse_hex_list(data["u"])
        elif kem:
            u_data = kem["u"]
        else:
            return jsonify({"error": "No ciphertext u available. Run encap first."}), 400

        # Parse v
        if "v" in data and data["v"]:
            v_data = _parse_hex_list(data["v"])
        elif kem:
            v_data = kem["v"]
        else:
            return jsonify({"error": "No ciphertext v available. Run encap first."}), 400

        # Parse y
        if "y" in data and data["y"]:
            y_words = _parse_hex_list(data["y"])
        elif sk:
            y_words = sk["y"]
        else:
            return jsonify({"error": "No secret key y available. Run keygen first."}), 400

        # Parse d
        if "d" in data and data["d"]:
            d_words = _parse_hex_list(data["d"])
        elif kem:
            d_words = kem["d"]
        else:
            return jsonify({"error": "No ciphertext d available. Run encap first."}), 400

    except Exception as exc:
        return jsonify({"error": f"Invalid hardware parameters: {exc}"}), 400

    job_id = str(uuid.uuid4())
    _create_job(job_id)

    thread = threading.Thread(
        target=_async_decap_task,
        args=(job_id, h_data, s_data, u_data, v_data, y_words, d_words),
        daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202

# --- Phase 5: AES-GCM Decrypt (Alice — Software) --------------------------
@app.route("/api/decrypt", methods=["POST"])
def api_decrypt():
    data = request.get_json(force=True) or {}

    try:
        # Retrieve recovered key K_prime
        if "K_prime" in data and data["K_prime"]:
            key = _hex_to_bytes(data["K_prime"], 32, "K_prime")
        elif demo_state["K_prime"]:
            key = demo_state["K_prime"]
        else:
            return jsonify({"error": "No recovered secret key. Run decap first."}), 400

        # Retrieve ciphertext
        if "encrypted_b64" in data and data["encrypted_b64"]:
            ciphertext = base64.b64decode(data["encrypted_b64"])
        elif demo_state["encrypted"]:
            ciphertext = base64.b64decode(demo_state["encrypted"]["data_b64"])
        else:
            return jsonify({"error": "No encrypted data available. Run encrypt first."}), 400

        # Retrieve nonce
        if "nonce_hex" in data and data["nonce_hex"]:
            nonce = _hex_to_bytes(data["nonce_hex"], 12, "nonce")
        elif demo_state["encrypted"]:
            nonce = _hex_to_bytes(demo_state["encrypted"]["nonce_hex"], 12, "nonce")
        else:
            return jsonify({"error": "No nonce available. Run encrypt first."}), 400

        # Retrieve authentication tag
        if "tag_hex" in data and data["tag_hex"]:
            tag = _hex_to_bytes(data["tag_hex"], 16, "tag")
        elif demo_state["encrypted"]:
            tag = _hex_to_bytes(demo_state["encrypted"]["tag_hex"], 16, "tag")
        else:
            return jsonify({"error": "No auth tag available. Run encrypt first."}), 400

        t0 = time.time()
        plaintext = aes_gcm_decrypt(key, ciphertext, nonce, tag)
        sw_time_ms = (time.time() - t0) * 1000

        decrypted_b64 = base64.b64encode(plaintext).decode()

        return jsonify({
            "decrypted_image_b64": decrypted_b64,
            "tag_valid": True,
            "sw_time_ms": round(sw_time_ms, 2),
        })

    except ValueError as val_err:
        return jsonify({
            "decrypted_image_b64": None,
            "tag_valid": False,
            "error": f"Tag verification failed: {val_err}",
        }), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

# --- Utility endpoints -----------------------------------------------------
@app.route("/api/reset", methods=["POST"])
def api_reset():
    _reset_state()
    # Reset job queue
    with jobs_lock:
        jobs.clear()
    return jsonify({"status": "ok", "message": "Demo state cleared."})

@app.route("/api/status", methods=["GET"])
def api_status():
    phases_done = {
        "phase0_keygen": demo_state.get("pk") is not None,
        "phase1_encap": demo_state.get("K") is not None,
        "phase2_encrypt": demo_state.get("encrypted") is not None,
        "phase4_decap": demo_state.get("K_prime") is not None,
    }
    return jsonify({
        "bitfile": BITFILE_PATH,
        "demo_image": DEMO_IMAGE,
        "demo_image_exists": os.path.exists(DEMO_IMAGE),
        "phases_done": phases_done,
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)