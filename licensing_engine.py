# -*- coding: utf-8 -*-
import os
import json
import base64
import hashlib
import hmac
from datetime import datetime, timedelta

# Asymmetric Ed25519 Keys
# The Sales Hub holds the PRIVATE KEY exclusively for generating official medical licenses.
# Client apps (clinic system) only possess the PUBLIC KEY for cryptographic verification.
ED25519_PRIVATE_KEY_B64 = "xB3A4L+WausHrkue1AuA7j5PqniG3eddO+I8MKGJqrA="
ED25519_PUBLIC_KEY_B64 = "GMGx6MuvR9wExI3VAp46IbEfPOLHveZkfMoC5ryZLbU="

# Legacy HMAC key for backward compatibility fallback in clinic/licensing.py
LEGACY_HMAC_SECRET = "AL-TABEEB-CLINIC-SYS-MASTER-SIG-2026-SECURE-KEY-v1"


def generate_signed_license(
    machine_id: str,
    client_name: str = "عيادة طبية",
    license_type: str = "FULL",
    duration_type: str = "LIFETIME",
    custom_days: int = None,
    max_doctors: int = None,
    specialty: str = "عام"
) -> tuple:
    """
    Generates a cryptographically authentic medical software license token
    fully compatible with Al-Tabeeb Clinic System licensing engine.
    Returns: (token_str, expires_at_str, payload_dict)
    """
    mid = machine_id.strip().upper()
    lic_type = license_type.upper()
    
    expires_at = None
    now_dt = datetime.now()
    
    # Calculate expiration based on duration_type
    dur = duration_type.upper()
    if dur == "LIFETIME":
        expires_at = None
    elif dur in ("YEARLY", "1_YEAR"):
        expires_at = (now_dt + timedelta(days=365)).strftime("%Y-%m-%d 23:59:59")
    elif dur in ("180_DAYS", "6_MONTHS"):
        expires_at = (now_dt + timedelta(days=180)).strftime("%Y-%m-%d 23:59:59")
    elif dur in ("90_DAYS", "3_MONTHS"):
        expires_at = (now_dt + timedelta(days=90)).strftime("%Y-%m-%d 23:59:59")
    elif dur in ("MONTHLY", "30_DAYS"):
        expires_at = (now_dt + timedelta(days=30)).strftime("%Y-%m-%d 23:59:59")
    elif dur == "CUSTOM" and custom_days:
        expires_at = (now_dt + timedelta(days=int(custom_days))).strftime("%Y-%m-%d 23:59:59")

    payload = {
        "machine_id": mid,
        "client_name": client_name.strip(),
        "license_type": lic_type,
        "specialty": specialty.strip(),
        "created_at": now_dt.strftime("%Y-%m-%d %H:%M:%S")
    }

    if expires_at:
        payload["expires_at"] = expires_at
    if max_doctors is not None:
        payload["max_doctors"] = max_doctors

    # Pack payload
    b64_payload = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('utf-8')
    
    # Generate signature (Ed25519 first, fallback to HMAC-SHA256)
    sig = None
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        priv_bytes = base64.b64decode(ED25519_PRIVATE_KEY_B64)
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
        sig_raw = private_key.sign(b64_payload.encode('utf-8'))
        sig = base64.urlsafe_b64encode(sig_raw).decode('utf-8')
    except Exception:
        sig = hmac.new(LEGACY_HMAC_SECRET.encode('utf-8'), b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()

    token = f"{b64_payload}.{sig}"
    return token, expires_at, payload


def verify_license_token(token: str) -> tuple:
    """Verifies a generated license token for integrity check."""
    if not token or "." not in token:
        return False, None, "تنسيق الرمز غير صالح"
    try:
        b64_payload, sig = token.strip().split(".", 1)
        is_valid = False
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            pub_bytes = base64.b64decode(ED25519_PUBLIC_KEY_B64)
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            sig_raw = base64.urlsafe_b64decode(sig.encode('utf-8'))
            pub_key.verify(sig_raw, b64_payload.encode('utf-8'))
            is_valid = True
        except Exception:
            expected = hmac.new(LEGACY_HMAC_SECRET.encode('utf-8'), b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected):
                is_valid = True

        if not is_valid:
            return False, None, "توقيع الترخيص غير صالح"

        data = json.loads(base64.urlsafe_b64decode(b64_payload.encode('utf-8')).decode('utf-8'))
        return True, data, "الترخيص معتمد وسليم"
    except Exception as e:
        return False, None, str(e)
