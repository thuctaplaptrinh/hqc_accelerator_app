from __future__ import annotations

import os
from functools import lru_cache

from flask import Flask, flash, render_template, request

from drivers.decap import D_DEPTH, N_MEM_WIDTH as DECAP_N_MEM_WIDTH, Y_DEPTH, HQCDecapDriver
from drivers.encap import MSG_DEPTH, N_MEM_WIDTH as ENCAP_N_MEM_WIDTH, HQCEncapDriver
from drivers.keygen import SEED_WORDS, HQCKeygenDriver


BITFILE_PATH = os.getenv("HQC_BITFILE", "hqc_accelerator.bit")
APP_SECRET = os.getenv("APP_SECRET_KEY", "hqc-demo-secret")

app = Flask(__name__)
app.secret_key = APP_SECRET


@lru_cache(maxsize=1)
def _get_keygen_driver() -> HQCKeygenDriver:
    # Cache one instance to avoid redundant bitstream reloading.
    return HQCKeygenDriver(BITFILE_PATH)


@lru_cache(maxsize=1)
def _get_encap_driver() -> HQCEncapDriver:
    # Cache one instance to avoid redundant bitstream reloading.
    return HQCEncapDriver(BITFILE_PATH)


@lru_cache(maxsize=1)
def _get_decap_driver() -> HQCDecapDriver:
    # Cache one instance to avoid redundant bitstream reloading.
    return HQCDecapDriver(BITFILE_PATH)


def _normalize_hex_token(token: str) -> str:
    cleaned = token.strip()
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if not cleaned:
        raise ValueError("Empty token is not a valid hex value.")
    int(cleaned, 16)
    return cleaned


def _parse_hex_list(raw_text: str, expected_count: int) -> list[int]:
    separators_replaced = raw_text.replace("\n", " ").replace(",", " ").replace(";", " ")
    tokens = [tok for tok in separators_replaced.split() if tok]
    if len(tokens) != expected_count:
        raise ValueError(f"Expected {expected_count} values, got {len(tokens)}.")
    return [int(_normalize_hex_token(tok), 16) for tok in tokens]


def _to_hex_list(values: list[int], width_bits: int) -> list[str]:
    width = width_bits // 4
    return [f"0x{value:0{width}x}" for value in values]


def _to_hex_blob(values: list[int], width_bits: int) -> str:
    return "\n".join(_to_hex_list(values, width_bits))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/keygen", methods=["POST"])
def keygen():
    sk_seed_text = request.form.get("sk_seed", "")
    pk_seed_text = request.form.get("pk_seed", "")

    try:
        sk_seed = _parse_hex_list(sk_seed_text, SEED_WORDS)
        pk_seed = _parse_hex_list(pk_seed_text, SEED_WORDS)
        result = _get_keygen_driver().run_keygen(sk_seed=sk_seed, pk_seed=pk_seed)
    except Exception as exc:
        flash(f"Keygen failed: {exc}", "danger")
        return render_template("index.html", active_tab="keygen")

    flash("Key generation completed successfully.", "success")
    return render_template(
        "index.html",
        active_tab="keygen",
        keygen_inputs={
            "sk_seed": sk_seed_text,
            "pk_seed": pk_seed_text,
        },
        keygen_result={
            "h": _to_hex_blob(result["h"], 128),
            "s": _to_hex_blob(result["s"], 128),
            "x": _to_hex_blob(result["x"], 16),
            "y": _to_hex_blob(result["y"], 16),
        },
    )


@app.route("/encap", methods=["POST"])
def encap():
    h_text = request.form.get("h_data", "")
    s_text = request.form.get("s_data", "")
    msg_text = request.form.get("msg_words", "")

    try:
        h_data = _parse_hex_list(h_text, ENCAP_N_MEM_WIDTH)
        s_data = _parse_hex_list(s_text, ENCAP_N_MEM_WIDTH)
        msg_words = _parse_hex_list(msg_text, MSG_DEPTH)
        result = _get_encap_driver().run_encap(h_data=h_data, s_data=s_data, msg_words=msg_words)
    except Exception as exc:
        flash(f"Encapsulation failed: {exc}", "danger")
        return render_template("index.html", active_tab="encap")

    flash("Encapsulation completed successfully.", "success")
    return render_template(
        "index.html",
        active_tab="encap",
        encap_inputs={
            "h_data": h_text,
            "s_data": s_text,
            "msg_words": msg_text,
        },
        encap_result={
            "ss": f"0x{result['ss'].hex()}",
            "d": _to_hex_blob(result["d"], 32),
            "u": _to_hex_blob(result["u"], 128),
            "v": _to_hex_blob(result["v"], 128),
        },
    )


@app.route("/decap", methods=["POST"])
def decap():
    h_text = request.form.get("h_data_decap", "")
    s_text = request.form.get("s_data_decap", "")
    u_text = request.form.get("u_data", "")
    v_text = request.form.get("v_data", "")
    y_text = request.form.get("y_words", "")
    d_text = request.form.get("d_words", "")

    try:
        h_data = _parse_hex_list(h_text, DECAP_N_MEM_WIDTH)
        s_data = _parse_hex_list(s_text, DECAP_N_MEM_WIDTH)
        u_data = _parse_hex_list(u_text, DECAP_N_MEM_WIDTH)
        v_data = _parse_hex_list(v_text, DECAP_N_MEM_WIDTH)
        y_words = _parse_hex_list(y_text, Y_DEPTH)
        d_words = _parse_hex_list(d_text, D_DEPTH)
        ss = _get_decap_driver().run_decap(
            h_data=h_data,
            s_data=s_data,
            u_data=u_data,
            v_data=v_data,
            y_words=y_words,
            d_words=d_words,
        )
    except Exception as exc:
        flash(f"Decapsulation failed: {exc}", "danger")
        return render_template("index.html", active_tab="decap")

    flash("Decapsulation completed successfully.", "success")
    return render_template(
        "index.html",
        active_tab="decap",
        decap_inputs={
            "h_data_decap": h_text,
            "s_data_decap": s_text,
            "u_data": u_text,
            "v_data": v_text,
            "y_words": y_text,
            "d_words": d_text,
        },
        decap_result={"ss": f"0x{ss.hex()}"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)