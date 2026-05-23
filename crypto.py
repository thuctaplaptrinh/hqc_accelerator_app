"""AES-256-GCM encryption and decryption for KEM/DEM hybrid demo."""

from __future__ import annotations

import os

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import HKDF
from Crypto.Hash import SHA256

KEM_KDF_SALT = b"HQC-KEM/DEM-v1"
KEM_KDF_INFO = b"AES-256-GCM-key"

def derive_aes_key(shared_secret: bytes) -> bytes:
    """Derive a 32-byte AES-256 key from HQC Shared Secret using HKDF-SHA256.
    
    HQC hardware may return SS of varying length (64 bytes for HQC-128).
    HKDF safely condenses this into a fixed 32-byte key.
    """
    return HKDF(
        master=shared_secret,
        key_len=32,
        salt=KEM_KDF_SALT,
        hashmod=SHA256,
        context=KEM_KDF_INFO,
    )



def aes_gcm_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes, bytes]:
    """Encrypt plaintext using AES-256-GCM.

    Args:
        key: 32-byte (256-bit) symmetric key.
        plaintext: Arbitrary-length data to encrypt.

    Returns:
        Tuple of (ciphertext, nonce, tag).
        - ciphertext: Same length as plaintext.
        - nonce: 12-byte random nonce.
        - tag: 16-byte GCM authentication tag.
    """
    if len(key) != 32:
        raise ValueError(f"Key must be 32 bytes, got {len(key)}.")

    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return ciphertext, nonce, tag


def aes_gcm_decrypt(key: bytes, ciphertext: bytes, nonce: bytes, tag: bytes) -> bytes:
    """Decrypt ciphertext using AES-256-GCM and verify authentication tag.

    Args:
        key: 32-byte (256-bit) symmetric key.
        ciphertext: Data to decrypt.
        nonce: 12-byte nonce used during encryption.
        tag: 16-byte GCM authentication tag.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        ValueError: If tag verification fails (data tampered).
    """
    if len(key) != 32:
        raise ValueError(f"Key must be 32 bytes, got {len(key)}.")

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return plaintext
