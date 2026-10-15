#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import random
import string
import re
import base64
import struct
import hashlib
import zlib
import uuid
import sqlite3
import threading
import asyncio
import logging
import requests
import urllib3
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═══════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("\n⚠️  pycryptodome yüklü değil! Kurmak için:")
    print("   pip install pycryptodome\n")
    sys.exit(1)

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False
    print("\n⚠️  brotli yüklü değil! Kurmak için:")
    print("   pip install brotli\n")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# 🔑 API KEYS & SETTINGS
# ═══════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8874961058:AAHN_BJ2dkeqro_MOMpidWCdV2_qTfaiphY")

FK = "AIzaSyAe_aOVT1gSfmHKBrorFvX4fRwN5nODXVA"
LOAD_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/GetPlayerRecords3"
SAVE_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/SavePlayerRecordsPartially8"

CPM_EU = "https://europe-west1-cp-multiplayer.cloudfunctions.net"
CPM_CARS_FETCH_URL = f"{CPM_EU}/GetAllCars2"
CPM_CARS_SAVE_URL = f"{CPM_EU}/SaveCarsPartially8"

GAME_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
    "X-Unity-Version": "2022.3.62f2",
}

# ═══════════════════════════════════════════════════════════
# 👑 ADMIN & MEMBERSHIP SETTINGS
# ═══════════════════════════════════════════════════════════

ADMIN_ID = 8882473760
CONTACT_USERNAME = "@derincpm"

PRIM_1_MONTH = 350
PRIM_2_MONTH = 600
VIP_1_MONTH = 500
VIP_2_MONTH = 900

PRIM_DAILY_LIMIT = 2
VIP_DAILY_LIMIT = 999999

DB_DIR = os.path.expanduser("~/.cpm1_termux")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "cpm_tokens.db")

# Sonuç dosyaları buraya kaydedilir: ~/.cpm1_termux/results/<user_id>/
RESULTS_DIR = os.path.join(DB_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

http_session = requests.Session()
http_session.verify = True
_cpm_thread_local = threading.local()


def _get_cpm_http_session():
    session = getattr(_cpm_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.verify = True
        _cpm_thread_local.session = session
    return session


# ═══════════════════════════════════════════════════════════
# 🔐 ENCRYPTION FUNCTIONS
# ═══════════════════════════════════════════════════════════

def make_xor_key(uid: str) -> bytes:
    chars = list(str(uid or ""))
    if len(chars) >= 9:
        chars[1], chars[8] = chars[8], chars[1]
    if len(chars) >= 3:
        chars.pop(2)
    if len(chars) >= 5:
        chars.append(chars[4])
    key = "".join(chars).encode("utf-8")
    return key or b"0"


def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def decompress(data: bytes):
    if HAS_BROTLI:
        try:
            return brotli.decompress(data)
        except Exception:
            pass
    for args in ((zlib.MAX_WBITS | 16,), tuple()):
        try:
            return zlib.decompress(data, *args)
        except Exception:
            pass
    return None


def decrypt_aes(data: bytes, key: bytes):
    if not HAS_CRYPTO:
        return None
    try:
        cipher = AES.new(key[:16], AES.MODE_CBC, b"\x00" * 16)
        return unpad(cipher.decrypt(data), 16)
    except Exception:
        return None


def _md5(text: str) -> bytes:
    return hashlib.md5(str(text).encode()).digest()


def _sha1(text: str) -> bytes:
    return hashlib.sha1(str(text).encode()).digest()[:16]


def build_aes_keys(uid: str, password: str = None, email: str = None) -> list:
    keys = [_md5("olzhas_carparking")]
    if password:
        keys.extend([_md5(password), _sha1(password)])
    if uid:
        keys.extend([_md5(uid), _sha1(uid)])
    if email:
        keys.append(_md5(email))
    return keys


# ═══════════════════════════════════════════════════════════
# 📖 READER / WRITER CLASSES
# ═══════════════════════════════════════════════════════════

class Reader:
    def __init__(self, data: bytes):
        self.buf = data
        self.pos = 0

    def has_bytes(self, n: int) -> bool:
        return self.pos + n <= len(self.buf)

    def read_byte(self) -> int:
        if not self.has_bytes(1):
            return 0
        value = self.buf[self.pos]
        self.pos += 1
        return value

    def read_int(self) -> int:
        if not self.has_bytes(4):
            self.pos = len(self.buf)
            return 0
        value = struct.unpack_from("<i", self.buf, self.pos)[0]
        self.pos += 4
        return value

    def read_float(self) -> float:
        if not self.has_bytes(4):
            self.pos = len(self.buf)
            return 0.0
        value = struct.unpack_from("<f", self.buf, self.pos)[0]
        self.pos += 4
        return value

    def read_string(self) -> str:
        marker = self.read_int()
        if marker in (0, -1):
            return ""
        length = (-marker) - 1 if marker < -1 else marker
        if marker < -1:
            self.read_int()
        length = max(0, min(length, 1000000))
        if not self.has_bytes(length):
            return ""
        text = self.buf[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return text.replace("\x00", "").strip()

    def read_list(self, item_fn):
        count = self.read_int()
        if count <= 0 or count > 1000000:
            return []
        result = []
        for _ in range(count):
            if self.pos >= len(self.buf):
                break
            value = item_fn()
            if value is not None:
                result.append(value)
        return result

    def read_dict(self) -> dict:
        count = self.read_int()
        if count <= 0 or count > 1000000:
            return {}
        result = {}
        for _ in range(count):
            if self.pos >= len(self.buf):
                break
            result[self.read_int()] = self.read_int()
        return result

    def read_equipment(self):
        if self.read_byte() == 0:
            return None
        return {
            "hair": self.read_list(self.read_int),
            "face": self.read_list(self.read_int),
            "beard": self.read_list(self.read_int),
            "cap": self.read_list(self.read_int),
            "mask": self.read_list(self.read_int),
            "top": self.read_list(self.read_int),
            "gloves": self.read_list(self.read_int),
            "bag": self.read_list(self.read_int),
            "pants": self.read_list(self.read_int),
            "shoes": self.read_list(self.read_int),
            "glasses": self.read_list(self.read_int),
            "SelectedEquipments": self.read_list(self.read_int),
            "Gender": self.read_int(),
        }


def parse_player(buf: bytes) -> dict:
    r = Reader(buf)
    if r.read_byte() == 0:
        return None
    player = {}
    player["Name"] = r.read_string()
    player["money"] = r.read_int()
    player["coin"] = r.read_int()
    player["localID"] = r.read_string()
    player["boughtFsos"] = r.read_list(r.read_int)

    def read_friend():
        r.read_byte()
        return {"id": r.read_string(), "Name": r.read_string(), "accountID": r.read_string()}

    player["FriendsID"] = r.read_list(read_friend)
    player["LevelsDoneTime"] = r.read_list(r.read_float)
    player["floats"] = r.read_list(r.read_float)
    player["integers"] = r.read_list(r.read_int)
    player["fcar"] = r.read_list(r.read_int)
    player["favouriteWheels"] = r.read_list(r.read_int)
    player["favouriteVinyls"] = r.read_list(r.read_int)
    player["favouriteEmojis"] = r.read_list(r.read_int)
    player["personEquipmentsMale"] = r.read_equipment()
    player["personEquipmentsFemale"] = r.read_equipment()

    if r.read_byte() == 0:
        player["platesData"] = None
    else:
        def read_vinyl():
            vinyl_fc = r.read_byte()

            def rv():
                return {"x": r.read_float(), "y": r.read_float(), "z": r.read_float()}

            return {
                "_fieldCount": vinyl_fc,
                "vectors": r.read_list(rv),
                "v": r.read_list(r.read_string),
                "floats": r.read_list(r.read_float),
                "text": r.read_string()
            }

        def read_plate():
            plate_fc = r.read_byte()
            return {
                "_fieldCount": plate_fc,
                "plateId": r.read_int(),
                "frontCarId": r.read_int(),
                "rearCarId": r.read_int(),
                "vinyls": r.read_list(read_vinyl)
            }

        player["platesData"] = {"allPlates": r.read_list(read_plate)}

    if r.read_byte() == 0:
        player["carIDnStatus"] = None
    else:
        player["carIDnStatus"] = {
            "carGeneratedIDs": r.read_list(r.read_string),
            "carStatus": r.read_list(r.read_int),
        }
    player["allData"] = r.read_string()
    player["flags"] = r.read_dict()
    player["animations"] = r.read_list(r.read_int)
    player["emojiPacks"] = r.read_list(r.read_int)
    player["wheels"] = r.read_list(r.read_int)
    player["boughtPoliceLights"] = r.read_list(r.read_int)
    player["boughtPoliceSirens"] = r.read_list(r.read_int)
    return player


def try_parse(buf: bytes) -> dict:
    candidates = [buf]
    first = decompress(buf)
    if first:
        candidates.append(first)
        second = decompress(first)
        if second:
            candidates.append(second)
    for candidate in candidates:
        if not candidate:
            continue
        if candidate and candidate[0] in (17, 23, 24):
            try:
                parsed = parse_player(candidate)
                if parsed and parsed.get("Name") is not None:
                    return parsed
            except Exception:
                pass
        try:
            clean = candidate[3:] if len(candidate) >= 3 and candidate[:2] == b"\xef\xbb" else candidate
            if clean and clean[0] == 123:
                return json.loads(clean.decode("utf-8"))
        except Exception:
            pass
    return None


def decrypt_player_record(base64_text: str, uid: str, password: str = None, email: str = None) -> dict:
    try:
        buf = base64.b64decode(base64_text)
    except Exception:
        return {"success": False, "message": "Bad base64"}
    if len(buf) < 10:
        return {"success": False, "message": "Too small"}

    direct = try_parse(buf)
    if direct:
        return {"success": True, "record": direct}

    if uid:
        try:
            decoded = decompress(xor_bytes(buf, make_xor_key(uid)))
            if decoded:
                parsed = try_parse(decoded)
                if parsed:
                    return {"success": True, "record": parsed}
        except Exception:
            pass

    for key in build_aes_keys(uid or "", password, email):
        plain = decrypt_aes(buf, key)
        if not plain:
            continue
        parsed = try_parse(plain)
        if parsed:
            return {"success": True, "record": parsed}
    return {"success": False, "message": "Could not decrypt"}


class Writer:
    def __init__(self):
        self._p: List[bytes] = []

    def write_byte(self, v):
        self._p.append(bytes([int(v or 0) & 0xFF]))

    def write_int(self, v):
        self._p.append(struct.pack("<i", int(v or 0)))

    def write_float(self, v):
        self._p.append(struct.pack("<f", float(v or 0.0)))

    def write_string(self, s):
        if s is None:
            self._p.append(struct.pack("<i", -1))
            return
        s = str(s)
        if s == "":
            self._p.append(struct.pack("<i", 0))
            return
        enc = s.encode("utf-8")
        self._p.append(struct.pack("<ii", -(len(enc)) - 1, len(s)) + enc)

    def write_list(self, lst, fn):
        if lst is None:
            self._p.append(struct.pack("<i", -1))
            return
        self._p.append(struct.pack("<i", len(lst)))
        for item in lst:
            fn(item)

    def write_equipment(self, data):
        if not data:
            self.write_byte(0)
            return
        self.write_byte(13)
        for key in ["hair", "face", "beard", "cap", "mask", "top", "gloves", "bag", "pants", "shoes", "glasses",
                    "SelectedEquipments"]:
            self.write_list(data.get(key, []), self.write_int)
        self.write_int(data.get("Gender", 0))

    def write_plates(self, data):
        if not data:
            self.write_byte(0)
            return
        self.write_byte(1)
        plates = data.get("allPlates", [])
        self._p.append(struct.pack("<i", len(plates)))
        for plate in plates:
            plate_fc = int(plate.get("_fieldCount", 4) or 4)
            self.write_byte(plate_fc)
            self.write_int(plate.get("plateId", 0))
            self.write_int(plate.get("frontCarId", 0))
            self.write_int(plate.get("rearCarId", 0))
            vinyls = plate.get("vinyls", [])
            self._p.append(struct.pack("<i", len(vinyls)))
            for vinyl in vinyls:
                vinyl_fc = int(vinyl.get("_fieldCount", 4) or 4)
                self.write_byte(vinyl_fc)
                vecs = vinyl.get("vectors", [])
                self._p.append(struct.pack("<i", len(vecs)))
                for vec in vecs:
                    self._p.append(struct.pack("<fff",
                                               float(vec.get("x", 0) or 0),
                                               float(vec.get("y", 0) or 0),
                                               float(vec.get("z", 0) or 0)))
                self.write_list(vinyl.get("v", []), self.write_string)
                self.write_list(vinyl.get("floats", []), self.write_float)
                self.write_string(vinyl.get("text", ""))

    def write_car_id_status(self, data):
        if not data:
            self.write_byte(0)
            return
        self.write_byte(2)
        self.write_list(data.get("carGeneratedIDs", []), self.write_string)
        self.write_list(data.get("carStatus", []), self.write_int)

    def to_bytes(self):
        return b"".join(self._p)


FIELD_MAPPING = [
    (1, "localID"), (2, "money"), (3, "Name"), (4, "coin"), (5, "allData"),
    (6, "boughtFsos"), (7, "boughtPoliceLights"), (8, "boughtPoliceSirens"),
    (9, "FriendsID"), (10, "LevelsDoneTime"), (11, "floats"), (12, "integers"),
    (13, "fcar"), (14, "favouriteWheels"), (15, "favouriteVinyls"),
    (16, "favouriteEmojis"), (18, "emojiPacks"),
    (41, "personEquipmentsMale"), (42, "personEquipmentsFemale"),
    (43, "platesData"), (44, "carIDnStatus"), (45, "flags"),
    (46, "animations"), (48, "wheels"),
]
INT_LIST_FIELDS = {6, 7, 8, 12, 13, 14, 15, 16, 18, 46, 48}
FLOAT_LIST_FIELDS = {10, 11}
ALWAYS_SEND = {"allData"}


def _field_modified(new_value, old_value) -> bool:
    if new_value is None and old_value is None:
        return False
    if new_value is None or old_value is None:
        return True
    if type(new_value) != type(old_value):
        return True
    if isinstance(new_value, (dict, list)):
        return json.dumps(new_value, sort_keys=True) != json.dumps(old_value, sort_keys=True)
    return new_value != old_value


def serialize_field(fid: int, value: Any) -> Optional[bytes]:
    w = Writer()
    if fid in (1, 3, 5):
        w.write_string(value)
        return w.to_bytes()
    if fid in (2, 4):
        w.write_int(value or 0)
        return w.to_bytes()
    if fid == 9:
        friends = value or []
        w._p.append(struct.pack("<i", len(friends)))
        for friend in friends:
            friend = friend or {}
            w.write_byte(3)
            w.write_string(friend.get("id", ""))
            w.write_string(friend.get("Name", ""))
            w.write_string(friend.get("accountID", ""))
        return w.to_bytes()
    if fid in INT_LIST_FIELDS:
        w.write_list(value or [], w.write_int)
        return w.to_bytes()
    if fid in FLOAT_LIST_FIELDS:
        w.write_list(value or [], w.write_float)
        return w.to_bytes()
    if fid in (41, 42):
        w.write_equipment(value)
        return w.to_bytes()
    if fid == 43:
        w.write_plates(value)
        return w.to_bytes()
    if fid == 44:
        w.write_car_id_status(value)
        return w.to_bytes()
    if fid == 45:
        flags = value or {}
        w._p.append(struct.pack("<i", len(flags)))
        for key, val in flags.items():
            w.write_int(int(key))
            w.write_int(int(val))
        return w.to_bytes()
    return None


def build_payload(record: Dict[str, Any], uid: str, original: Optional[Dict[str, Any]] = None,
                  force_fields: Optional[set] = None) -> str:
    force_fields = set(force_fields or [])
    fields = []
    for fid, key in FIELD_MAPPING:
        value = record.get(key)
        if value is None:
            continue
        if key in ALWAYS_SEND:
            should_send = isinstance(value, str) and len(value) > 0
        elif key in force_fields:
            should_send = True
        elif original is not None:
            should_send = _field_modified(value, original.get(key))
        else:
            should_send = True
        if not should_send:
            continue
        raw = serialize_field(fid, value)
        if raw is not None:
            fields.append((fid, raw))

    parts = [struct.pack("<i", len(fields))]
    for fid, raw in fields:
        parts.append(struct.pack("<hi", fid, len(raw)))
        parts.append(raw)
    combined = b"".join(parts)
    if HAS_BROTLI:
        compressed = brotli.compress(combined)
    else:
        compressed = zlib.compress(combined)
    encrypted = xor_bytes(compressed, make_xor_key(uid))
    return base64.b64encode(encrypted).decode("ascii")


# ═══════════════════════════════════════════════════════════
# 📦 CPM1 Class
# ═══════════════════════════════════════════════════════════

class CPM1:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tokens (
                user_id INTEGER PRIMARY KEY,
                auth_token TEXT,
                email TEXT,
                password TEXT,
                refresh_token TEXT,
                firebase_uid TEXT,
                token_expires_at REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_data (
                cache_key TEXT PRIMARY KEY,
                email TEXT,
                data_json TEXT,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            c.commit()

    def _ck(self, uid: int, email: Optional[str] = None) -> str:
        if email:
            return f"{uid}_{email}"
        td = self.get_token_data(uid)
        return f"{uid}_{td['email']}" if td and td.get("email") else str(uid)

    def save_token(self, uid: int, auth: str, email: str, pw: Optional[str] = None,
                   rt: Optional[str] = None, fuid: Optional[str] = None):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""INSERT OR REPLACE INTO tokens
                (user_id, auth_token, email, password, refresh_token, firebase_uid, token_expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (uid, auth, email, pw, rt, fuid, time.time() + 3600))
            c.commit()

    def get_token_data(self, uid: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute("""SELECT auth_token, email, password, refresh_token, firebase_uid, token_expires_at
                               FROM tokens WHERE user_id=?""", (uid,)).fetchone()
        if not row:
            return None
        return {
            "auth_token": row[0], "email": row[1], "password": row[2],
            "refresh_token": row[3], "firebase_uid": row[4], "token_expires_at": row[5]
        }

    def get_record(self, uid: int, email: Optional[str] = None) -> Dict[str, Any]:
        ck = self._ck(uid, email)
        if ck not in self.cache:
            with sqlite3.connect(self.db_path) as c:
                row = c.execute("SELECT data_json FROM user_data WHERE cache_key=?", (ck,)).fetchone()
            if row:
                try:
                    self.cache[ck] = json.loads(row[0])
                except Exception:
                    pass
        return self.cache.get(ck, {})

    def set_record(self, uid: int, data: Dict[str, Any], email: Optional[str] = None):
        ck = self._ck(uid, email)
        self.cache[ck] = data
        with sqlite3.connect(self.db_path) as c:
            c.execute("INSERT OR REPLACE INTO user_data (cache_key, email, data_json) VALUES (?, ?, ?)",
                      (ck, email, json.dumps(data)))
            c.commit()

    def _post_sync(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        try:
            clean_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
            r = requests.post(url, json=payload, headers=clean_headers, timeout=60, verify=False)
            try:
                return r.json()
            except Exception:
                return {"raw": r.text, "status": r.status_code}
        except Exception as exc:
            logger.warning(f"HTTP error: {exc}")
            return None

    def login(self, email: str, password: str) -> Dict[str, Any]:
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FK}"
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "X-Unity-Version": "2022.3.62f2",
        }
        payload = {"email": email, "password": password, "returnSecureToken": True,
                   "clientType": "CLIENT_TYPE_ANDROID"}
        result = self._post_sync(url, payload, headers)
        if not result:
            return {"ok": False, "message": "NETWORK_ERROR"}
        if "idToken" in result:
            return {
                "ok": True,
                "message": "OK",
                "auth": result["idToken"],
                "refresh_token": result.get("refreshToken", ""),
                "firebase_uid": result.get("localId", ""),
            }
        err = str(result.get("error", {}).get("message", "")).upper()
        for key in ["EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "TOO_MANY_ATTEMPTS",
                    "USER_DISABLED", "INVALID_EMAIL"]:
            if key in err:
                return {"ok": False, "message": key}
        return {"ok": False, "message": f"LOGIN_FAILED: {err[:80]}"}

    def _ok(self, value: Any) -> bool:
        if value in (1, True):
            return True
        if value in (0, False, None):
            return False
        if isinstance(value, str):
            text = value.strip()
            if text == "1":
                return True
            if text == "0":
                return False
            try:
                return self._ok(json.loads(text))
            except Exception:
                return False
        if isinstance(value, dict):
            for key in ("result", "ok", "success"):
                if key in value:
                    return self._ok(value[key])
        return False

    def _send(self, auth: str, record: Dict[str, Any], fuid: str,
              original: Optional[Dict[str, Any]] = None,
              force_fields: Optional[set] = None) -> Tuple[bool, str]:
        if not fuid:
            return False, "NO_FIREBASE_UID"
        try:
            payload = build_payload(record, fuid, original, force_fields=force_fields)
            result = self._post_sync(
                SAVE_URL,
                {"data": {"data": payload, "deviceId": fuid[:8]}},
                {**GAME_HEADERS, "Authorization": f"Bearer {auth}", "Connection": "Keep-Alive",
                 "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)"},
            )
            if result and self._ok(result):
                return True, "OK"
            return False, f"SAVE_FAILED: {str(result)[:160]}"
        except Exception as exc:
            return False, str(exc)

    def create_account(self, email: str, password: str, display_name: str = "Player") -> Dict[str, Any]:
        email = (email or "").strip().lower()
        password = (password or "").strip()
        display_name = (display_name or "Player").strip()[:32] or "Player"

        if not email or "@" not in email or "." not in email.split("@")[-1]:
            return {"ok": False, "message": "INVALID_EMAIL"}
        if len(password) < 6:
            return {"ok": False, "message": "WEAK_PASSWORD"}

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FK}"
        h = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "X-Unity-Version": "2022.3.62f2",
        }
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True,
            "clientType": "CLIENT_TYPE_ANDROID",
        }
        try:
            r = requests.post(url, json=payload, headers=h, timeout=60, verify=False)
            try:
                data = r.json()
            except Exception:
                return {"ok": False, "message": "NETWORK_ERROR"}
        except Exception:
            return {"ok": False, "message": "NETWORK_ERROR"}

        if "idToken" not in data:
            err = str(data.get("error", {}).get("message", "")).upper()
            for k in ["EMAIL_EXISTS", "OPERATION_NOT_ALLOWED", "TOO_MANY_ATTEMPTS_TRY_LATER", "WEAK_PASSWORD",
                      "INVALID_EMAIL", "MISSING_PASSWORD"]:
                if k in err:
                    return {"ok": False, "message": k}
            return {"ok": False, "message": f"SIGNUP_FAILED: {err[:80]}"}

        auth = data["idToken"]
        fuid = data.get("localId", "")
        refresh = data.get("refreshToken", "")
        local_id = (fuid[:8] if fuid else "".join(random.choice("0123456789ABCDEF") for _ in range(8))).upper()

        start_money = 50_000_000
        start_coin = 30_000
        record = {
            "Name": display_name,
            "money": start_money,
            "coin": start_coin,
            "localID": local_id,
            "boughtFsos": [],
            "FriendsID": [],
            "LevelsDoneTime": [],
            "floats": [0.0] * 54,
            "integers": [0] * 120,
            "fcar": [],
            "favouriteWheels": [],
            "favouriteVinyls": [],
            "favouriteEmojis": [],
            "personEquipmentsMale": None,
            "personEquipmentsFemale": None,
            "platesData": None,
            "carIDnStatus": None,
            "allData": "",
            "flags": {},
            "animations": [],
            "emojiPacks": [],
            "wheels": [],
            "boughtPoliceLights": [],
            "boughtPoliceSirens": [],
        }
        save_ok = False
        save_msg = ""
        try:
            ok2, msg2 = self._send(auth, record, fuid, original=None)
            save_ok, save_msg = ok2, msg2
        except Exception as e:
            save_msg = str(e)[:80]

        return {
            "ok": True,
            "auth": auth,
            "refresh_token": refresh,
            "firebase_uid": fuid,
            "email": email,
            "localID": local_id,
            "Name": display_name,
            "money": start_money,
            "coin": start_coin,
            "player_saved": save_ok,
            "save_message": save_msg,
        }


# ═══════════════════════════════════════════════════════════
# 🚗 CAR CLONE FUNCTIONS
# ═══════════════════════════════════════════════════════════

CPM_FIELD_NAMES = {
    1: 'CarID', 2: 'dataVersion', 3: 'vectors', 4: 'floats', 5: 'gears',
    6: 'typeToInstall', 7: 'BoughtParts', 8: 'texts', 9: 'flagID',
    10: 'fsoData', 11: 'installedPoliceLights', 12: 'Vynils', 13: 'WindowVinyls',
}


def cpm1_ios_headers(token):
    return {
        'accept': '*/*',
        'authorization': f'Bearer {token}',
        'content-type': 'application/json; charset=utf-8',
        'user-agent': 'CarParking/265 CFNetwork/3860.600.12 Darwin/25.5.0',
        'x-client-platform': 'IOS',
        'x-client-version': '4.9.10',
        'x-client-deviceid': str(uuid.uuid4()).upper(),
        'x-request-nonce': str(uuid.uuid4()),
        'x-unity-version': '2022.3.62f2',
        'accept-encoding': 'gzip',
        'connection': 'Keep-Alive',
    }


def cpm1_write_memorypack_string(s):
    if s is None or s == "":
        return struct.pack("<i", 0)
    sb = str(s).encode("utf-8")
    return struct.pack("<ii", -len(sb) - 1, len(sb)) + sb


def cpm1_looks_like_base64(val):
    if not isinstance(val, str):
        return False
    if len(val) < 4 or len(val) % 4 != 0:
        return False
    return bool(re.match(r'^[A-Za-z0-9+/]+={0,2}$', val))


def cpm1_deserialize_string_list(b64):
    if not b64 or not isinstance(b64, str):
        return []
    try:
        compressed = base64.b64decode(b64)
    except Exception:
        return []
    try:
        decompressed = brotli.decompress(compressed)
    except Exception:
        try:
            decompressed = zlib.decompress(compressed, zlib.MAX_WBITS | 16)
        except Exception:
            decompressed = compressed
    if not decompressed or len(decompressed) < 4:
        return []
    out = []
    pos = 0
    count = struct.unpack_from("<i", decompressed, 0)[0]
    pos = 4
    if count <= 0 or count > 100000:
        return []
    for _ in range(count):
        if pos + 4 > len(decompressed):
            break
        marker = struct.unpack_from("<i", decompressed, pos)[0]
        pos += 4
        if marker == 0:
            out.append("")
            continue
        if marker == -1:
            break
        if marker < -1:
            byte_len = -marker - 1
            if pos + 4 > len(decompressed):
                break
            pos += 4
        else:
            byte_len = marker
        if pos + byte_len > len(decompressed):
            break
        s = decompressed[pos:pos + byte_len].decode("utf-8", errors="replace")
        pos += byte_len
        out.append(s.replace("\x00", "").strip())
    return out


def cpm1_is_readonly_cpm_id(v):
    if not isinstance(v, str):
        return False
    x = v.strip().upper()
    return len(x) == 8 and bool(re.match(r'^[A-Z]{2}\d{6}$', x))


def cpm1_generate_random_cpm_id():
    L = string.ascii_uppercase
    D = string.digits
    return ''.join(random.choice(L) for _ in range(2)) + ''.join(random.choice(D) for _ in range(6))


def cpm1_resolve_cpm_id(record):
    val = (record or {}).get("localID")
    if not val or not cpm1_is_readonly_cpm_id(val):
        val = cpm1_generate_random_cpm_id()
    return str(val).strip().upper()


def cpm1_generated_id_from_car(car):
    for k in ("generatedID", "generatedId", "carGeneratedID", "carGeneratedId"):
        v = car.get(k)
        if v and isinstance(v, str):
            return v
    texts = car.get("texts")
    if isinstance(texts, str):
        try:
            texts = cpm1_deserialize_string_list(texts)
        except Exception:
            texts = []
    if isinstance(texts, list):
        for v in texts:
            if v and isinstance(v, str) and '_' in v and re.search(r'\d', v):
                return v
    return None


def cpm1_build_car_entries(cars, cpm_id):
    entries = []
    for car in cars:
        try:
            cid = int(car.get("CarID"))
        except Exception:
            continue
        if cid < 0:
            continue
        gen = cpm1_generated_id_from_car(car) or f"{cpm_id}_{cid}XX000"
        entries.append([cid, gen])
    return entries


def cpm1_entries_to_sparse(car_entries):
    if not car_entries:
        return {"carGeneratedIDs": [], "carStatus": []}
    ids = []
    for e in car_entries:
        try:
            ids.append(int(e[0]))
        except Exception:
            pass
    ids = [i for i in ids if i >= 0]
    if not ids:
        return {"carGeneratedIDs": [], "carStatus": []}
    max_id = max(ids)
    gen_ids = [''] * (max_id + 1)
    statuses = [0] * (max_id + 1)
    for e in car_entries:
        try:
            cid = int(e[0])
        except Exception:
            continue
        if 0 <= cid <= max_id:
            gen_ids[cid] = str(e[1]) if e[1] is not None else ''
            statuses[cid] = 1
    return {"carGeneratedIDs": gen_ids, "carStatus": statuses}


def cpm1_replace_uid_in_value(value, src_uid, tgt_uid):
    if not src_uid or src_uid == tgt_uid:
        return value
    if isinstance(value, str):
        if cpm1_looks_like_base64(value):
            return value
        return value.replace(str(src_uid), str(tgt_uid))
    if isinstance(value, list):
        return [cpm1_replace_uid_in_value(v, src_uid, tgt_uid) for v in value]
    if isinstance(value, dict):
        return {k: cpm1_replace_uid_in_value(v, src_uid, tgt_uid) for k, v in value.items()}
    return value


def cpm1_prepare_car(stock_car, cpm_id, new_car_id=None):
    car_copy = json.loads(json.dumps(stock_car))
    car_id = int(car_copy.get("CarID") or 0)

    if new_car_id is not None:
        car_id = int(new_car_id)
        car_copy["CarID"] = car_id

    if isinstance(car_copy.get("Vynils"), dict):
        car_copy["Vynils"]["CarID"] = car_id
    texts = car_copy.get("texts")
    if isinstance(texts, str):
        try:
            texts = cpm1_deserialize_string_list(texts)
        except Exception:
            texts = []
    if not isinstance(texts, list):
        texts = []
    while len(texts) < 4:
        texts.append('')
    orig = texts[2]
    if isinstance(orig, str) and '_' in orig:
        parts = orig.split('_')
        texts[2] = f"{cpm_id}_" + "_".join(parts[1:])
    else:
        texts[2] = f"{cpm_id}_{car_id}XX000"
    car_copy["texts"] = texts
    return car_copy


def cpm1_sanitize_car_for_encoding(car):
    clean = {}
    for key, value in car.items():
        if isinstance(value, str) and cpm1_looks_like_base64(value):
            clean[key] = value
            continue
        if key == 'floats':
            arr = value if isinstance(value, list) else []
            out = []
            for v in arr:
                try:
                    f = float(v)
                except Exception:
                    f = 0.0
                out.append(f)
            while len(out) < 48:
                out.append(0.0)
            clean[key] = out
        elif key == 'gears':
            arr = value if isinstance(value, list) else []
            out = []
            for v in arr:
                try:
                    f = float(v)
                except Exception:
                    f = -10.0
                out.append(f)
            clean[key] = out
        elif key == 'vectors':
            arr = value if isinstance(value, list) else []
            out = []
            for vec in arr:
                if isinstance(vec, dict):
                    try:
                        x = float(vec.get('x') or 0)
                    except Exception:
                        x = 0.0
                    try:
                        y = float(vec.get('y') or 0)
                    except Exception:
                        y = 0.0
                    try:
                        z = float(vec.get('z') or 0)
                    except Exception:
                        z = 0.0
                    out.append({'x': x, 'y': y, 'z': z})
                else:
                    out.append({'x': 0.0, 'y': 0.0, 'z': 0.0})
            clean[key] = out
        elif key in ('typeToInstall', 'BoughtParts', 'fsoData', 'installedPoliceLights'):
            arr = value if isinstance(value, list) else []
            out = []
            for v in arr:
                try:
                    iv = int(v)
                except Exception:
                    iv = 0
                out.append(iv)
            clean[key] = out
        elif key == 'texts':
            arr = value if isinstance(value, list) else []
            clean[key] = [str(v) if v is not None else '' for v in arr]
        elif key == 'Vynils':
            if isinstance(value, dict) and isinstance(value.get('allVynils'), list):
                fc = int(value.get('_fieldCount', 2) or 2)
                if fc < 2:
                    fc = 2
                nv = {'_fieldCount': fc, 'allVynils': [], 'CarID': int(car.get('CarID', 0) or 0)}
                if isinstance(value.get('_remainingHex'), str):
                    nv['_remainingHex'] = value['_remainingHex']
                for vinyl in value['allVynils']:
                    if not isinstance(vinyl, dict):
                        continue
                    cv = {'_fieldCount': int(vinyl.get('_fieldCount', 6) or 6)}
                    for fn in ('position', 'scaleRotation', 'iconPosition'):
                        vec = vinyl.get(fn) or {}
                        if not isinstance(vec, dict):
                            vec = {}
                        try:
                            x = float(vec.get('x') or 0)
                        except Exception:
                            x = 0.0
                        try:
                            y = float(vec.get('y') or 0)
                        except Exception:
                            y = 0.0
                        try:
                            z = float(vec.get('z') or 0)
                        except Exception:
                            z = 0.0
                        cv[fn] = {'x': x, 'y': y, 'z': z}
                    cv['text'] = str(vinyl.get('text', '') or '')
                    cv['color'] = int(vinyl.get('color', 0) or 0)
                    cv['packedData'] = int(vinyl.get('packedData', 0) or 0)
                    nv['allVynils'].append(cv)
                clean[key] = nv
            else:
                clean[key] = value
        elif key == 'WindowVinyls':
            if isinstance(value, list):
                out = []
                for vinyl in value:
                    if not isinstance(vinyl, dict):
                        continue
                    cv = {'_fieldCount': int(vinyl.get('_fieldCount', 6) or 6)}
                    for fn in ('position', 'scaleRotation', 'iconPosition'):
                        vec = vinyl.get(fn) or {}
                        if not isinstance(vec, dict):
                            vec = {}
                        try:
                            x = float(vec.get('x') or 0)
                        except Exception:
                            x = 0.0
                        try:
                            y = float(vec.get('y') or 0)
                        except Exception:
                            y = 0.0
                        try:
                            z = float(vec.get('z') or 0)
                        except Exception:
                            z = 0.0
                        cv[fn] = {'x': x, 'y': y, 'z': z}
                    cv['text'] = str(vinyl.get('text', '') or '')
                    cv['color'] = int(vinyl.get('color', 0) or 0)
                    cv['packedData'] = int(vinyl.get('packedData', 0) or 0)
                    out.append(cv)
                clean[key] = out
            else:
                clean[key] = value
        elif key in ('CarID', 'dataVersion', 'flagID'):
            try:
                clean[key] = int(value)
            except Exception:
                clean[key] = 0
        else:
            clean[key] = value
    return clean


def cpm1_encode_field_value_binary(name, value):
    if name == 'vectors':
        if not isinstance(value, list):
            value = []
        buf = struct.pack("<i", len(value))
        for vec in value:
            x = float(vec.get('x') or 0) if isinstance(vec, dict) else 0.0
            y = float(vec.get('y') or 0) if isinstance(vec, dict) else 0.0
            z = float(vec.get('z') or 0) if isinstance(vec, dict) else 0.0
            buf += struct.pack("<fff", x, y, z)
        return buf
    if name in ('floats', 'gears'):
        if not isinstance(value, list):
            value = []
        buf = struct.pack("<i", len(value))
        for v in value:
            try:
                f = float(v)
            except Exception:
                f = 0.0
            buf += struct.pack("<f", f)
        return buf
    if name in ('typeToInstall', 'BoughtParts', 'fsoData', 'installedPoliceLights'):
        if not isinstance(value, list):
            value = []
        buf = struct.pack("<i", len(value))
        for v in value:
            try:
                iv = int(v)
            except Exception:
                iv = 0
            buf += struct.pack("<i", iv)
        return buf
    if name == 'texts':
        if not isinstance(value, list):
            value = []
        buf = struct.pack("<i", len(value))
        for v in value:
            buf += cpm1_write_memorypack_string(str(v) if v is not None else '')
        return buf
    if name == 'Vynils':
        if not isinstance(value, dict) or not isinstance(value.get('allVynils'), list):
            return b""
        fc = int(value.get('_fieldCount', 2) or 2)
        parts = [bytes([fc & 0xFF])]
        if fc == 0:
            return b"".join(parts)
        parts.append(struct.pack("<i", len(value['allVynils'])))
        for vinyl in value['allVynils']:
            if not isinstance(vinyl, dict):
                parts.append(bytes([0]))
                continue
            vfc = int(vinyl.get('_fieldCount', 6) or 6)
            parts.append(bytes([vfc & 0xFF]))
            if vfc == 0:
                continue
            for fn in ('position', 'scaleRotation', 'iconPosition'):
                vec = vinyl.get(fn) if isinstance(vinyl.get(fn), dict) else {}
                x = float(vec.get('x') or 0)
                y = float(vec.get('y') or 0)
                z = float(vec.get('z') or 0)
                parts.append(struct.pack("<fff", x, y, z))
            parts.append(cpm1_write_memorypack_string(str(vinyl.get('text', '') or '')))
            parts.append(struct.pack("<I", int(vinyl.get('color', 0) or 0) & 0xFFFFFFFF))
            try:
                pd = int(vinyl.get('packedData', 0) or 0)
            except Exception:
                pd = 0
            parts.append(struct.pack("<q", pd))
        if fc >= 2:
            carid = value.get('CarID')
            if carid is None and isinstance(value.get('_remainingHex'), str) and value['_remainingHex']:
                try:
                    hx = bytes.fromhex(value['_remainingHex'])
                    carid = struct.unpack_from("<i", hx, 0)[0]
                except Exception:
                    carid = 0
            try:
                carid = int(carid) if carid is not None else 0
            except Exception:
                carid = 0
            parts.append(struct.pack("<i", carid))
        return b"".join(parts)
    if name == 'WindowVinyls':
        if not isinstance(value, list):
            value = []
        parts = [struct.pack("<i", len(value))]
        for vinyl in value:
            if not isinstance(vinyl, dict):
                parts.append(bytes([0]))
                continue
            vfc = int(vinyl.get('_fieldCount', 6) or 6)
            parts.append(bytes([vfc & 0xFF]))
            if vfc == 0:
                continue
            for fn in ('position', 'scaleRotation', 'iconPosition'):
                vec = vinyl.get(fn) if isinstance(vinyl.get(fn), dict) else {}
                x = float(vec.get('x') or 0)
                y = float(vec.get('y') or 0)
                z = float(vec.get('z') or 0)
                parts.append(struct.pack("<fff", x, y, z))
            parts.append(cpm1_write_memorypack_string(str(vinyl.get('text', '') or '')))
            parts.append(struct.pack("<I", int(vinyl.get('color', 0) or 0) & 0xFFFFFFFF))
            try:
                pd = int(vinyl.get('packedData', 0) or 0)
            except Exception:
                pd = 0
            parts.append(struct.pack("<q", pd))
        return b"".join(parts)
    return b""


def cpm1_encode_car_to_memorypack(car):
    fields = []
    for fid, name in CPM_FIELD_NAMES.items():
        if name in car and car[name] is not None:
            fields.append((fid, name, car[name]))
    fields.sort(key=lambda x: x[0])
    parts = [struct.pack("<i", len(fields))]
    for fid, name, value in fields:
        parts.append(struct.pack("<h", fid))
        if name in ('CarID', 'dataVersion', 'flagID'):
            try:
                iv = int(value)
            except Exception:
                iv = 0
            parts.append(cpm1_write_memorypack_string(str(iv)))
        elif isinstance(value, str) and cpm1_looks_like_base64(value):
            parts.append(cpm1_write_memorypack_string(value))
        else:
            binv = cpm1_encode_field_value_binary(name, value)
            try:
                compressed = brotli.compress(binv, quality=4)
            except Exception:
                compressed = brotli.compress(binv)
            parts.append(cpm1_write_memorypack_string(base64.b64encode(compressed).decode('ascii')))
    return b"".join(parts)


def cpm1_encrypt_car_for_save(car, uid):
    clean = cpm1_sanitize_car_for_encoding(car)
    key = make_xor_key(uid)
    mp = cpm1_encode_car_to_memorypack(clean)
    compressed = brotli.compress(mp, quality=4)
    encrypted = xor_bytes(compressed, key)
    return "__ver1__" + base64.b64encode(encrypted).decode('ascii')


def cpm1_fetch_cars_v2(token):
    try:
        resp = http_session.post(CPM_CARS_FETCH_URL, json={"data": ""},
                                 headers=cpm1_ios_headers(token), timeout=20, verify=False)
        if resp.status_code < 200 or resp.status_code >= 300:
            return {"success": False, "error": f"HTTP {resp.status_code}",
                    "status": resp.status_code, "cars": []}
        parsed = resp.json() if resp.text else {}
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception:
                pass
        raw_result = None
        if isinstance(parsed, dict):
            if parsed.get("result") is not None:
                raw_result = parsed["result"]
            elif isinstance(parsed.get("data"), dict) and parsed["data"].get("result") is not None:
                raw_result = parsed["data"]["result"]
        if raw_result is None:
            return {"success": True, "cars": []}
        cars = []
        try:
            cars = json.loads(raw_result)
        except Exception:
            m = re.search(r'\[\s*\{[\s\S]*\}\s*\]', str(raw_result))
            if m:
                try:
                    cars = json.loads(m.group(0))
                except Exception:
                    pass
        return {"success": True, "cars": cars if isinstance(cars, list) else []}
    except Exception as e:
        return {"success": False, "error": str(e), "cars": []}


def cpm1_save_car_encrypted_v2(token, car, uid, retries=2):
    try:
        payload = cpm1_encrypt_car_for_save(car, uid)
    except Exception as e:
        return {"success": False, "error": f"encode:{str(e)[:80]}"}
    headers = cpm1_ios_headers(token)
    last = {"success": False, "error": "max_retries"}
    for attempt in range(1, retries + 1):
        try:
            resp = _get_cpm_http_session().post(
                CPM_CARS_SAVE_URL, json={"data": payload},
                headers=headers, timeout=8, verify=False
            )
            if resp.status_code < 300:
                return {"success": True, "status": resp.status_code, "data": resp.text}
            if resp.status_code in (401, 403):
                return {"success": False, "auth": True, "status": resp.status_code,
                        "error": f"HTTP {resp.status_code}"}
            last = {"success": False, "status": resp.status_code, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            last = {"success": False, "error": str(e)[:120]}
        if attempt < retries:
            time.sleep(min(1 * attempt, 3))
    return last


def cpm1_save_car_status(auth, fuid, car_entries, nuker=None):
    sparse = cpm1_entries_to_sparse(car_entries)
    gen_ids = ['' if v is None else str(v) for v in sparse["carGeneratedIDs"]]
    statuses = [0 if v is None else int(v) for v in sparse["carStatus"]]
    record = {"carIDnStatus": {"carGeneratedIDs": gen_ids, "carStatus": statuses}}
    try:
        payload = build_payload(record, fuid, original=None, force_fields={"carIDnStatus"})
        headers = {**GAME_HEADERS, "Authorization": f"Bearer {auth}", "Connection": "Keep-Alive"}
        r = requests.post(SAVE_URL, json={"data": {"data": payload, "deviceId": fuid[:8]}},
                          headers=headers, timeout=30, verify=False)
        ok = r.status_code == 200
        return {"success": ok, "message": "OK" if ok else f"HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:120]}


# ═══════════════════════════════════════════════════════════
# 👑 MEMBERSHIP DATABASE
# ═══════════════════════════════════════════════════════════

class MembershipDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS memberships (
                user_id INTEGER PRIMARY KEY,
                tier TEXT DEFAULT 'none',
                expires_at REAL DEFAULT 0,
                daily_count INTEGER DEFAULT 0,
                last_reset TEXT DEFAULT '',
                added_by INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            c.commit()

    def _today(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _reset_daily_if_needed(self, user_id: int):
        today = self._today()
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT last_reset FROM memberships WHERE user_id=?", (user_id,)
            ).fetchone()
            if row and row[0] != today:
                c.execute(
                    "UPDATE memberships SET daily_count=0, last_reset=? WHERE user_id=?",
                    (today, user_id)
                )
                c.commit()

    def get_membership(self, user_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT tier, expires_at, daily_count, last_reset FROM memberships WHERE user_id=?",
                (user_id,)
            ).fetchone()
        if not row:
            return None
        tier, expires_at, daily_count, last_reset = row
        if expires_at and time.time() > expires_at:
            with sqlite3.connect(self.db_path) as c:
                c.execute("UPDATE memberships SET tier='none', expires_at=0 WHERE user_id=?", (user_id,))
                c.commit()
            return {"tier": "none", "expires_at": 0, "daily_count": daily_count, "last_reset": last_reset}
        return {"tier": tier or "none", "expires_at": expires_at or 0,
                "daily_count": daily_count or 0, "last_reset": last_reset or ""}

    def is_prim(self, user_id: int) -> bool:
        m = self.get_membership(user_id)
        return bool(m and m["tier"] == "prim")

    def is_vip(self, user_id: int) -> bool:
        m = self.get_membership(user_id)
        return bool(m and m["tier"] == "vip")

    def is_member(self, user_id: int) -> bool:
        return self.is_prim(user_id) or self.is_vip(user_id)

    def get_daily_count(self, user_id: int) -> int:
        self._reset_daily_if_needed(user_id)
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT daily_count FROM memberships WHERE user_id=?", (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def increment_daily_count(self, user_id: int, amount: int = 1):
        self._reset_daily_if_needed(user_id)
        today = self._today()
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """INSERT INTO memberships (user_id, daily_count, last_reset)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   daily_count = daily_count + ?,
                   last_reset = ?
                """,
                (user_id, amount, today, amount, today)
            )
            c.commit()

    def can_clone(self, user_id: int) -> Tuple[bool, str, int]:
        m = self.get_membership(user_id)
        if not m or m["tier"] == "none":
            return False, "NOT_MEMBER", 0
        tier = m["tier"]
        if tier == "vip":
            return True, "VIP", 999999
        if tier == "prim":
            self._reset_daily_if_needed(user_id)
            used = self.get_daily_count(user_id)
            remaining = PRIM_DAILY_LIMIT - used
            if remaining <= 0:
                return False, "DAILY_LIMIT", 0
            return True, "PRIM", remaining
        return False, "UNKNOWN", 0

    def add_membership(self, user_id: int, tier: str, months: int = 1, added_by: int = 0) -> Dict[str, Any]:
        tier = (tier or "none").lower()
        if tier not in ("prim", "vip", "none"):
            return {"ok": False, "message": "INVALID_TIER"}
        now = time.time()
        current = self.get_membership(user_id)
        base = now
        if current and current.get("expires_at") and current["expires_at"] > now:
            base = current["expires_at"]
        expires_at = base + (months * 30 * 24 * 3600)
        if tier == "none":
            expires_at = 0
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """INSERT INTO memberships (user_id, tier, expires_at, daily_count, last_reset, added_by)
                   VALUES (?, ?, ?, 0, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   tier=?, expires_at=?, daily_count=0, last_reset=?, added_by=?
                """,
                (user_id, tier, expires_at, self._today(), added_by,
                 tier, expires_at, self._today(), added_by)
            )
            c.commit()
        return {"ok": True, "tier": tier, "expires_at": expires_at, "months": months}

    def remove_membership(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as c:
            c.execute("UPDATE memberships SET tier='none', expires_at=0 WHERE user_id=?", (user_id,))
            c.commit()
        return True

    def list_members(self) -> List[Dict[str, Any]]:
        now = time.time()
        with sqlite3.connect(self.db_path) as c:
            rows = c.execute(
                """SELECT user_id, tier, expires_at, daily_count, last_reset, added_by, added_at
                   FROM memberships WHERE tier != 'none' AND expires_at > ?
                   ORDER BY tier DESC, expires_at ASC""",
                (now,)
            ).fetchall()
        return [
            {"user_id": r[0], "tier": r[1], "expires_at": r[2], "daily_count": r[3],
             "last_reset": r[4], "added_by": r[5], "added_at": r[6]}
            for r in rows
        ]

    def get_stats(self) -> Dict[str, Any]:
        now = time.time()
        with sqlite3.connect(self.db_path) as c:
            total = c.execute("SELECT COUNT(*) FROM memberships WHERE tier != 'none' AND expires_at > ?", (now,)).fetchone()[0]
            prim = c.execute("SELECT COUNT(*) FROM memberships WHERE tier='prim' AND expires_at > ?", (now,)).fetchone()[0]
            vip = c.execute("SELECT COUNT(*) FROM memberships WHERE tier='vip' AND expires_at > ?", (now,)).fetchone()[0]
        return {"total": total, "prim": prim, "vip": vip}


membership_db = MembershipDB(DB_PATH)
nuker = CPM1(DB_PATH)

# Conversation states
(EMAIL, PASSWORD, COUNT, SPEED, CONFIRM) = range(5)

ACTIVE_JOBS = {}


def md_escape(text) -> str:
    if text is None:
        return ""
    text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    out = []
    for ch in text:
        if ch in escape_chars:
            out.append('\\' + ch)
        elif ch == '\\':
            out.append('\\\\')
        else:
            out.append(ch)
    return ''.join(out)


async def send_md(bot, chat_id, text, reply_markup=None, edit_message_id=None):
    try:
        if edit_message_id:
            return await bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.warning(f"send_md failed: {e}")
        try:
            plain = re.sub(r'\\(.)', r'\1', text)
            if edit_message_id:
                return await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    text=plain,
                    reply_markup=reply_markup,
                )
            return await bot.send_message(
                chat_id=chat_id,
                text=plain,
                reply_markup=reply_markup,
            )
        except Exception as e2:
            logger.error(f"send_md fallback failed: {e2}")
            return None


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def membership_required_message():
    return (
        "🔒 *ÜYELİK GEREKLİ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "❌ Bu botu kullanmak için *PRIM* veya *VIP* üyeliğiniz olması gerekmektedir\\.\n\n"
        "💎 *PRIM Üyelik:*\n"
        f"• 1 Ay: `{PRIM_1_MONTH} TL`\n"
        f"• 2 Ay: `{PRIM_2_MONTH} TL`\n"
        f"• Günlük max *{PRIM_DAILY_LIMIT} hesap* klonlama\n\n"
        "👑 *VIP Üyelik:*\n"
        f"• 1 Ay: `{VIP_1_MONTH} TL`\n"
        f"• 2 Ay: `{VIP_2_MONTH} TL`\n"
        "• *Sınırsız* klonlama\n\n"
        f"📞 Satın almak için: {md_escape(CONTACT_USERNAME)}\n\n"
        "_Üyeliğiniz bulunmaktadır " + md_escape(CONTACT_USERNAME) + " ile iletişime geçin_"
    )


def get_user_results_dir(user_id: int) -> str:
    """Her kullanıcı için ayrı sonuç klasörü döndürür."""
    user_dir = os.path.join(RESULTS_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def save_user_result_file(user_id: int, content: str) -> str:
    """Kullanıcıya özel sonuç dosyası kaydeder, tam yolu döndürür."""
    user_dir = get_user_results_dir(user_id)
    filename = f"cloned_accounts_{int(time.time())}.txt"
    path = os.path.join(user_dir, filename)
    # Çakışma olursa ek suffix
    counter = 1
    while os.path.exists(path):
        filename = f"cloned_accounts_{int(time.time())}_{counter}.txt"
        path = os.path.join(user_dir, filename)
        counter += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ═══════════════════════════════════════════════════════════
# 🤖 TELEGRAM BOT COMMANDS
# ═══════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if is_admin(user_id):
        name = md_escape(user.first_name or "Admin")
        text = (
            "👑 *ADMIN PANELİ* 👑\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Hoş geldin *{name}*\\!\n\n"
            "📋 *Admin Komutları:*\n"
            "• /clone \\- Klon başlat\n"
            "• /saved \\- Sonuçları görüntüle\n"
            "• /admin \\- Admin paneli\n"
            "• /addmember \\- Üye ekle\n"
            "• /removemember \\- Üye çıkar\n"
            "• /members \\- Üyeleri listele\n"
            "• /stats \\- İstatistikler\n"
            "• /cancel \\- İptal\n\n"
            "💎 PRIM: Günlük max 2 hesap\n"
            "👑 VIP: Sınırsız"
        )
        await send_md(context.bot, update.effective_chat.id, text)
        return

    if not membership_db.is_member(user_id):
        text = (
            "🚗 *CPM1 CLONE BOT* 🚗\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❌ Bu botu kullanmak için üyeliğiniz bulunmamaktadır\\.\n\n"
            f"📞 Üyelik için: {md_escape(CONTACT_USERNAME)}\n\n"
            "_Üyeliğiniz bulunmaktadır " + md_escape(CONTACT_USERNAME) + " ile iletişime geçin_"
        )
        await send_md(context.bot, update.effective_chat.id, text)
        return

    m = membership_db.get_membership(user_id)
    tier = m["tier"] if m else "none"
    tier_icon = "💎" if tier == "prim" else "👑"
    tier_name = "PRIM" if tier == "prim" else "VIP"
    name = md_escape(user.first_name or "Kullanıcı")

    if tier == "prim":
        used = membership_db.get_daily_count(user_id)
        remaining = max(0, PRIM_DAILY_LIMIT - used)
        tier_info = f"Günlük kalan: `{remaining}/{PRIM_DAILY_LIMIT}`"
    else:
        tier_info = "Sınırsız klonlama"

    text = (
        f"{tier_icon} *{tier_name} ÜYE* {tier_icon}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Merhaba *{name}*\\!\n\n"
        f"💎 Üyelik: *{tier_name}*\n"
        f"📊 {tier_info}\n\n"
        "📋 *Komutlar:*\n"
        "• /clone \\- Yeni klon başlat\n"
        "• /saved \\- Sonuçları görüntüle\n"
        "• /profile \\- Üyelik bilgilerim\n"
        "• /cancel \\- İptal"
    )
    await send_md(context.bot, update.effective_chat.id, text)


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await start(update, context)
        return
    m = membership_db.get_membership(user_id)
    if not m or m["tier"] == "none":
        await send_md(context.bot, update.effective_chat.id, membership_required_message())
        return

    tier = m["tier"]
    tier_name = "PRIM" if tier == "prim" else "VIP"
    tier_icon = "💎" if tier == "prim" else "👑"

    expires = m.get("expires_at", 0)
    if expires > 0:
        remaining_days = max(0, int((expires - time.time()) / 86400))
        expiry_text = f"{remaining_days} gün"
        expiry_date = datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M")
    else:
        expiry_text = "Süresiz"
        expiry_date = "-"

    if tier == "prim":
        used = membership_db.get_daily_count(user_id)
        remaining = max(0, PRIM_DAILY_LIMIT - used)
        daily_text = f"`{used}/{PRIM_DAILY_LIMIT}` (kalan: `{remaining}`)"
    else:
        daily_text = "Sınırsız"

    text = (
        f"{tier_icon} *ÜYELİK BİLGİLERİM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 ID: `{user_id}`\n"
        f"💎 Üyelik: *{tier_name}*\n"
        f"⏱️ Kalan Süre: *{expiry_text}*\n"
        f"📅 Bitiş: `{expiry_date}`\n"
        f"📊 Bugünkü Kullanım: {daily_text}"
    )
    await send_md(context.bot, update.effective_chat.id, text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id) and not membership_db.is_member(user_id):
        await send_md(context.bot, update.effective_chat.id, membership_required_message())
        return

    text = (
        "📖 *YARDIM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "*Nasıl kullanılır?*\n\n"
        "1\\. `/clone` komutunu gönder\n"
        "2\\. Kaynak hesabın e\\-postasını gir\n"
        "3\\. Kaynak hesabın şifresini gir\n"
        "4\\. Kaç hesap oluşturulacağını gir\n"
        "5\\. Hız modunu seç\n"
        "6\\. Onayla ve bekle\n\n"
        "*Hız Modları:*\n"
        "🐢 Güvenli \\- 5 worker\n"
        "⚡ Hızlı \\- 15 worker\n"
        "🚀 Turbo \\- 30 worker\n"
        "💥 Max \\- 50 worker\n\n"
        "*Not:* PRIM üyeler günde max 2 hesap klonlayabilir\\."
    )
    await send_md(context.bot, update.effective_chat.id, text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await send_md(
        context.bot,
        update.effective_chat.id,
        "❌ *İşlem iptal edildi\\.*",
    )
    return ConversationHandler.END


async def clone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        allowed, reason, remaining = membership_db.can_clone(user_id)
        if not allowed:
            if reason == "NOT_MEMBER":
                await send_md(context.bot, update.effective_chat.id, membership_required_message())
            elif reason == "DAILY_LIMIT":
                await send_md(
                    context.bot,
                    update.effective_chat.id,
                    f"⏰ *GÜNLÜK LİMİT DOLDU\\!*\n\n"
                    f"💎 PRIM üyeler günde max *{PRIM_DAILY_LIMIT} hesap* klonlayabilir\\.\n\n"
                    f"Yarın tekrar deneyin veya *VIP* üyeliğe geçin\\.",
                )
            return ConversationHandler.END

    if user_id in ACTIVE_JOBS:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "⚠️ *Zaten aktif bir işleminiz var\\!*\nLütfen bitmesini bekleyin veya /cancel gönderin\\.",
        )
        return ConversationHandler.END

    context.user_data.clear()
    await send_md(
        context.bot,
        update.effective_chat.id,
        "📧 *Kaynak Hesap E\\-postası:*\n\n"
        "Örnek: `ornek@gmail\\.com`\n\n"
        "İptal için /cancel",
    )
    return EMAIL


async def clone_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.message.text or "").strip()
    if "@" not in email or "." not in email:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Geçersiz e\\-posta\\!* Tekrar deneyin:",
        )
        return EMAIL
    context.user_data["src_email"] = email
    await send_md(
        context.bot,
        update.effective_chat.id,
        "🔑 *Kaynak Hesap Şifresi:*\n\n"
        "İptal için /cancel",
    )
    return PASSWORD


async def clone_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = (update.message.text or "").strip()
    if len(password) < 4:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Şifre çok kısa\\!* Tekrar deneyin:",
        )
        return PASSWORD
    context.user_data["src_pass"] = password
    try:
        await update.message.delete()
    except Exception:
        pass

    user_id = update.effective_user.id
    max_count = 100
    limit_note = ""
    if not is_admin(user_id):
        allowed, reason, remaining = membership_db.can_clone(user_id)
        if allowed and reason == "PRIM":
            max_count = min(remaining, 100)
            limit_note = f"\n💎 PRIM günlük kalan: *{remaining}* hesap"

    await send_md(
        context.bot,
        update.effective_chat.id,
        f"🔢 *Kaç Hesap Oluşturulsun?* \\(1\\-{max_count}\\){limit_note}\n\n"
        f"Örnek: `{min(5, max_count)}`\n\n"
        "İptal için /cancel",
    )
    return COUNT


async def clone_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    max_count = 100
    if not is_admin(user_id):
        allowed, reason, remaining = membership_db.can_clone(user_id)
        if allowed and reason == "PRIM":
            max_count = min(remaining, 100)
        elif not allowed:
            await send_md(context.bot, update.effective_chat.id, membership_required_message())
            return ConversationHandler.END

    try:
        count = int((update.message.text or "").strip())
        if count < 1 or count > max_count:
            raise ValueError
    except ValueError:
        await send_md(
            context.bot,
            update.effective_chat.id,
            f"❌ *Geçersiz sayı\\!* 1\\-{max_count} arası bir değer girin:",
        )
        return COUNT
    context.user_data["count"] = count

    if not is_admin(user_id):
        allowed, reason, remaining = membership_db.can_clone(user_id)
        if allowed and reason == "PRIM":
            context.user_data["workers"] = 5
            src_email = context.user_data.get("src_email", "")
            keyboard = [
                [
                    InlineKeyboardButton("✅ Başlat", callback_data="confirm_yes"),
                    InlineKeyboardButton("❌ İptal", callback_data="confirm_no"),
                ]
            ]
            text = (
                "📋 *ÖZET*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💎 Üyelik: *PRIM*\n"
                f"📧 Kaynak: `{md_escape(src_email)}`\n"
                f"🔢 Hesap Sayısı: `{count}`\n"
                f"⚡ Hız: *Güvenli* \\(5 worker\\)\n\n"
                "_PRIM üyeler güvenli modda klonlanır\\._\n\n"
                "Onaylıyor musunuz?"
            )
            await send_md(
                context.bot,
                update.effective_chat.id,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return CONFIRM

    keyboard = [
        [InlineKeyboardButton("🐢 Güvenli (5 worker)", callback_data="speed_5")],
        [InlineKeyboardButton("⚡ Hızlı (15 worker)", callback_data="speed_15")],
        [InlineKeyboardButton("🚀 Turbo (30 worker)", callback_data="speed_30")],
        [InlineKeyboardButton("💥 Max (50 worker)", callback_data="speed_50")],
    ]
    await send_md(
        context.bot,
        update.effective_chat.id,
        "⚡ *Hız Modu Seçin:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SPEED


async def clone_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("speed_"):
        return SPEED
    try:
        workers = int(data.split("_")[1])
    except Exception:
        workers = 30
    context.user_data["workers"] = workers

    src_email = context.user_data.get("src_email", "")
    count = context.user_data.get("count", 1)

    keyboard = [
        [
            InlineKeyboardButton("✅ Başlat", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ İptal", callback_data="confirm_no"),
        ]
    ]
    text = (
        "📋 *ÖZET*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Kaynak: `{md_escape(src_email)}`\n"
        f"🔢 Hesap Sayısı: `{count}`\n"
        f"⚡ Worker: `{workers}`\n\n"
        "Onaylıyor musunuz?"
    )
    await send_md(
        context.bot,
        update.effective_chat.id,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        edit_message_id=query.message.message_id,
    )
    return CONFIRM


async def clone_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "confirm_no":
        context.user_data.clear()
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *İşlem iptal edildi\\.*",
            edit_message_id=query.message.message_id,
        )
        return ConversationHandler.END

    src_email = context.user_data.get("src_email")
    src_pass = context.user_data.get("src_pass")
    count = context.user_data.get("count", 1)
    workers = context.user_data.get("workers", 30)

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = query.message.message_id

    if not is_admin(user_id):
        allowed, reason, remaining = membership_db.can_clone(user_id)
        if not allowed:
            context.user_data.clear()
            if reason == "DAILY_LIMIT":
                await send_md(
                    context.bot,
                    chat_id,
                    f"⏰ *GÜNLÜK LİMİT DOLDU\\!*\n\nPRIM üyeler günde max *{PRIM_DAILY_LIMIT} hesap* klonlayabilir\\.",
                    edit_message_id=message_id,
                )
            else:
                await send_md(context.bot, chat_id, membership_required_message())
            return ConversationHandler.END
        if reason == "PRIM" and count > remaining:
            count = remaining

    await send_md(
        context.bot,
        chat_id,
        "⏳ *İşlem Başlatılıyor\\.\\.\\.*\n\nKaynak hesaba giriş yapılıyor\\.",
        edit_message_id=message_id,
    )

    ACTIVE_JOBS[user_id] = True

    asyncio.create_task(
        run_clone(
            bot=context.bot,
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            src_email=src_email,
            src_pass=src_pass,
            count=count,
            workers=workers,
        )
    )
    return ConversationHandler.END


async def run_clone(bot, chat_id, message_id, user_id, src_email, src_pass, count, workers):
    is_admin_user = is_admin(user_id)
    is_prim = False
    if not is_admin_user:
        m = membership_db.get_membership(user_id)
        if m and m["tier"] == "prim":
            is_prim = True

    async def status(text, edit=True):
        if edit:
            await send_md(bot, chat_id, text, edit_message_id=message_id)
        else:
            await send_md(bot, chat_id, text)

    try:
        await status("⏳ *Kaynak hesaba giriş yapılıyor\\.\\.\\.*")

        loop = asyncio.get_event_loop()
        src_login = await loop.run_in_executor(None, nuker.login, src_email, src_pass)

        if not src_login.get("ok"):
            await status(
                "❌ *Giriş başarısız\\!*\n\n"
                f"Sebep: `{md_escape(src_login.get('message', 'UNKNOWN'))}`"
            )
            return

        src_uid = src_login.get("firebase_uid", "")
        src_token = src_login.get("auth", "")

        await status("🚗 *Kaynak arabalar çekiliyor\\.\\.\\.*")
        cars_result = await loop.run_in_executor(None, cpm1_fetch_cars_v2, src_token)

        if not cars_result.get("success"):
            await status(
                "❌ *Araba çekme hatası\\!*\n\n"
                f"Sebep: `{md_escape(cars_result.get('error', 'UNKNOWN'))}`"
            )
            return

        source_cars = cars_result.get("cars", [])
        if not source_cars:
            await status("❌ *Kaynak hesapta araba bulunamadı\\!*")
            return

        await status(
            f"✅ *{len(source_cars)} araba bulundu*\n\n"
            f"🚀 *{count} hesap* oluşturuluyor\\.\\.\\.\n"
            f"⚡ Worker: `{workers}`\n\n"
            "_Bu işlem biraz sürebilir\\._"
        )

        lock = threading.Lock()
        counter = {"done": 0, "ok": 0, "fail": 0}
        created_accounts = []
        progress_msg_id = [message_id]
        last_update_time = [0.0]

        def safe_edit_progress(text):
            now = time.time()
            if now - last_update_time[0] < 2.0:
                return
            last_update_time[0] = now

            async def _do():
                try:
                    await send_md(bot, chat_id, text, edit_message_id=progress_msg_id[0])
                except Exception:
                    pass

            try:
                asyncio.run_coroutine_threadsafe(_do(), loop)
            except Exception:
                pass

        def clone_one_account(index):
            try:
                rand_num = random.randint(10000000, 99999999)
                new_email = f"derin{rand_num}@gmail.com"
                new_password = ''.join(
                    random.choice(string.ascii_letters + string.digits) for _ in range(15)
                )
                new_name = f"Player{index + 1}_{rand_num}"

                create_result = nuker.create_account(new_email, new_password, new_name)
                if not create_result.get("ok"):
                    with lock:
                        counter["done"] += 1
                        counter["fail"] += 1
                    return None

                new_login = nuker.login(new_email, new_password)
                if not new_login.get("ok"):
                    with lock:
                        counter["done"] += 1
                        counter["fail"] += 1
                    return None

                new_uid = new_login.get("firebase_uid", "")
                new_token = new_login.get("auth", "")
                cpm_id = cpm1_generate_random_cpm_id()

                prepared = []
                for car in source_cars:
                    try:
                        c2 = json.loads(json.dumps(car))
                        if src_uid:
                            c2 = cpm1_replace_uid_in_value(c2, src_uid, new_uid)
                        prepared.append(cpm1_prepare_car(c2, cpm_id))
                    except Exception:
                        pass

                ok_count = 0
                if prepared:
                    with ThreadPoolExecutor(max_workers=min(50, len(prepared))) as car_executor:
                        car_futures = [
                            car_executor.submit(
                                cpm1_save_car_encrypted_v2, new_token, car, new_uid, 1
                            )
                            for car in prepared
                        ]
                        for future in as_completed(car_futures):
                            try:
                                sr = future.result()
                                if sr.get("success"):
                                    ok_count += 1
                            except Exception:
                                pass

                entries = cpm1_build_car_entries(prepared, cpm_id)
                cpm1_save_car_status(new_token, new_uid, entries)

                with lock:
                    counter["done"] += 1
                    counter["ok"] += 1
                    done = counter["done"]
                    ok = counter["ok"]
                    fail = counter["fail"]

                safe_edit_progress(
                    f"🚀 *İlerleme:* `{done}/{count}`\n"
                    f"✅ Başarılı: `{ok}`\n"
                    f"❌ Başarısız: `{fail}`\n\n"
                    f"📧 `{md_escape(new_email)}`\n"
                    f"🚗 Araba: `{ok_count}`"
                )

                return {
                    "email": new_email,
                    "password": new_password,
                    "name": new_name,
                    "id": create_result.get("localID", ""),
                    "cars": ok_count,
                    "index": index + 1,
                }

            except Exception as e:
                logger.exception("clone_one_account failed")
                with lock:
                    counter["done"] += 1
                    counter["fail"] += 1
                return None

        start_time = time.time()

        def run_all():
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(clone_one_account, i): i
                    for i in range(count)
                }
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            with lock:
                                created_accounts.append(result)
                    except Exception:
                        pass
            return created_accounts

        created = await loop.run_in_executor(None, run_all)
        elapsed = time.time() - start_time

        if is_prim:
            membership_db.increment_daily_count(user_id, count)

        if created:
            created.sort(key=lambda x: x.get("index", 0))

            # ✅ Kullanıcıya özel klasöre kaydet
            content_lines = []
            content_lines.append("═" * 60)
            content_lines.append("CPM1 BULK CLONE RESULTS")
            content_lines.append(f"Tarih: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            content_lines.append(f"Toplam: {len(created)} hesap")
            content_lines.append(f"Kaynak Araba: {len(source_cars)}")
            content_lines.append(f"Worker: {workers}")
            content_lines.append(f"Süre: {elapsed:.1f} saniye")
            content_lines.append("═" * 60)
            content_lines.append("")

            for i, acc in enumerate(created, 1):
                content_lines.append(f"[{i}] Account")
                content_lines.append(f"  Email:    {acc['email']}")
                content_lines.append(f"  Password: {acc['password']}")
                content_lines.append(f"  Name:     {acc['name']}")
                content_lines.append(f"  ID:       {acc['id']}")
                content_lines.append(f"  Cars:     {acc['cars']}")
                if i < len(created):
                    content_lines.append("")

            content = "\n".join(content_lines)
            save_path = save_user_result_file(user_id, content)

            avg = elapsed / len(created) if created else 0
            summary = (
                "✅ *TAMAMLANDI\\!*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Başarılı: `{len(created)}/{count}`\n"
                f"⏱️ Süre: `{elapsed:.1f} sn`\n"
                f"⚡ Ortalama: `{avg:.2f} sn/hesap`\n"
                f"🚗 Araba: `{len(source_cars)}`\n\n"
                "📄 Sonuç dosyası gönderiliyor\\.\\.\\."
            )
            await status(summary)

            try:
                with open(save_path, "rb") as doc:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=doc,
                        filename=os.path.basename(save_path),
                        caption=f"📄 {len(created)} hesap sonucu",
                    )
            except Exception as e:
                await send_md(
                    bot,
                    chat_id,
                    f"⚠️ Dosya gönderilemedi: `{md_escape(str(e)[:100])}`",
                )
        else:
            await status(
                "❌ *Hiç hesap oluşturulamadı\\!*\n\n"
                f"Başarısız: `{counter['fail']}/{count}`"
            )

    except Exception as e:
        logger.exception("run_clone failed")
        try:
            await send_md(
                bot,
                chat_id,
                f"❌ *Beklenmeyen hata:*\n`{md_escape(str(e)[:200])}`",
                edit_message_id=message_id,
            )
        except Exception:
            pass
    finally:
        ACTIVE_JOBS.pop(user_id, None)


async def saved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sadece kullanıcının KENDİ sonuçlarını gösterir. Admin tüm kullanıcıları görebilir."""
    user_id = update.effective_user.id
    if not is_admin(user_id) and not membership_db.is_member(user_id):
        await send_md(context.bot, update.effective_chat.id, membership_required_message())
        return

    try:
        # ✅ Kullanıcı sadece kendi klasörünü görür
        if is_admin(user_id):
            # Admin: tüm kullanıcıların sonuçlarını listeleyebilir
            all_files = []
            if os.path.isdir(RESULTS_DIR):
                for uid_dir in os.listdir(RESULTS_DIR):
                    full_dir = os.path.join(RESULTS_DIR, uid_dir)
                    if not os.path.isdir(full_dir):
                        continue
                    for f in os.listdir(full_dir):
                        if f.endswith(".txt") and f.startswith("cloned_accounts_"):
                            path = os.path.join(full_dir, f)
                            all_files.append((path, uid_dir, f))
            if not all_files:
                await send_md(
                    context.bot,
                    update.effective_chat.id,
                    "📭 *Kayıtlı sonuç yok\\.*",
                )
                return
            # En yeni 15 dosya
            all_files.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)
            all_files = all_files[:15]
            keyboard = []
            for path, uid_dir, f in all_files:
                size = os.path.getsize(path)
                label = f"👤 {uid_dir} | {f.replace('cloned_accounts_', '').replace('.txt', '')} ({size} B)"
                # callback_data max 64 byte, dosya adı + kullanıcı ID sığmalı
                cb = f"file_{uid_dir}_{f}"
                if len(cb.encode('utf-8')) > 64:
                    # Çok uzunsa kısalt
                    cb = f"file_{uid_dir}_{f}"[:64]
                keyboard.append([InlineKeyboardButton(label, callback_data=cb)])
            await send_md(
                context.bot,
                update.effective_chat.id,
                "📂 *Tüm Kullanıcı Sonuçları* \\(admin\\):\n\nBirini seçin:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # Normal kullanıcı: sadece kendi klasörü
        user_dir = get_user_results_dir(user_id)
        files = [
            f for f in os.listdir(user_dir)
            if f.endswith(".txt") and f.startswith("cloned_accounts_")
        ]
        if not files:
            await send_md(
                context.bot,
                update.effective_chat.id,
                "📭 *Kayıtlı sonucunuz yok\\.*\n\n"
                "Önce `/clone` komutu ile hesap oluşturun\\.",
            )
            return

        # En yeni önce
        files_with_time = []
        for f in files:
            path = os.path.join(user_dir, f)
            files_with_time.append((f, os.path.getmtime(path), os.path.getsize(path)))
        files_with_time.sort(key=lambda x: x[1], reverse=True)
        files_with_time = files_with_time[:10]

        keyboard = []
        for f, mtime, size in files_with_time:
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            label = f"{date_str} ({size} B)"
            cb = f"file_{user_id}_{f}"
            if len(cb.encode('utf-8')) > 64:
                cb = cb[:64]
            keyboard.append([InlineKeyboardButton(label, callback_data=cb)])

        await send_md(
            context.bot,
            update.effective_chat.id,
            "📂 *Kendi Sonuçlarınız:*\n\nBirini seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        await send_md(
            context.bot,
            update.effective_chat.id,
            f"❌ Hata: `{md_escape(str(e)[:100])}`",
        )


async def file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dosya gönderir. Kullanıcı sadece kendi dosyasını indirebilir."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("file_"):
        return

    user_id = update.effective_user.id
    if not is_admin(user_id) and not membership_db.is_member(user_id):
        await send_md(context.bot, update.effective_chat.id, membership_required_message())
        return

    # data = "file_<owner_uid>_<filename>"
    rest = data[5:]  # file_ sonrası
    # İlk parça owner uid olmalı (sayı)
    parts = rest.split("_", 1)
    if len(parts) != 2:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Geçersiz dosya\\.*",
        )
        return
    try:
        owner_uid = int(parts[0])
    except ValueError:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Geçersiz dosya sahibi\\.*",
        )
        return
    filename = os.path.basename(parts[1])

    # ✅ Güvenlik: kullanıcı sadece KENDİ dosyasını indirebilir
    if not is_admin(user_id) and owner_uid != user_id:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "⛔ *Bu dosya size ait değil\\!*",
        )
        return

    owner_dir = os.path.join(RESULTS_DIR, str(owner_uid))
    path = os.path.join(owner_dir, filename)

    # Path traversal koruması
    real_path = os.path.realpath(path)
    real_owner = os.path.realpath(owner_dir)
    if not real_path.startswith(real_owner):
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Geçersiz dosya yolu\\.*",
        )
        return

    if not os.path.exists(path):
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Dosya bulunamadı\\.*",
        )
        return

    try:
        with open(path, "rb") as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename=filename,
                caption=f"📄 {filename}",
            )
        await send_md(
            context.bot,
            update.effective_chat.id,
            f"✅ `{md_escape(filename)}` gönderildi\\.",
        )
    except Exception as e:
        await send_md(
            context.bot,
            update.effective_chat.id,
            f"❌ Gönderilemedi: `{md_escape(str(e)[:100])}`",
        )


# ═══════════════════════════════════════════════════════════
# 👑 ADMIN COMMANDS
# ═══════════════════════════════════════════════════════════

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await send_md(
                context.bot,
                update.effective_chat.id,
                "⛔ *Bu komut sadece admin içindir\\!*",
            )
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = membership_db.get_stats()
    text = (
        "👑 *ADMIN PANELİ* 👑\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 *İstatistikler:*\n"
        f"• Toplam Üye: `{stats['total']}`\n"
        f"• 💎 PRIM: `{stats['prim']}`\n"
        f"• 👑 VIP: `{stats['vip']}`\n\n"
        "📋 *Komutlar:*\n"
        "• /addmember `user_id` `tier` `ay`\n"
        "• /removemember `user_id`\n"
        "• /members \\- Üye listesi\n"
        "• /memberinfo `user_id`\n"
        "• /stats \\- İstatistikler\n"
        "• /broadcast \\- Mesaj gönder\n\n"
        "*Tier:* `prim` veya `vip`\n"
        "*Ay:* `1` veya `2`\n\n"
        f"💰 *Fiyatlar:*\n"
        f"💎 PRIM: 1 ay `{PRIM_1_MONTH} TL` / 2 ay `{PRIM_2_MONTH} TL`\n"
        f"👑 VIP: 1 ay `{VIP_1_MONTH} TL` / 2 ay `{VIP_2_MONTH} TL`"
    )
    await send_md(context.bot, update.effective_chat.id, text)


@admin_only
async def add_member_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 2:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Kullanım:* `/addmember <user_id> <prim|vip> [ay]`\n\n"
            "Örnek: `/addmember 123456789 prim 1`\n"
            "Örnek: `/addmember 123456789 vip 2`",
        )
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await send_md(context.bot, update.effective_chat.id, "❌ *Geçersiz user_id\\!*")
        return
    tier = args[1].lower()
    if tier not in ("prim", "vip"):
        await send_md(context.bot, update.effective_chat.id, "❌ *Tier `prim` veya `vip` olmalı\\!*")
        return
    months = 1
    if len(args) >= 3:
        try:
            months = int(args[2])
        except ValueError:
            months = 1
    if months not in (1, 2):
        await send_md(context.bot, update.effective_chat.id, "❌ *Ay `1` veya `2` olmalı\\!*")
        return

    result = membership_db.add_membership(target_id, tier, months, added_by=update.effective_user.id)
    if not result.get("ok"):
        await send_md(context.bot, update.effective_chat.id, f"❌ Hata: `{md_escape(result.get('message', ''))}`")
        return

    tier_icon = "💎" if tier == "prim" else "👑"
    tier_name = "PRIM" if tier == "prim" else "VIP"
    expires = result.get("expires_at", 0)
    expiry_date = datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M") if expires else "-"

    await send_md(
        context.bot,
        update.effective_chat.id,
        f"✅ *Üye Eklendi\\!*\n\n"
        f"{tier_icon} Tier: *{tier_name}*\n"
        f"👤 User ID: `{target_id}`\n"
        f"📅 Bitiş: `{expiry_date}`\n"
        f"⏱️ Süre: `{months}` ay",
    )

    try:
        await send_md(
            context.bot,
            target_id,
            f"🎉 *ÜYELİĞİNİZ AKTİF EDİLDİ\\!*\n\n"
            f"{tier_icon} Üyelik: *{tier_name}*\n"
            f"📅 Bitiş: `{expiry_date}`\n"
            f"⏱️ Süre: `{months}` ay\n\n"
            f"Botu kullanmak için /start yazın\\.",
        )
    except Exception:
        pass


@admin_only
async def remove_member_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 1:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Kullanım:* `/removemember <user_id>`",
        )
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await send_md(context.bot, update.effective_chat.id, "❌ *Geçersiz user_id\\!*")
        return

    membership_db.remove_membership(target_id)
    await send_md(
        context.bot,
        update.effective_chat.id,
        f"✅ *Üyelik kaldırıldı:* `{target_id}`",
    )
    try:
        await send_md(
            context.bot,
            target_id,
            "❌ *Üyeliğiniz sonlandırıldı\\.*\n\n"
            f"Bilgi için: {md_escape(CONTACT_USERNAME)}",
        )
    except Exception:
        pass


@admin_only
async def members_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    members = membership_db.list_members()
    if not members:
        await send_md(context.bot, update.effective_chat.id, "📭 *Hiç üye yok\\.*")
        return

    lines = ["👥 *ÜYE LİSTESİ*", "━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for m in members:
        tier_icon = "💎" if m["tier"] == "prim" else "👑"
        tier_name = "PRIM" if m["tier"] == "prim" else "VIP"
        remaining_days = max(0, int((m["expires_at"] - time.time()) / 86400))
        expiry_date = datetime.fromtimestamp(m["expires_at"]).strftime("%Y-%m-%d")
        lines.append(
            f"{tier_icon} *{tier_name}* \\| `{m['user_id']}`\n"
            f"   └ Bitiş: `{expiry_date}` \\({remaining_days} gün\\)"
        )
    lines.append("")
    lines.append(f"_Toplam: {len(members)} üye_")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n_\\.\\.\\. liste kesildi_"
    await send_md(context.bot, update.effective_chat.id, text)


@admin_only
async def member_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 1:
        await send_md(context.bot, update.effective_chat.id, "❌ *Kullanım:* `/memberinfo <user_id>`")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await send_md(context.bot, update.effective_chat.id, "❌ *Geçersiz user_id\\!*")
        return

    m = membership_db.get_membership(target_id)
    if not m or m["tier"] == "none":
        await send_md(
            context.bot,
            update.effective_chat.id,
            f"❌ `{target_id}` *üye değil\\.*",
        )
        return

    tier = m["tier"]
    tier_icon = "💎" if tier == "prim" else "👑"
    tier_name = "PRIM" if tier == "prim" else "VIP"
    remaining_days = max(0, int((m["expires_at"] - time.time()) / 86400))
    expiry_date = datetime.fromtimestamp(m["expires_at"]).strftime("%Y-%m-%d %H:%M")
    daily_used = m.get("daily_count", 0)

    text = (
        f"{tier_icon} *ÜYE BİLGİSİ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 ID: `{target_id}`\n"
        f"💎 Tier: *{tier_name}*\n"
        f"📅 Bitiş: `{expiry_date}`\n"
        f"⏱️ Kalan: `{remaining_days}` gün\n"
        f"📊 Bugünkü Kullanım: `{daily_used}`"
    )
    await send_md(context.bot, update.effective_chat.id, text)


@admin_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = membership_db.get_stats()
    text = (
        "📊 *İSTATİSTİKLER*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Toplam Üye: `{stats['total']}`\n"
        f"💎 PRIM: `{stats['prim']}`\n"
        f"👑 VIP: `{stats['vip']}`"
    )
    await send_md(context.bot, update.effective_chat.id, text)


@admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await send_md(
            context.bot,
            update.effective_chat.id,
            "❌ *Kullanım:* `/broadcast <mesaj>`",
        )
        return
    msg = " ".join(args)
    members = membership_db.list_members()
    sent = 0
    failed = 0
    for m in members:
        try:
            await send_md(context.bot, m["user_id"], f"📢 *DUYURU*\n\n{md_escape(msg)}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await send_md(
        context.bot,
        update.effective_chat.id,
        f"✅ *Broadcast tamamlandı\\!*\n\n✅ Başarılı: `{sent}`\n❌ Başarısız: `{failed}`",
    )


# ═══════════════════════════════════════════════════════════
# 🚨 ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        import traceback
        traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    except Exception:
        pass
    if isinstance(update, Update) and update.effective_chat:
        try:
            await send_md(
                context.bot,
                update.effective_chat.id,
                "❌ *Bir hata oluştu\\.* Lütfen tekrar deneyin\\.",
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# 🚀 MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════╗")
    print("║   CPM1 CLONE BOT - PRIM/VIP v2.2            ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"Admin ID: {ADMIN_ID}")
    print(f"Bot token: {'*' * 10 if BOT_TOKEN else 'YOK!'}")
    print(f"Sonuç klasörü: {RESULTS_DIR}")
    print()

    if not BOT_TOKEN or BOT_TOKEN == "BURAYA_BOT_TOKEN_YAZ":
        print("❌ BOT_TOKEN ayarlanmamış!")
        print("   export BOT_TOKEN='123456:ABC...'")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("clone", clone_start)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_password)],
            COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_count)],
            SPEED: [CallbackQueryHandler(clone_speed, pattern=r"^speed_")],
            CONFIRM: [CallbackQueryHandler(clone_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("saved", saved_cmd))
    app.add_handler(CallbackQueryHandler(file_callback, pattern=r"^file_"))

    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addmember", add_member_cmd))
    app.add_handler(CommandHandler("removemember", remove_member_cmd))
    app.add_handler(CommandHandler("members", members_cmd))
    app.add_handler(CommandHandler("memberinfo", member_info_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    app.add_error_handler(error_handler)

    print("✅ Bot çalışıyor... Durdurmak için Ctrl+C")
    print()
    print("📋 Admin komutları:")
    print("   /admin - Admin paneli")
    print("   /addmember <user_id> <prim|vip> [1|2]")
    print("   /removemember <user_id>")
    print("   /members - Üye listesi")
    print("   /memberinfo <user_id>")
    print("   /stats - İstatistikler")
    print("   /broadcast <mesaj>")
    print()

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Çıkış.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Hata: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
