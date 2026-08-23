from __future__ import annotations

import base64
import csv
import html
import json
import mimetypes
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
PRODUCTS_CSV = APP_DIR / "products.csv"
MEMORY_JSON = APP_DIR / "memory.json"
CHAT_JSON = APP_DIR / "chat_history.json"
ADMIN_PENDING_JSON = APP_DIR / "admin_pending_media.json"
ORDERS_JSON = APP_DIR / "orders.json"
SEND_LOG_JSON = APP_DIR / "wati_send_log.json"
WEBHOOK_LOG_JSON = APP_DIR / "last_webhook_payload.json"
STORE_INFO_FILE = APP_DIR / "store_info.txt"
BANK_INFO_FILE = APP_DIR / "bank_info.txt"

load_dotenv(ENV_FILE, override=True)

app = Flask(__name__)

PRODUCT_FIELDS = [
    "id", "product_name", "code", "category", "subcategory",
    "price_retail", "price_wholesale", "price_dozen",
    "stock", "image_urls", "description", "keywords",
    "created_at", "updated_at",
]

DEFAULT_STORE_INFO = """Estamos ubicados en San Isidro, Santo Domingo Este, República Dominicana 😊

Horario: Lunes a sábado de 9:00 AM a 9:00 PM.
Teléfono / WhatsApp: 829-324-4477
Google Maps: https://maps.app.goo.gl/icojLCpGZsTrhUW6A?g_st=aw

Puedes enviarnos tu zona o dirección y te confirmamos la disponibilidad y el envío."""

DEFAULT_BANK_INFO = """Claro 😊 Puedes realizar el pago por transferencia.

Banco: BANCO BHD / BANCO POPULAR / BANRESERVAS
Cuenta: 34762070010 / 829434380 / 960119259
Nombre: MEWEARCORPORATION / MEWEARCORPORATION / SHUBIAOCHEN
Tipo: CORRIENTE

Cuando realices el pago, envíanos el comprobante para confirmar tu pedido ✅"""


# =============================
# Basic file helpers
# =============================

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def norm(text: Any) -> str:
    s = str(text or "").lower().strip()
    table = str.maketrans("áéíóúüñ", "aeiouun")
    return s.translate(table)


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text_file(path: Path, default: str) -> str:
    if not path.exists():
        path.write_text(default, encoding="utf-8")
    txt = path.read_text(encoding="utf-8", errors="replace").strip()
    return txt or default


def write_text_file(path: Path, value: str) -> None:
    path.write_text(str(value or "").strip(), encoding="utf-8")


def ensure_files() -> None:
    if not PRODUCTS_CSV.exists():
        with PRODUCTS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_FIELDS)
            writer.writeheader()

    for path, default in [
        (MEMORY_JSON, {}),
        (CHAT_JSON, {}),
        (ADMIN_PENDING_JSON, {}),
        (ORDERS_JSON, {}),
        (SEND_LOG_JSON, []),
    ]:
        if not path.exists():
            save_json(path, default)

    read_text_file(STORE_INFO_FILE, DEFAULT_STORE_INFO)
    read_text_file(BANK_INFO_FILE, DEFAULT_BANK_INFO)


ensure_files()


# =============================
# CSV products
# =============================

def normalize_price_string(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")
    # 1.500 in DR often means 1500
    if "." in s and "," not in s:
        parts = s.split(".")
        if len(parts[-1]) == 3:
            s = "".join(parts)
    if "," in s and "." not in s:
        parts = s.split(",")
        if len(parts[-1]) == 3:
            s = "".join(parts)
        else:
            s = s.replace(",", ".")
    s = s.replace(",", "")
    return s


def parse_money(value: Any) -> float:
    s = normalize_price_string(value)
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def money(value: Any) -> str:
    n = parse_money(value)
    if not n:
        return str(value or "")
    if float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}"


def load_products() -> List[Dict[str, str]]:
    ensure_files()
    rows: List[Dict[str, str]] = []
    with PRODUCTS_CSV.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            item = {field: str(r.get(field, "") or "") for field in PRODUCT_FIELDS}
            # Backward compatibility with old columns
            if not item["product_name"]:
                item["product_name"] = str(r.get("name") or r.get("nombre") or "")
            if not item["category"]:
                item["category"] = str(r.get("categoria") or "")
            if not item["image_urls"]:
                img = str(r.get("image_url") or r.get("foto") or r.get("photo_url") or "")
                item["image_urls"] = img
            if not item["id"]:
                item["id"] = "p_" + str(uuid.uuid4())[:8]
            rows.append(item)
    return rows


def save_products(rows: List[Dict[str, str]]) -> None:
    with PRODUCTS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRODUCT_FIELDS)
        writer.writeheader()
        for r in rows:
            out = {field: str(r.get(field, "") or "") for field in PRODUCT_FIELDS}
            writer.writerow(out)


def product_by_id(pid: str) -> Dict[str, str] | None:
    pid = str(pid or "").strip()
    for p in load_products():
        if p.get("id") == pid or p.get("code") == pid:
            return p
    return None


def upsert_product(product: Dict[str, str]) -> Tuple[Dict[str, str], bool]:
    rows = load_products()
    name = norm(product.get("product_name"))
    code = norm(product.get("code"))
    updated = False
    target = None

    for r in rows:
        if code and norm(r.get("code")) == code:
            target = r
            break
        if name and norm(r.get("product_name")) == name:
            target = r
            break

    if target is None:
        target = {field: "" for field in PRODUCT_FIELDS}
        target["id"] = "p_" + str(uuid.uuid4())[:8]
        target["created_at"] = now()
        rows.append(target)
    else:
        updated = True

    for field in PRODUCT_FIELDS:
        if field in product and product[field] != "":
            target[field] = str(product[field])

    if not target.get("category"):
        target["category"] = auto_category(target.get("product_name", ""))
    target["updated_at"] = now()
    save_products(rows)
    return target, updated


def delete_product(pid: str) -> bool:
    rows = load_products()
    before = len(rows)
    rows = [p for p in rows if p.get("id") != pid and p.get("code") != pid]
    save_products(rows)
    return len(rows) < before


# =============================
# Categories and product parsing
# =============================

CATEGORY_RULES = {
    "Juguetes": [
        "juguete", "muñeca", "muneca", "pelota", "carro", "slime", "bloques", "rompecabeza",
        "pistola de agua", "bebe lloron", "oso", "dinosaurio", "lego"
    ],
    "Muebles": [
        "silla", "mesa", "sofa", "sofá", "escritorio", "estante", "mueble", "gabinete",
        "organizador de baño", "organizador de bano", "zapatera", "repisa", "taburete"
    ],
    "Electrodomésticos": [
        "estufa", "licuadora", "abanico", "freidora", "plancha", "cafetera", "greca",
        "batidora", "tostadora", "calentador", "dispensador automatico", "dispensador automático"
    ],
    "Electrónicos y accesorios": [
        "audifono", "audífono", "audifonos", "audífonos", "cargador", "cable", "bocina",
        "speaker", "power bank", "usb", "bluetooth", "telefono", "teléfono", "mouse", "teclado",
        "lampara led", "lámpara led"
    ],
    "Hogar y cocina": [
        "olla", "sarten", "sartén", "bandeja", "vaso", "termo", "plato", "cuchara",
        "cuchillo", "tabla", "colador", "jarra", "cocina", "horno", "envase", "taza",
        "botella", "cubierto"
    ],
    "Belleza y cuidado personal": [
        "maquillaje", "peine", "espejo", "brocha", "labial", "pestaña", "pestana",
        "cosmetico", "cosmético", "secador", "rizador", "organizador de maquillaje",
        "manicure", "uñas", "unas"
    ],
    "Escolar y oficina": [
        "lapicero", "boligrafo", "bolígrafo", "pluma", "libreta", "cuaderno", "carpeta",
        "mochila", "regla", "marcador", "resaltador", "tijera escolar", "pegamento", "lápiz", "lapiz"
    ],
    "Limpieza y organización": [
        "escoba", "zafacon", "zafacón", "detergente", "limpieza", "mopa", "paño", "pano",
        "cepillo", "organizador", "caja organizadora", "basurero", "percha"
    ],
    "Ferretería y herramientas": [
        "martillo", "tornillo", "destornillador", "linterna", "herramienta", "taladro",
        "cinta metrica", "cinta métrica", "alicate", "candado"
    ],
    "Bebé": [
        "biberon", "biberón", "pañalera", "panalera", "coche", "bebé", "bebe", "sonajero",
        "chupete", "tetero"
    ],
    "Deportes": [
        "pesa", "yoga", "deporte", "balon", "balón", "raqueta", "guante", "bicicleta",
        "pelota fitness"
    ],
    "Decoración": [
        "decoracion", "decoración", "flor", "flores", "luces", "cuadro", "adorno", "cortina",
        "alfombra", "velas"
    ],
}


def auto_category(name: str, description: str = "") -> str:
    hay = norm(f"{name} {description}")
    for cat, words in CATEGORY_RULES.items():
        for w in words:
            if norm(w) in hay:
                return cat
    return "Variedades"


def keywords_for_product(p: Dict[str, str]) -> str:
    base = f"{p.get('product_name','')} {p.get('code','')} {p.get('category','')} {p.get('description','')}"
    words = [w for w in re.findall(r"[a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]+", norm(base)) if len(w) >= 3]
    return " ".join(sorted(set(words)))


def parse_admin_product_text(text: str) -> Dict[str, str]:
    data = {field: "" for field in PRODUCT_FIELDS}
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    free_lines: List[str] = []

    for line in lines:
        raw = line.strip()
        low = norm(raw)
        if ":" in raw:
            key, val = raw.split(":", 1)
        elif "：" in raw:
            key, val = raw.split("：", 1)
        else:
            key, val = "", raw
        k = norm(key)
        v = val.strip()

        if k in ["nombre", "producto", "product", "name", "产品名", "名称"]:
            data["product_name"] = v
        elif k in ["codigo", "código", "code", "sku", "编号"]:
            data["code"] = v
        elif k in ["precio", "detalle", "price", "零售价", "价格"]:
            data["price_retail"] = normalize_price_string(v)
        elif k in ["mayor", "por mayor", "wholesale", "批发价", "miembro", "precio miembro", "member", "member price", "会员价"]:
            data["price_wholesale"] = normalize_price_string(v)
        elif k in ["docena", "dozen", "一打价"]:
            data["price_dozen"] = normalize_price_string(v)
        elif k in ["categoria", "categoría", "category", "分类"]:
            data["category"] = v
        elif k in ["descripcion", "descripción", "description", "描述"]:
            data["description"] = v
        elif k in ["stock", "库存"]:
            data["stock"] = normalize_price_string(v)
        else:
            if low.startswith(("codigo ", "code ", "código ")):
                data["code"] = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            elif low.startswith(("precio ", "detalle ")):
                nums = re.findall(r"[\d.,]+", raw)
                if nums:
                    data["price_retail"] = normalize_price_string(nums[-1])
            elif low.startswith(("mayor ", "por mayor ", "miembro ", "precio miembro ", "会员价 ")):
                nums = re.findall(r"[\d.,]+", raw)
                if nums:
                    data["price_wholesale"] = normalize_price_string(nums[-1])
            elif low.startswith("docena "):
                nums = re.findall(r"[\d.,]+", raw)
                if nums:
                    data["price_dozen"] = normalize_price_string(nums[-1])
            elif low.startswith(("categoria ", "categoría ")):
                data["category"] = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            else:
                free_lines.append(raw)

    if not data["product_name"] and free_lines:
        # first free line as name, or "Product 250"
        first = free_lines[0]
        m = re.search(r"(.+?)\s+([\d.,]+)\s*$", first)
        if m:
            data["product_name"] = m.group(1).strip()
            if not data["price_retail"]:
                data["price_retail"] = normalize_price_string(m.group(2))
        else:
            data["product_name"] = first

    if not data["price_retail"]:
        nums = re.findall(r"[\d.,]+", text)
        if nums and data["product_name"]:
            data["price_retail"] = normalize_price_string(nums[-1])

    if not data["category"]:
        data["category"] = auto_category(data["product_name"], data["description"])
    data["keywords"] = keywords_for_product(data)
    return data


# =============================
# WATI send and payload parse
# =============================

def wati_base_url() -> str:
    load_dotenv(ENV_FILE, override=True)
    return (os.getenv("WATI_API_ENDPOINT") or os.getenv("WATI_BASE_URL") or "").strip().rstrip("/")


def wati_token() -> str:
    load_dotenv(ENV_FILE, override=True)
    return (os.getenv("WATI_TOKEN") or "").strip()


def send_wati_text(phone: str, text: str) -> bool:
    phone = clean_phone(phone)
    msg = str(text or "").strip()
    if not phone or not msg:
        return False

    base = wati_base_url()
    token = wati_token()
    if not base or not token:
        print("[YOME V2] Missing WATI_BASE_URL/WATI_API_ENDPOINT or WATI_TOKEN")
        return False

    url = f"{base}/api/v1/sendSessionMessage/{phone}?messageText={urllib.parse.quote(msg)}"
    auth_options = [token]
    if not token.lower().startswith("bearer "):
        auth_options.insert(0, "Bearer " + token)
    else:
        auth_options.append(token[7:].strip())

    last_error = ""
    for auth in auth_options:
        try:
            req = urllib.request.Request(
                url=url,
                data=b"",
                method="POST",
                headers={
                    "Authorization": auth,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "YOME-AI-V2/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as res:
                status = res.status
                body = res.read().decode("utf-8", errors="replace")
            ok = 200 <= status < 300
            log_item = {"time": now(), "phone": phone, "status": status, "ok": ok, "response": body[:1500], "message": msg[:800]}
            logs = load_json(SEND_LOG_JSON, [])
            logs.append(log_item)
            save_json(SEND_LOG_JSON, logs[-100:])
            print("[YOME V2 SEND]", phone, status, ok, body[:250])
            if ok:
                append_chat(phone, "assistant", msg)
                return True
        except urllib.error.HTTPError as e:
            last_error = e.read().decode("utf-8", errors="replace")
            print("[YOME V2 SEND HTTP ERROR]", getattr(e, "code", ""), last_error[:300])
        except Exception as e:
            last_error = str(e)
            print("[YOME V2 SEND ERROR]", last_error)

    logs = load_json(SEND_LOG_JSON, [])
    logs.append({"time": now(), "phone": phone, "ok": False, "error": last_error, "message": msg[:800]})
    save_json(SEND_LOG_JSON, logs[-100:])
    return False


def recursive_find_phone(obj: Any) -> str:
    if isinstance(obj, dict):
        for k in ["waId", "wa_id", "from", "phone", "sender", "sourceId", "whatsappNumber", "phoneNumber", "number", "mobile", "contactNumber", "customerPhone"]:
            p = clean_phone(obj.get(k))
            if p:
                return p
        for v in obj.values():
            p = recursive_find_phone(v)
            if p:
                return p
    elif isinstance(obj, list):
        for item in obj:
            p = recursive_find_phone(item)
            if p:
                return p
    return ""


def recursive_find_text(obj: Any) -> str:
    if isinstance(obj, dict):
        for k in ["text", "messageText", "body", "content", "caption", "msg"]:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (dict, list)):
                t = recursive_find_text(v)
                if t:
                    return t
        v = obj.get("message")
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (dict, list)):
            t = recursive_find_text(v)
            if t:
                return t
        for v in obj.values():
            if isinstance(v, (dict, list)):
                t = recursive_find_text(v)
                if t:
                    return t
    elif isinstance(obj, list):
        for item in obj:
            t = recursive_find_text(item)
            if t:
                return t
    return ""


def recursive_find_media(obj: Any, urls: List[str] | None = None, files: List[str] | None = None) -> Tuple[List[str], List[str]]:
    if urls is None:
        urls = []
    if files is None:
        files = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if isinstance(v, str):
                val = v.strip()
                if val.startswith(("http://", "https://")):
                    urls.append(val)
                if (
                    "filename" in key or "file_name" in key or key in ["file", "media", "url"]
                ) and (val.startswith("data/") or val.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf"))):
                    files.append(val)
            elif isinstance(v, (dict, list)):
                recursive_find_media(v, urls, files)
    elif isinstance(obj, list):
        for item in obj:
            recursive_find_media(item, urls, files)
    return urls, files


def parse_request_payload() -> Tuple[Dict[str, Any], str, str, List[str], str]:
    raw_body = request.get_data(cache=True, as_text=True) or ""
    data: Dict[str, Any] = {}

    j = request.get_json(silent=True)
    if isinstance(j, dict):
        data.update(j)

    if not data and raw_body.strip():
        try:
            j2 = json.loads(raw_body)
            if isinstance(j2, dict):
                data.update(j2)
        except Exception:
            pass

    if request.form:
        data.update(request.form.to_dict(flat=True))
    if request.args:
        data.update(request.args.to_dict(flat=True))

    phone = recursive_find_phone(data)
    text = recursive_find_text(data)

    if not phone and raw_body:
        for pat in [r'"waId"\s*:\s*"([^"]+)"', r'"from"\s*:\s*"([^"]+)"', r'"phone"\s*:\s*"([^"]+)"', r'waId=([^&\s]+)', r'from=([^&\s]+)']:
            m = re.search(pat, raw_body)
            if m:
                phone = clean_phone(m.group(1))
                if phone:
                    break
    if not text and raw_body:
        for pat in [r'"text"\s*:\s*"([^"]+)"', r'"messageText"\s*:\s*"([^"]+)"', r'"body"\s*:\s*"([^"]+)"', r'text=([^&]+)']:
            m = re.search(pat, raw_body)
            if m:
                text = urllib.parse.unquote_plus(m.group(1))
                break

    urls, files = recursive_find_media(data)
    media_urls: List[str] = []
    base = wati_base_url()
    for u in urls:
        low = u.lower()
        if "showfile" in low or any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp", ".pdf"]):
            media_urls.append(u)
    for f in files:
        if base:
            media_urls.append(f"{base}/api/file/showFile?fileName={urllib.parse.quote(f, safe='/._-')}")
    # de-duplicate
    media_urls = list(dict.fromkeys(media_urls))
    return data, phone, text, media_urls, raw_body


def is_outgoing(payload: Dict[str, Any]) -> bool:
    owner = payload.get("owner")
    event_type = str(payload.get("eventType", "") or "").lower()
    if owner is True or str(owner).lower() == "true":
        return True
    return "sent" in event_type


def admin_phones() -> set[str]:
    load_dotenv(ENV_FILE, override=True)
    raw = os.getenv("ADMIN_PHONES", "")
    return {clean_phone(x) for x in raw.split(",") if clean_phone(x)}


def is_admin(phone: str) -> bool:
    p = clean_phone(phone)
    for a in admin_phones():
        if p == a or (len(p) >= 8 and len(a) >= 8 and (p.endswith(a) or a.endswith(p))):
            return True
    return False


# =============================
# Chat and memory
# =============================

def append_chat(phone: str, role: str, message: str) -> None:
    phone = clean_phone(phone)
    data = load_json(CHAT_JSON, {})
    data.setdefault(phone, [])
    item = {"role": role, "message": message, "time": now()}
    if data[phone]:
        last = data[phone][-1]
        if last.get("role") == role and last.get("message") == message:
            return
    data[phone].append(item)
    data[phone] = data[phone][-200:]
    save_json(CHAT_JSON, data)


def get_memory(phone: str) -> Dict[str, Any]:
    phone = clean_phone(phone)
    mem = load_json(MEMORY_JSON, {})
    return mem.get(phone, {}) if isinstance(mem.get(phone), dict) else {}


def set_memory(phone: str, **kwargs: Any) -> None:
    phone = clean_phone(phone)
    mem = load_json(MEMORY_JSON, {})
    mem.setdefault(phone, {})
    if not isinstance(mem[phone], dict):
        mem[phone] = {}
    for k, v in kwargs.items():
        mem[phone][k] = v
    save_json(MEMORY_JSON, mem)


def clear_product_memory(phone: str) -> None:
    phone = clean_phone(phone)
    mem = load_json(MEMORY_JSON, {})
    mem.setdefault(phone, {})
    for k in ["last_product", "selected_product", "last_candidates", "awaiting_quantity"]:
        mem[phone].pop(k, None)
    save_json(MEMORY_JSON, mem)


# =============================
# Search and replies
# =============================

def product_search(query: str, limit: int = 6) -> List[Dict[str, str]]:
    q = norm(query)
    tokens = [w for w in re.findall(r"[a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]+", q) if len(w) >= 3]
    stop = {
        "tienes", "tiene", "precio", "quiero", "busco", "necesito", "hay", "mas", "más",
        "producto", "productos", "dame", "ver", "opciones", "modelos", "otro", "otra", "otros",
    }
    tokens = [w for w in tokens if w not in stop]
    scored: List[Tuple[int, Dict[str, str]]] = []
    for p in load_products():
        hay = norm(" ".join([
            p.get("product_name", ""),
            p.get("code", ""),
            p.get("category", ""),
            p.get("subcategory", ""),
            p.get("description", ""),
            p.get("keywords", ""),
        ]))
        score = 0
        for t in tokens:
            if t in hay:
                score += 4
            elif t.endswith("s") and t[:-1] in hay:
                score += 2
            elif (t + "s") in hay:
                score += 2
        if q and q in hay:
            score += 6
        if first_photo(p):
            score += 1
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for score, p in scored[:limit]]


def first_photo(product: Dict[str, str]) -> str:
    raw = product.get("image_urls") or ""
    parts = [x.strip() for x in re.split(r"[|,]\s*", raw) if x.strip()]
    for u in parts:
        low = u.lower()
        if u.startswith(("http://", "https://")) and "example.com" not in low and "wati.io" not in low and "showfile" not in low:
            return u
    return ""


def all_photos(product: Dict[str, str], max_count: int = 4) -> List[str]:
    raw = product.get("image_urls") or ""
    parts = [x.strip() for x in re.split(r"[|,]\s*", raw) if x.strip()]
    return [u for u in parts if u.startswith(("http://", "https://"))][:max_count]


def product_reply(product: Dict[str, str]) -> str:
    lines = [f"Sí 😊 Tenemos {product.get('product_name')}."]
    if product.get("price_retail"):
        lines.append(f"Precio regular: RD${money(product.get('price_retail'))} c/u.")
    if product.get("price_wholesale"):
        lines.append(f"Precio miembro: RD${money(product.get('price_wholesale'))} c/u.")
    if product.get("price_dozen"):
        lines.append(f"Precio docena desde 12 unidades: RD${money(product.get('price_dozen'))} c/u.")
    if product.get("code"):
        lines.append(f"Código: {product.get('code')}.")
    photos = all_photos(product)
    for i, u in enumerate(photos, 1):
        lines.append(f"Foto {i}: {u}" if len(photos) > 1 else f"Foto: {u}")
    lines.append("¿Cuántas deseas?")
    return "\n".join(lines)


def list_reply(products: List[Dict[str, str]], keyword: str = "producto") -> str:
    lines = [f"Sí 😊 Tenemos varias opciones de {keyword}.", "Te envío algunas con precio y foto:"]
    for i, p in enumerate(products[:6], 1):
        lines.append("")
        lines.append(f"{i}. {p.get('product_name')}")
        if p.get("price_retail"):
            lines.append(f"Precio regular: RD${money(p.get('price_retail'))}")
        if p.get("price_wholesale"):
            lines.append(f"Precio miembro: RD${money(p.get('price_wholesale'))}")
        if p.get("price_dozen"):
            lines.append(f"Precio docena: RD${money(p.get('price_dozen'))}")
        if p.get("code"):
            lines.append(f"Código: {p.get('code')}")
        photo = first_photo(p)
        if photo:
            lines.append(f"Foto: {photo}")
        else:
            lines.append("Foto: pendiente de subir.")
    lines.append("")
    lines.append("Puedes responder con 1, 2, 3 o 4 para elegir el modelo 😊")
    return "\n".join(lines)


def qty_reply(product: Dict[str, str], qty: int) -> str:
    retail = parse_money(product.get("price_retail"))
    member = parse_money(product.get("price_wholesale"))
    dozen = parse_money(product.get("price_dozen"))

    unit = retail
    rule = "precio regular"
    if member:
        unit = member
        rule = "precio miembro"
    if qty >= 12 and dozen and (not unit or dozen <= unit):
        unit = dozen
        rule = "precio docena"

    lines = [product_reply(product)]
    if unit:
        total = qty * unit
        lines.append("")
        lines.append(f"Para {qty} unidad(es), usando {rule}: RD${money(unit)} c/u.")
        lines.append(f"Total: RD${money(total)}")
        lines.append("Puedo ayudarte a completar el pedido ahora mismo.")
        lines.append("Envíame tu nombre, zona/dirección y método de pago 😊")
    return "\n".join(lines)


def no_product_reply() -> str:
    return (
        "Por ahora no tengo ese producto registrado 😊\n\n"
        "Si eres miembro de YOME, te puedo orientar con el precio miembro, pedidos y pagos desde aquí.\n"
        "También puedes decirme: quiero hacer un pedido de RD$7800.\n\n"
        "Trabajamos muchos productos de:\n"
        "🛒 Hogar y cocina\n"
        "🎧 Electrónicos y accesorios\n"
        "🔌 Electrodomésticos\n"
        "🪑 Muebles\n"
        "🧸 Juguetes\n"
        "💄 Belleza y cuidado personal\n"
        "📚 Escolar y oficina\n"
        "🧼 Limpieza y organización\n"
        "🛠️ Ferretería y herramientas\n"
        "🎁 Variedades\n\n"
        "Puedes enviarme una foto, nombre o código y te ayudo a revisar."
    )


def yome_product_name(product: Dict[str, Any] | None) -> str:
    if not isinstance(product, dict):
        return ""
    return str(product.get("product_name") or product.get("name") or product.get("nombre") or "").strip()


def yome_product_code(product: Dict[str, Any] | None) -> str:
    if not isinstance(product, dict):
        return ""
    return str(product.get("code") or product.get("codigo") or product.get("sku") or "").strip()


def yome_product_member_price(product: Dict[str, Any] | None) -> float:
    if not isinstance(product, dict):
        return 0
    return parse_money(product.get("price_wholesale") or product.get("mayor"))


def yome_product_regular_price(product: Dict[str, Any] | None) -> float:
    if not isinstance(product, dict):
        return 0
    return parse_money(product.get("price_retail") or product.get("price"))


def yome_product_dozen_price(product: Dict[str, Any] | None) -> float:
    if not isinstance(product, dict):
        return 0
    return parse_money(product.get("price_dozen") or product.get("docena"))


def yome_unit_price_for_order(product: Dict[str, Any] | None, qty: int) -> Tuple[float, str]:
    regular = yome_product_regular_price(product)
    member = yome_product_member_price(product)
    dozen = yome_product_dozen_price(product)
    unit = member or regular
    rule = "precio miembro" if member else "precio regular"
    if qty >= 12 and dozen and (not unit or dozen <= unit):
        unit = dozen
        rule = "precio docena"
    return unit, rule


def yome_membership_intent(text: str) -> bool:
    low = norm(text)
    return any(k in low for k in [
        "miembro", "membresia", "membresía", "socio", "club", "member", "membership",
        "precio miembro", "会员", "会员价", "会员系统", "会员价格", "优惠"
    ])


def yome_membership_reply() -> str:
    return (
        "Claro 😊 En YOME ser miembro te ayuda a comprar mejor:\n\n"
        "✅ Ves el precio miembro de los productos\n"
        "✅ Puedes pedir para hogar o negocio con atención más rápida\n"
        "✅ Te ayudamos a confirmar disponibilidad, pago y entrega\n\n"
        "En cada producto, el segundo precio es el precio miembro.\n"
        "Para ordenar, dime el producto o el monto. Ejemplo: quiero hacer un pedido de RD$7800."
    )


def yome_chinese_amount(text: str) -> float:
    s = str(text or "")
    digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if not any(ch in s for ch in digits) or not any(unit in s for unit in "千百十万"):
        return 0
    total = 0
    section = 0
    number = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for ch in s:
        if ch in digits:
            number = digits[ch]
        elif ch in units:
            section += (number or 1) * units[ch]
            number = 0
        elif ch == "万":
            total += (section + number) * 10000
            section = 0
            number = 0
    amount = total + section + number
    return float(amount if amount >= 100 else 0)


def yome_extract_amount(text: str) -> float:
    raw = str(text or "")
    cn = yome_chinese_amount(raw)
    if cn:
        return cn
    money_matches = re.findall(
        r"(?:rd\$|\$)?\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,7})(?:\s*(?:rd|dop|pesos?))?",
        raw,
        flags=re.I,
    )
    values = [parse_money(item) for item in money_matches]
    values = [value for value in values if value >= 100]
    return max(values) if values else 0


def yome_order_intent(text: str) -> bool:
    low = norm(text)
    if any(k in low for k in [
        "pedido", "orden", "ordenar", "comprar", "compra", "lo quiero", "separalo", "sepáralo",
        "hacer un pedido", "quiero pedir", "quiero comprar", "下单", "订单", "购买", "订购"
    ]):
        return True
    return bool(yome_extract_amount(text) and any(k in low for k in ["rd", "$", "peso", "下单", "订单", "pedido", "orden"]))


def yome_line_value(text: str, keys: List[str]) -> str:
    raw = str(text or "")
    key_pattern = "|".join(re.escape(k) for k in keys)
    match = re.search(rf"(?:{key_pattern})\s*[:：-]?\s*([^\n,;|]+)", raw, flags=re.I)
    return match.group(1).strip() if match else ""


def yome_extract_order_details(text: str) -> Dict[str, Any]:
    low = norm(text)
    details: Dict[str, Any] = {}
    amount = yome_extract_amount(text)
    if amount:
        details["amount"] = amount
    name = yome_line_value(text, ["nombre", "name", "cliente", "名字", "姓名", "联系人"])
    if name:
        details["customer_name"] = name
    address = yome_line_value(text, ["direccion", "dirección", "address", "zona", "ubicacion", "ubicación", "地址", "区域"])
    if address:
        details["address"] = address
    if "transferencia" in low or "deposito" in low or "depósito" in low or "banco" in low or "转账" in low:
        details["payment_method"] = "transferencia"
    elif "efectivo" in low or "cash" in low or "现金" in low:
        details["payment_method"] = "efectivo"
    elif "tarjeta" in low or "card" in low or "信用卡" in low:
        details["payment_method"] = "tarjeta"
    payment = yome_line_value(text, ["pago", "payment", "metodo de pago", "método de pago", "付款"])
    if payment:
        details["payment_method"] = payment
    return details


def yome_append_order_record(record: Dict[str, Any]) -> str:
    data = load_json(ORDERS_JSON, {})
    if isinstance(data, list):
        data = {"orders": data}
    if not isinstance(data, dict):
        data = {"orders": []}
    data.setdefault("orders", [])
    order_id = "YOME-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
    record["id"] = order_id
    record["created_at"] = now()
    record["status"] = "pending_payment"
    data["orders"].append(record)
    data["orders"] = data["orders"][-300:]
    save_json(ORDERS_JSON, data)
    return order_id


def yome_order_missing_fields(draft: Dict[str, Any]) -> List[str]:
    missing = []
    if not draft.get("amount") and not draft.get("product_name"):
        missing.append("producto o monto")
    if draft.get("product_name") and not draft.get("qty"):
        missing.append("cantidad")
    if not draft.get("customer_name"):
        missing.append("nombre")
    if not draft.get("address"):
        missing.append("zona/dirección")
    if not draft.get("payment_method"):
        missing.append("método de pago")
    return missing


def yome_order_flow_reply(phone: str, text: str, product: Dict[str, Any] | None = None, qty: int = 0, amount: float = 0) -> str:
    state = get_memory(phone)
    draft = state.get("order_draft") if isinstance(state.get("order_draft"), dict) else {}
    draft = dict(draft or {})
    details = yome_extract_order_details(text)
    if draft.get("amount") and details.get("amount") and not yome_order_intent(text):
        details.pop("amount", None)
    draft.update(details)
    if product:
        draft["product_name"] = yome_product_name(product)
        code = yome_product_code(product)
        if code:
            draft["code"] = code
        if qty:
            unit, rule = yome_unit_price_for_order(product, qty)
            draft["qty"] = qty
            draft["unit_price"] = unit
            draft["price_rule"] = rule
            if unit:
                draft["amount"] = qty * unit
    if amount:
        draft["amount"] = amount
    draft.setdefault("source", "chat")
    draft["phone"] = clean_phone(phone)
    set_memory(phone, order_draft=draft)

    missing = yome_order_missing_fields(draft)
    if missing:
        total = draft.get("amount")
        intro = "Perfecto 😊 puedo ayudarte a completar el pedido"
        if total:
            intro += f" por RD${money(total)}"
        return (
            intro
            + ".\n\nPara dejarlo listo, envíame lo que falta:\n"
            + "\n".join(f"- {item}" for item in missing)
            + "\n\nEjemplo: Nombre Juan, Dirección Santo Domingo Este, Pago transferencia."
        )

    order_id = yome_append_order_record(draft)
    set_memory(phone, order_draft={}, last_order_id=order_id)
    lines = [
        f"Listo ✅ Registré tu pedido {order_id}.",
        f"Cliente: {draft.get('customer_name')}",
        f"Dirección/Zona: {draft.get('address')}",
        f"Pago: {draft.get('payment_method')}",
    ]
    if draft.get("product_name"):
        lines.append(f"Producto: {draft.get('product_name')}")
    if draft.get("qty"):
        lines.append(f"Cantidad: {draft.get('qty')}")
    if draft.get("amount"):
        lines.append(f"Total: RD${money(draft.get('amount'))}")
    lines.append("")
    lines.append(read_text_file(BANK_INFO_FILE, DEFAULT_BANK_INFO))
    lines.append("")
    lines.append("Cuando realices el pago, envíanos el comprobante para confirmar y preparar la entrega 😊")
    return "\n".join(lines)


def extract_choice_and_qty(text: str, total_options: int) -> Tuple[int, int]:
    low = norm(text)
    option = 0
    qty = 0

    m = re.search(r"\bde\s*(?:la|el)?\s*(\d{1,2})\b", low)
    if m:
        option = int(m.group(1))
        before = low[:m.start()]
        nums_before = [int(x) for x in re.findall(r"\b(\d{1,3})\b", before)]
        if nums_before:
            qty = nums_before[-1]
    if not option:
        m = re.search(r"\b(?:la|el|opcion|opción|modelo|producto)\s*(\d{1,2})\b", low)
        if m:
            option = int(m.group(1))
            after = low[m.end():]
            nums_after = [int(x) for x in re.findall(r"\b(\d{1,3})\b", after)]
            if nums_after:
                qty = nums_after[0]
    if not option:
        nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", low)]
        if len(nums) == 1:
            option = nums[0]
        elif len(nums) >= 2:
            if 1 <= nums[-1] <= total_options:
                option = nums[-1]
                qty = nums[-2]
            elif 1 <= nums[0] <= total_options:
                option = nums[0]
                qty = nums[1]

    if option < 1 or option > total_options:
        return 0, 0
    return option, qty


# =============================
# Media upload / image recognition
# =============================

def download_wati_media(url: str) -> str:
    token = wati_token()
    auth_options = [token]
    if token and not token.lower().startswith("bearer "):
        auth_options.insert(0, "Bearer " + token)
    for auth in auth_options:
        try:
            req = urllib.request.Request(url, headers={"Authorization": auth, "User-Agent": "YOME-AI-V2/1.0"})
            with urllib.request.urlopen(req, timeout=30) as res:
                content = res.read()
                ctype = res.headers.get("Content-Type", "")
            suffix = ".jpg"
            if "png" in ctype:
                suffix = ".png"
            elif "webp" in ctype:
                suffix = ".webp"
            elif "pdf" in ctype:
                suffix = ".pdf"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(content)
            tmp.close()
            return tmp.name
        except Exception as e:
            print("[YOME V2 MEDIA DOWNLOAD]", e)
    return ""


def upload_cloudinary(local_file: str) -> Tuple[str, str]:
    load_dotenv(ENV_FILE, override=True)
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    api_key = os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
    if not cloud_name or not api_key or not api_secret:
        return "", "Cloudinary no configurado"
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
        res = cloudinary.uploader.upload(local_file, folder="yome_products", resource_type="auto")
        return res.get("secure_url") or res.get("url") or "", ""
    except Exception as e:
        return "", str(e)


def media_to_cloudinary(media_urls: List[str]) -> Tuple[List[str], List[str]]:
    uploaded: List[str] = []
    errors: List[str] = []
    for url in media_urls[:8]:
        local = download_wati_media(url)
        if not local:
            errors.append("No se pudo descargar la imagen de WATI")
            continue
        cloud, err = upload_cloudinary(local)
        if cloud:
            uploaded.append(cloud)
        else:
            errors.append(err or "No se pudo subir a Cloudinary")
    return uploaded, errors


def describe_image(local_file: str) -> str:
    load_dotenv(ENV_FILE, override=True)
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not local_file:
        return ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        mime = mimetypes.guess_type(local_file)[0] or "image/jpeg"
        data = base64.b64encode(Path(local_file).read_bytes()).decode("utf-8")
        data_url = f"data:{mime};base64,{data}"
        model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres asistente de tienda. Describe el producto en español con palabras clave. Máximo 12 palabras. No inventes precio."},
                {"role": "user", "content": [
                    {"type": "text", "text": "¿Qué producto aparece en esta imagen?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("[YOME V2 VISION]", e)
        return ""


# =============================
# Admin flow
# =============================

def handle_admin(phone: str, text: str, media_urls: List[str]) -> str:
    pending = load_json(ADMIN_PENDING_JSON, {})
    phone = clean_phone(phone)
    pending.setdefault(phone, {"photos": [], "errors": [], "updated_at": now()})

    if media_urls:
        uploaded, errors = media_to_cloudinary(media_urls)
        pending[phone]["photos"].extend(uploaded)
        pending[phone]["errors"].extend(errors)
        pending[phone]["updated_at"] = now()
        save_json(ADMIN_PENDING_JSON, pending)
        if not text:
            if uploaded:
                return (
                    "✅ Foto/archivo recibido y subido a la nube / 图片或文件已收到并上传云端\n\n"
                    "Ahora envía los datos del producto / 现在发送产品资料：\n"
                    "Nombre: \nCódigo: \nPrecio: \nMayor: \nDocena: \nCategoría: "
                )
            return (
                "⚠️ Foto recibida / 图片已收到\n"
                "Pero no se pudo subir a la nube / 但是没有上传到云端\n"
                + ("; ".join(errors[:2]) if errors else "")
                + "\n\nAhora envía los datos del producto / 现在发送产品资料。"
            )

    if text:
        product = parse_admin_product_text(text)
        if not product.get("product_name") or not product.get("price_retail"):
            return (
                "⚠️ No se guardó / 没有保存\n\n"
                "Falta nombre o precio / 缺少产品名或价格。\n\n"
                "Formato / 格式：\n"
                "Nombre: Lapicero panda\nCódigo: LP-001\nPrecio: 50\nMayor: 40\nDocena: 35"
            )

        photos = pending.get(phone, {}).get("photos", [])
        if photos:
            product["image_urls"] = "|".join(photos)
        if not product.get("category"):
            product["category"] = auto_category(product.get("product_name", ""), product.get("description", ""))
        product["keywords"] = keywords_for_product(product)
        saved, updated = upsert_product(product)
        pending.pop(phone, None)
        save_json(ADMIN_PENDING_JSON, pending)

        action = "actualizado" if updated else "guardado"
        action_cn = "更新成功" if updated else "保存成功"
        lines = [
            f"✅ Producto {action} correctamente / 产品{action_cn}",
            "",
            f"Nombre / 产品名: {saved.get('product_name')}",
            f"Categoría / 分类: {saved.get('category')}",
        ]
        if saved.get("code"):
            lines.append(f"Código / 编号: {saved.get('code')}")
        if saved.get("price_retail"):
            lines.append(f"Precio / 零售价: RD${money(saved.get('price_retail'))}")
        if saved.get("price_wholesale"):
            lines.append(f"Miembro / 会员价: RD${money(saved.get('price_wholesale'))}")
        if saved.get("price_dozen"):
            lines.append(f"Docena / 一打价: RD${money(saved.get('price_dozen'))}")
        if saved.get("image_urls"):
            lines.append(f"Fotos / 图片: {len(saved.get('image_urls').split('|'))}")
        else:
            lines.append("Fotos / 图片: pendiente de subir")
        return "\n".join(lines)

    return "Administrador / 管理员：envía una foto o los datos del producto."


# =============================
# Customer flow
# =============================

def customer_asks_location(text: str) -> bool:
    low = norm(text)
    return any(k in low for k in ["donde estan", "ubicados", "ubicacion", "direccion", "donde queda", "local", "sucursal"])


def customer_asks_payment(text: str) -> bool:
    low = norm(text)
    return any(k in low for k in ["como pago", "cuenta", "banco", "transferencia", "deposito", "metodo de pago"])


def only_greeting(text: str) -> bool:
    low = norm(text)
    business = ["tienes", "precio", "quiero", "busco", "necesito", "producto", "foto", "codigo", "pago", "cuenta", "direccion", "ubicacion"]
    if any(b in low for b in business):
        return False
    return low in ["hola", "buen dia", "buenos dias", "buenas", "hola buen dia", "hola buenos dias", "saludos"] or (len(low.split()) <= 4 and "hola" in low)


def customer_wants_change(text: str) -> bool:
    low = norm(text)
    return any(k in low for k in ["otro producto", "otra mercancia", "otra mercancía", "algo diferente", "no ese", "quiero otro", "otro modelo"])


def handle_customer(phone: str, text: str, media_urls: List[str]) -> str:
    append_chat(phone, "user", text or ("[图片/文件] " + " ".join(media_urls)))

    if media_urls:
        clear_product_memory(phone)
        desc = ""
        local = download_wati_media(media_urls[0])
        if local:
            desc = describe_image(local)
        matches = product_search(desc, limit=4) if desc else []
        if matches:
            set_memory(phone, last_candidates=matches)
            return list_reply(matches, desc or "producto")
        return (
            f"Recibí la foto ✅\n"
            + (f"Parece: {desc}\n" if desc else "")
            + "Ahora mismo no encontré ese producto exacto en el catálogo.\n"
            "Puedes enviarme el nombre o código para revisarlo mejor."
        )

    if not text:
        return "Puedes enviarme nombre, código o una foto del producto y te ayudo 😊"

    if customer_asks_location(text):
        clear_product_memory(phone)
        return read_text_file(STORE_INFO_FILE, DEFAULT_STORE_INFO)

    if customer_asks_payment(text):
        clear_product_memory(phone)
        return read_text_file(BANK_INFO_FILE, DEFAULT_BANK_INFO)

    if only_greeting(text):
        clear_product_memory(phone)
        return "¡Hola, buen día! 😊 Bienvenido a YOME.\n¿Qué producto estás buscando?\nPuedes enviarme nombre, código o una foto y te ayudo."

    if customer_wants_change(text):
        clear_product_memory(phone)
        return "Claro 😊 Buscamos otro producto.\nEnvíame una foto, nombre o código del producto que deseas."

    state = get_memory(phone)
    order_draft = state.get("order_draft") if isinstance(state.get("order_draft"), dict) else {}
    order_amount = yome_extract_amount(text)
    if order_draft or (order_amount and yome_order_intent(text)):
        return yome_order_flow_reply(phone, text, amount=order_amount)

    if yome_membership_intent(text) and not product_search(text, limit=1):
        return yome_membership_reply()

    candidates = state.get("last_candidates")
    if isinstance(candidates, list) and candidates:
        option, qty = extract_choice_and_qty(text, len(candidates))
        if option:
            product = candidates[option - 1]
            set_memory(phone, last_product=product, selected_product=product, awaiting_quantity=not bool(qty), last_candidates=[])
            if qty:
                return qty_reply(product, qty) + "\n\n" + yome_order_flow_reply(phone, text, product=product, qty=qty)
            return f"Perfecto 😊 Elegiste la opción {option}: {product.get('product_name')}.\n" + product_reply(product)

    nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", norm(text))]
    last_product = state.get("last_product") or state.get("selected_product")
    if nums and isinstance(last_product, dict):
        return qty_reply(last_product, nums[0]) + "\n\n" + yome_order_flow_reply(phone, text, product=last_product, qty=nums[0])

    matches = product_search(text, limit=6)
    if len(matches) == 1:
        set_memory(phone, last_product=matches[0], selected_product=matches[0], awaiting_quantity=True)
        return product_reply(matches[0])
    if len(matches) > 1:
        set_memory(phone, last_candidates=matches)
        # keyword for list title
        keyword = "producto"
        tokens = [w for w in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ]+", norm(text)) if len(w) >= 3]
        if tokens:
            keyword = tokens[-1]
        return list_reply(matches, keyword)

    clear_product_memory(phone)
    return no_product_reply()


# =============================
# Routes
# =============================

@app.post("/wati-webhook")
def wati_webhook():
    payload, phone, text, media_urls, raw_body = parse_request_payload()
    save_json(WEBHOOK_LOG_JSON, {"time": now(), "phone": phone, "text": text, "media_urls": media_urls, "payload": payload, "raw_preview": raw_body[:1000]})

    print("[YOME V2 WEBHOOK]", "phone=", phone, "text=", text, "media=", len(media_urls))

    if is_outgoing(payload):
        return jsonify({"status": "ignored_outgoing"})

    if not phone:
        return jsonify({"status": "no_phone", "keys": list(payload.keys()), "raw_preview": raw_body[:300]})

    if is_admin(phone):
        reply = handle_admin(phone, text, media_urls)
    else:
        reply = handle_customer(phone, text, media_urls)

    ok = send_wati_text(phone, reply)
    return jsonify({"status": "ok" if ok else "send_failed", "phone": phone, "reply_preview": reply[:200]})


def site_chat_json(payload: Dict[str, Any], status: int = 200):
    response = jsonify(payload)
    origin = (os.getenv("YOME_SITE_ALLOWED_ORIGIN") or "*").strip() or "*"
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-YOME-Widget-Key"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response, status


def site_chat_authorized() -> bool:
    widget_key = (os.getenv("YOME_SITE_WIDGET_KEY") or "").strip()
    if not widget_key:
        return True
    sent_key = (request.headers.get("X-YOME-Widget-Key") or "").strip()
    return bool(sent_key) and sent_key == widget_key


def site_chat_member_phone(member_id: Any, session_id: Any = "") -> str:
    member_digits = clean_phone(member_id)
    if member_digits:
        return "900000000" + member_digits[-12:]

    session_digits = clean_phone(session_id)
    if session_digits:
        return "900000999" + session_digits[-12:]

    return "900000999" + clean_phone(int(uuid.uuid4()))[-12:]


def site_chat_is_yome_question(message: str) -> bool:
    n = norm(message)
    if not n:
        return False

    yome_words = [
        "yome", "hola", "buenas", "ayuda", "asesor", "tienda", "catalogo", "catalogo",
        "producto", "productos", "precio", "precios", "mayor", "por mayor", "docena",
        "comprar", "pedido", "orden", "pago", "transferencia", "banco", "direccion",
        "ubicacion", "horario", "abierto", "envio", "delivery", "whatsapp", "contacto",
        "hogar", "cocina", "bano", "mueble", "muebles", "electronica", "ferreteria",
        "papeleria", "lampara", "silla", "mesa", "sofa", "organizador",
        "商品", "价格", "批发", "地址", "营业", "配送", "付款", "会员", "会员价", "客服", "订单",
        "product", "price", "wholesale", "delivery", "payment", "order", "member", "membership", "miembro",
    ]
    if any(word in n for word in yome_words):
        return True

    try:
        return bool(product_search(message, limit=1))
    except Exception:
        return False


@app.route("/site-chat", methods=["POST", "OPTIONS"])
def site_chat():
    if request.method == "OPTIONS":
        return site_chat_json({"status": "ok"})

    if not site_chat_authorized():
        return site_chat_json({"status": "error", "message": "Unauthorized"}, 401)

    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return site_chat_json({
            "status": "error",
            "message": "Escribe una pregunta sobre YOME para poder ayudarte.",
        }, 400)

    if not site_chat_is_yome_question(message):
        return site_chat_json({
            "status": "ok",
            "reply": (
                "Soy el asistente de YOME 😊\n"
                "Puedo ayudarte con productos, precios, catálogo, pagos, dirección, horario, "
                "entrega y pedidos de YOME."
            ),
            "scope": "yome_only",
        })

    phone = site_chat_member_phone(payload.get("member_id"), payload.get("session_id"))
    try:
        reply = handle_customer(phone, message, [])
    except Exception as exc:
        print("[YOME SITE CHAT] error:", exc)
        return site_chat_json({
            "status": "error",
            "message": "Ahora mismo no pude responder. Intenta otra vez en un momento.",
        }, 500)

    return site_chat_json({
        "status": "ok",
        "reply": reply,
        "assistant": "YOME",
    })


@app.get("/")
def root():
    return redirect("/manage")


@app.get("/manage")
def manage():
    products = load_products()
    chat = load_json(CHAT_JSON, {})
    photos = sum(1 for p in products if first_photo(p))
    categories = sorted(set(p.get("category") or "Variedades" for p in products))
    return f"""
<!doctype html><html><head><meta charset='utf-8'><title>YOME AI V2</title>
<style>
body{{font-family:Arial;margin:0;background:#f3f4f6;color:#111827}} .top{{background:#0f172a;color:white;padding:18px 24px}}
.container{{padding:24px;max-width:1100px;margin:auto}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
.card{{background:white;border-radius:16px;padding:18px;box-shadow:0 4px 14px #0001}} a.btn{{display:inline-block;background:#2563eb;color:white;text-decoration:none;padding:12px 16px;border-radius:12px;margin:6px 6px 0 0}}
.small{{color:#64748b}}
</style></head><body>
<div class='top'><h1>YOME AI V2 后台 / Panel Administrativo</h1><div>干净版 / Versión limpia</div></div>
<div class='container'>
<div class='grid'>
<div class='card'><h2>{len(products)}</h2><div>产品 / Productos</div></div>
<div class='card'><h2>{photos}</h2><div>有图片产品 / Con fotos</div></div>
<div class='card'><h2>{len(categories)}</h2><div>分类 / Categorías</div></div>
<div class='card'><h2>{len(chat)}</h2><div>客户聊天 / Chats</div></div>
</div>
<div class='card' style='margin-top:16px'>
<a class='btn' href='/product-admin'>产品管理 / Productos</a>
<a class='btn' href='/livechat'>聊天中心 / Chat</a>
<a class='btn' href='/bank-admin'>银行资料 / Banco</a>
<a class='btn' href='/store-info-admin'>店铺地址 / Dirección</a>
<a class='btn' href='/debug/config'>调试 / Debug</a>
</div>
<div class='card'><h3>分类 / Categorías</h3><p>{", ".join(html.escape(c) for c in categories) or "无"}</p></div>
</div></body></html>
"""


@app.route("/product-admin", methods=["GET"])
def product_admin():
    q = norm(request.args.get("q", ""))
    rows = load_products()
    if q:
        rows = [p for p in rows if q in norm(p.get("product_name", "") + " " + p.get("code", "") + " " + p.get("category", ""))]
    trs = []
    for p in rows:
        photo = first_photo(p)
        img = f"<img src='{html.escape(photo)}' style='width:70px;height:70px;object-fit:cover;border-radius:10px'>" if photo else ""
        trs.append(f"""
<tr>
<td>{img}</td><td>{html.escape(p.get('product_name',''))}</td><td>{html.escape(p.get('code',''))}</td>
<td>{html.escape(p.get('category',''))}</td><td>RD${money(p.get('price_retail'))}</td>
<td>RD${money(p.get('price_wholesale'))}</td><td>RD${money(p.get('price_dozen'))}</td>
<td><a href='/product-edit/{p.get('id')}'>编辑/Edit</a> | <a href='/product-delete/{p.get('id')}' onclick='return confirm("Eliminar?")'>删除/Eliminar</a></td>
</tr>""")
    return f"""
<!doctype html><html><head><meta charset='utf-8'><title>Productos</title>
<style>body{{font-family:Arial;background:#f3f4f6;padding:20px}} table{{width:100%;background:white;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}} .btn{{background:#2563eb;color:white;padding:10px 14px;border-radius:10px;text-decoration:none}}</style>
</head><body>
<h1>产品管理 / Productos</h1>
<a class='btn' href='/manage'>返回 / Volver</a> <a class='btn' href='/product-edit/new'>添加产品 / Agregar</a>
<form style='margin:15px 0'><input name='q' placeholder='搜索 / Buscar' value='{html.escape(request.args.get("q",""))}'><button>Buscar</button></form>
<table><tr><th>图片/Foto</th><th>产品/Nombre</th><th>编号/Código</th><th>分类/Categoría</th><th>零售/Regular</th><th>会员价/Miembro</th><th>一打/Docena</th><th>操作</th></tr>
{''.join(trs)}
</table></body></html>
"""


@app.route("/product-edit/<pid>", methods=["GET", "POST"])
def product_edit(pid):
    if pid == "new":
        p = {field: "" for field in PRODUCT_FIELDS}
        p["id"] = "new"
    else:
        p = product_by_id(pid) or {field: "" for field in PRODUCT_FIELDS}

    if request.method == "POST":
        item = {field: request.form.get(field, p.get(field, "")) for field in PRODUCT_FIELDS}
        if not item.get("id") or item.get("id") == "new":
            item["id"] = "p_" + str(uuid.uuid4())[:8]
            item["created_at"] = now()
        if not item.get("category"):
            item["category"] = auto_category(item.get("product_name", ""), item.get("description", ""))
        item["keywords"] = keywords_for_product(item)
        upsert_product(item)
        return redirect("/product-admin")

    def inp(name, label, typ="text"):
        val = html.escape(p.get(name, ""))
        if name in ["description", "image_urls", "keywords"]:
            return f"<label>{label}</label><textarea name='{name}'>{val}</textarea>"
        return f"<label>{label}</label><input name='{name}' value='{val}' type='{typ}'>"

    return f"""
<!doctype html><html><head><meta charset='utf-8'><title>Edit</title>
<style>body{{font-family:Arial;background:#f3f4f6;padding:20px}}form{{background:white;padding:20px;border-radius:16px;max-width:800px}}label{{display:block;margin-top:12px;font-weight:bold}}input,textarea{{width:100%;padding:10px;border:1px solid #ccc;border-radius:10px}}textarea{{height:90px}}button,.btn{{background:#2563eb;color:white;padding:12px 16px;border:0;border-radius:10px;text-decoration:none;margin-top:12px;display:inline-block}}</style>
</head><body><h1>编辑产品 / Editar producto</h1>
<form method='POST'>
{inp('product_name','产品名 / Nombre')}
{inp('code','编号 / Código')}
{inp('category','分类 / Categoría')}
{inp('subcategory','子分类 / Subcategoría')}
{inp('price_retail','零售价 / Precio detalle')}
{inp('price_wholesale','会员价 / Precio miembro')}
{inp('price_dozen','一打价 / Docena')}
{inp('stock','库存 / Stock')}
{inp('image_urls','图片链接，多张用 | 分开 / Fotos separadas por |')}
{inp('description','描述 / Descripción')}
{inp('keywords','关键词 / Palabras clave')}
<button>保存 / Guardar</button> <a class='btn' href='/product-admin'>返回 / Volver</a>
</form></body></html>
"""


@app.get("/product-delete/<pid>")
def product_delete(pid):
    delete_product(pid)
    return redirect("/product-admin")


@app.route("/bank-admin", methods=["GET", "POST"])
def bank_admin():
    if request.method == "POST":
        write_text_file(BANK_INFO_FILE, request.form.get("text", ""))
        return redirect("/bank-admin?saved=1")
    txt = html.escape(read_text_file(BANK_INFO_FILE, DEFAULT_BANK_INFO))
    saved = "<p style='background:#dcfce7;padding:10px'>Guardado / 保存成功 ✅</p>" if request.args.get("saved") else ""
    return f"<html><head><meta charset='utf-8'><style>body{{font-family:Arial;padding:20px}}textarea{{width:100%;height:300px}}</style></head><body><h1>银行资料 / Datos bancarios</h1>{saved}<form method='POST'><textarea name='text'>{txt}</textarea><br><button>保存 / Guardar</button> <a href='/manage'>返回 / Volver</a></form></body></html>"


@app.route("/store-info-admin", methods=["GET", "POST"])
def store_info_admin():
    if request.method == "POST":
        write_text_file(STORE_INFO_FILE, request.form.get("text", ""))
        return redirect("/store-info-admin?saved=1")
    txt = html.escape(read_text_file(STORE_INFO_FILE, DEFAULT_STORE_INFO))
    saved = "<p style='background:#dcfce7;padding:10px'>Guardado / 保存成功 ✅</p>" if request.args.get("saved") else ""
    return f"<html><head><meta charset='utf-8'><style>body{{font-family:Arial;padding:20px}}textarea{{width:100%;height:300px}}</style></head><body><h1>店铺地址 / Dirección</h1>{saved}<form method='POST'><textarea name='text'>{txt}</textarea><br><button>保存 / Guardar</button> <a href='/manage'>返回 / Volver</a></form></body></html>"


@app.get("/livechat")
def livechat():
    chat = load_json(CHAT_JSON, {})
    links = []
    for phone, msgs in chat.items():
        last = msgs[-1]["message"] if msgs else ""
        links.append(f"<li><a href='/livechat/{phone}'>{phone}</a> - {html.escape(last[:80])}</li>")
    return f"<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='5'><style>body{{font-family:Arial;padding:20px}}</style></head><body><h1>聊天中心 / Chat</h1><a href='/manage'>返回 / Volver</a><ul>{''.join(links)}</ul></body></html>"


@app.get("/livechat/<phone>")
def livechat_phone(phone):
    chat = load_json(CHAT_JSON, {})
    msgs = chat.get(clean_phone(phone), [])
    html_msgs = []
    for m in reversed(msgs[-100:]):
        html_msgs.append(f"<div style='background:white;margin:8px;padding:10px;border-radius:10px'><b>{m.get('role')}</b> <small>{m.get('time')}</small><br>{html.escape(m.get('message',''))}</div>")
    return f"<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='5'><style>body{{font-family:Arial;background:#f3f4f6;padding:20px}}</style></head><body><h1>{phone}</h1><a href='/livechat'>返回 / Volver</a>{''.join(html_msgs)}</body></html>"


@app.get("/debug/config")
def debug_config():
    return jsonify({
        "wati_base": wati_base_url(),
        "wati_token": "set" if wati_token() else "missing",
        "admin_phones": list(admin_phones()),
        "products": len(load_products()),
        "cloudinary": "set" if os.getenv("CLOUDINARY_CLOUD_NAME") and os.getenv("CLOUDINARY_API_KEY") and os.getenv("CLOUDINARY_API_SECRET") else "missing",
    })


@app.get("/debug/send-test")
def debug_send_test():
    phone = request.args.get("phone", "")
    msg = request.args.get("msg", "Prueba YOME V2 ✅")
    ok = send_wati_text(phone, msg)
    return jsonify({"ok": ok, "phone": phone, "msg": msg})


@app.get("/debug/last-webhook")
def debug_last_webhook():
    return jsonify(load_json(WEBHOOK_LOG_JSON, {}))


@app.get("/debug/send-log")
def debug_send_log():
    return jsonify(load_json(SEND_LOG_JSON, []))



# =============================
# YOME AI V2.2 聊天词库加强
# 重点：口语、拼写错误、更多产品、会员价、照片请求
# =============================

import csv as _v22_csv
import json as _v22_json
import re as _v22_re
import difflib as _v22_difflib
from pathlib import Path as _v22_Path
from flask import jsonify as _v22_jsonify

try:
    APP_DIR
except NameError:
    APP_DIR = _v22_Path("C:/yome_ai_v2")

try:
    PRODUCTS_CSV
except NameError:
    PRODUCTS_CSV = APP_DIR / "products.csv"

V22_MEMORY_FILE = APP_DIR / "memory.json"
V22_CHAT_WORDS_FILE = APP_DIR / "chat_words.json"


V22_DEFAULT_WORDS = {
    "greeting": [
        "hola", "buen dia", "buenos dias", "buenas", "buenas tardes",
        "buenas noches", "saludos", "bendiciones", "klk", "que lo que",
        "hello", "hi"
    ],
    "catalog": [
        "que mercancia hay", "que mercancía hay", "que productos hay",
        "que venden", "que tienen", "que hay", "ver catalogo", "ver catálogo",
        "mandame catalogo", "mándame catálogo", "catalogo", "catálogo",
        "mercancia", "mercancía", "productos disponibles"
    ],
    "payment": [
        "como pago", "como puedo pagar", "donde pago", "cuenta", "banco",
        "transferencia", "deposito", "depósito", "datos bancarios",
        "metodo de pago", "método de pago", "quiero pagar"
    ],
    "location": [
        "donde estan", "donde están", "donde estan ubicados", "donde están ubicados",
        "ubicacion", "ubicación", "direccion", "dirección", "donde queda",
        "local", "tienda fisica", "tienda física", "sucursal"
    ],
    "hours": [
        "horario", "a que hora", "a qué hora", "estan abiertos", "están abiertos",
        "hora cierran", "hora abren", "abren hoy", "cierran hoy"
    ],
    "delivery": [
        "delivery", "envio", "envío", "entrega", "domicilio", "mandan",
        "llevan", "hacen delivery", "para enviar", "cuanto el envio", "cuánto el envío"
    ],
    "more": [
        "mas", "más", "tienen mas", "tienen más", "hay mas", "hay más",
        "otros", "otras", "otro modelo", "mas modelos", "más modelos",
        "mas opciones", "más opciones", "quiero ver mas", "quiero ver más"
    ],
    "photo": [
        "foto", "imagen", "dame foto", "mandame foto", "mándame foto",
        "quiero verla", "quiero verlo", "ensename", "enséñame", "ver foto"
    ],
    "wholesale": [
        "mayor", "por mayor", "al mayor", "precio mayor", "mayoreo",
        "miembro", "precio miembro", "会员价", "会员价格",
        "docena", "por docena", "caja", "por caja", "cantidad"
    ],
    "change_product": [
        "otro producto", "otra cosa", "algo diferente", "no ese", "no quiero ese",
        "quiero otro", "otro modelo", "diferente", "otra mercancia", "otra mercancía"
    ],
    "yes": [
        "si", "sí", "ok", "okay", "dale", "claro", "perfecto", "lo quiero",
        "quiero ese", "me gusta", "separalo", "sepáralo"
    ]
}


def v22_load_words():
    if not V22_CHAT_WORDS_FILE.exists():
        V22_CHAT_WORDS_FILE.write_text(
            _v22_json.dumps(V22_DEFAULT_WORDS, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return V22_DEFAULT_WORDS

    try:
        data = _v22_json.loads(V22_CHAT_WORDS_FILE.read_text(encoding="utf-8", errors="replace"))
        for k, v in V22_DEFAULT_WORDS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return V22_DEFAULT_WORDS


def v22_norm(s):
    try:
        base = norm(s)
    except Exception:
        base = str(s or "").lower().strip()
        base = base.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n")
    return base


def v22_has_phrase(text, group):
    low = v22_norm(text)
    words = v22_load_words().get(group, [])
    return any(v22_norm(x) in low for x in words)


def v22_alias_word(w):
    w = v22_norm(w)

    aliases = {
        "lipicero": "lapicero",
        "lipiceros": "lapicero",
        "lapiceros": "lapicero",
        "lapisero": "lapicero",
        "lapiseros": "lapicero",
        "lapizero": "lapicero",
        "lapizeros": "lapicero",
        "pluma": "lapicero",
        "plumas": "lapicero",
        "boligrafo": "lapicero",
        "bolígrafo": "lapicero",
        "boligrafos": "lapicero",
        "bolígrafos": "lapicero",

        "audifonos": "audifono",
        "audífonos": "audifono",
        "auriculares": "audifono",
        "earphone": "audifono",
        "headphone": "audifono",

        "sillas": "silla",
        "mesas": "mesa",
        "sofas": "sofa",
        "sofá": "sofa",
        "sofás": "sofa",
        "organizadores": "organizador",
        "organisador": "organizador",
        "organisadores": "organizador",

        "muneca": "muñeca",
        "munecas": "muñeca",
        "muñecas": "muñeca",
        "juguetes": "juguete",

        "sartenes": "sarten",
        "sartén": "sarten",
        "ollas": "olla",
        "bandejas": "bandeja",
        "vasos": "vaso",
        "termos": "termo",

        "maquillajes": "maquillaje",
        "peines": "peine",

        "abanicos": "abanico",
        "estufas": "estufa",
        "licuadoras": "licuadora",
        "grecas": "greca",
    }

    return aliases.get(w, w)


def v22_tokens(text):
    low = v22_norm(text)
    raw = _v22_re.findall(r"[a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]+", low)

    stop = {
        "tienes", "tiene", "tienen", "hay", "quiero", "busco", "necesito",
        "precio", "cuanto", "cuánto", "dame", "ver", "opciones", "modelos",
        "otro", "otra", "otros", "otras", "mas", "más", "producto", "productos",
        "mercancia", "mercancía", "mercancias", "mercancías", "que", "qué",
        "cual", "cuál", "cuales", "cuáles", "venden", "vendes", "disponible",
        "por", "favor", "me", "puedes", "mandar", "enviar", "hola", "buenas"
    }

    tokens = []
    for w in raw:
        if len(w) < 3:
            continue
        w = v22_alias_word(w)
        if w in stop:
            continue
        tokens.append(w)

    return tokens


def v22_load_products():
    if not PRODUCTS_CSV.exists():
        return []

    try:
        with open(PRODUCTS_CSV, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            return [dict(r) for r in _v22_csv.DictReader(f)]
    except Exception:
        return []


def v22_p_name(p):
    return str(p.get("product_name") or p.get("name") or p.get("nombre") or "")


def v22_p_code(p):
    return str(p.get("code") or p.get("codigo") or p.get("sku") or "")


def v22_p_category(p):
    return str(p.get("category") or p.get("categoria") or "")


def v22_p_subcategory(p):
    return str(p.get("subcategory") or p.get("subcategoria") or "")


def v22_p_description(p):
    return str(p.get("description") or p.get("descripcion") or "")


def v22_product_words(p):
    hay = " ".join([
        v22_p_name(p),
        v22_p_code(p),
        v22_p_category(p),
        v22_p_subcategory(p),
        v22_p_description(p),
    ])

    words = _v22_re.findall(r"[a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]+", v22_norm(hay))
    return [v22_alias_word(w) for w in words if len(w) >= 3]


def product_search(query, limit=6):
    tokens = v22_tokens(query)

    if not tokens:
        return []

    scored = []

    for p in v22_load_products():
        pwords = v22_product_words(p)
        hay = " ".join(pwords)

        score = 0

        for t in tokens:
            if t in pwords:
                score += 12
            elif t in hay:
                score += 8
            else:
                best = 0
                for pw in pwords:
                    r = _v22_difflib.SequenceMatcher(None, t, pw).ratio()
                    if r > best:
                        best = r

                if best >= 0.82:
                    score += 6
                elif best >= 0.76 and len(t) >= 6:
                    score += 4

        try:
            if score > 0 and first_photo(p):
                score += 1
        except Exception:
            pass

        if score >= 6:
            scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for score, p in scored[:limit]]


def v22_catalog_reply():
    products = v22_load_products()
    cats = []

    for p in products:
        c = v22_p_category(p).strip()
        if c and c not in cats:
            cats.append(c)

    if cats:
        cats_text = "\n".join([f"• {c}" for c in cats[:12]])
        return (
            "Tenemos muchas variedades en YOME 😊\n\n"
            "Categorías disponibles / 可选分类：\n"
            f"{cats_text}\n\n"
            "Puedes decirme qué buscas o enviarme una foto y te ayudo a encontrarlo."
        )

    return (
        "Tenemos muchas variedades en YOME 😊\n\n"
        "Trabajamos productos de:\n"
        "🛒 Hogar y cocina\n"
        "🪑 Muebles y organizadores\n"
        "🔌 Electrodomésticos\n"
        "🎧 Electrónicos y accesorios\n"
        "🧸 Juguetes\n"
        "💄 Belleza y cuidado personal\n"
        "📚 Escolar y oficina\n"
        "🧼 Limpieza y organización\n"
        "🛠️ Ferretería y herramientas\n"
        "🎁 Variedades\n\n"
        "Puedes decirme qué buscas o enviarme una foto y te ayudo a encontrarlo."
    )


def v22_only_greeting(text):
    low = v22_norm(text)

    business = [
        "tienes", "tiene", "hay", "precio", "cuanto", "cuánto", "quiero",
        "busco", "necesito", "producto", "foto", "codigo", "código",
        "pago", "cuenta", "banco", "direccion", "dirección", "ubicacion",
        "ubicación", "envio", "envío", "delivery"
    ]

    if any(w in low for w in business):
        return False

    words = [w for w in _v22_re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ]+", low)]
    if not words:
        return False

    joined = " ".join(words)

    greetings = [v22_norm(x) for x in v22_load_words().get("greeting", [])]

    if joined in greetings:
        return True

    if len(words) <= 4 and any(g in joined for g in greetings):
        return True

    return False


def v22_delivery_reply():
    return (
        "Sí 😊 Podemos coordinar entrega según tu zona.\n"
        "Envíame tu ubicación o dirección y te confirmamos disponibilidad y costo de envío.\n\n"
        "也可以把你所在区域/地址发给我们，我们帮你确认配送。"
    )


def v22_wholesale_reply(product):
    name = product.get("product_name", "")
    member = product.get("price_wholesale", "")
    docena = product.get("price_dozen", "")
    price = product.get("price_retail", "")

    lines = [f"Para {name} 😊"]

    if price:
        lines.append(f"Precio regular: RD${money(price)} c/u.")
    if member:
        lines.append(f"Precio miembro: RD${money(member)} c/u.")
    if docena:
        lines.append(f"Precio docena desde 12 unidades: RD${money(docena)} c/u.")

    lines.append("¿Cuántas unidades deseas?")
    return "\n".join(lines)


def v22_photo_reply(product):
    try:
        foto = first_photo(product)
    except Exception:
        foto = ""

    if foto:
        return f"Claro 😊 Aquí tienes la foto de {product.get('product_name')}:\n{foto}"

    return "Todavía no tengo foto disponible para ese producto 😊"


def handle_customer(phone: str, text: str, media_urls: list[str]) -> str:
    try:
        append_chat(phone, "user", text or ("[图片/文件] " + " ".join(media_urls)))
    except Exception:
        pass

    if media_urls:
        try:
            clear_product_memory(phone)
        except Exception:
            pass

        desc = ""
        try:
            local = download_wati_media(media_urls[0])
            if local:
                desc = describe_image(local)
        except Exception:
            desc = ""

        matches = product_search(desc, limit=4) if desc else []

        if matches:
            set_memory(phone, last_candidates=matches)
            return list_reply(matches, desc or "producto")

        return (
            "Recibí la foto ✅\n"
            + (f"Parece: {desc}\n" if desc else "")
            + "Ahora mismo no encontré ese producto exacto en el catálogo.\n"
            "Puedes enviarme el nombre o código para revisarlo mejor."
        )

    if not text:
        return "Puedes enviarme nombre, código o una foto del producto y te ayudo 😊"

    low = v22_norm(text)

    # 1. 目录/有什么货
    if v22_has_phrase(text, "catalog"):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return v22_catalog_reply()

    # 2. 地址
    if v22_has_phrase(text, "location"):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return read_text_file(STORE_INFO_FILE, DEFAULT_STORE_INFO)

    # 3. 付款
    if v22_has_phrase(text, "payment"):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return read_text_file(BANK_INFO_FILE, DEFAULT_BANK_INFO)

    # 4. 营业时间，也用店铺资料回复
    if v22_has_phrase(text, "hours"):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return read_text_file(STORE_INFO_FILE, DEFAULT_STORE_INFO)

    # 5. 配送
    if v22_has_phrase(text, "delivery"):
        return v22_delivery_reply()

    # 6. 单纯问候
    if v22_only_greeting(text):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return "¡Hola, buen día! 😊 Bienvenido a YOME.\n¿Qué producto estás buscando?\nPuedes enviarme nombre, código o una foto y te ayudo."

    # 7. 换产品
    if v22_has_phrase(text, "change_product"):
        try:
            clear_product_memory(phone)
        except Exception:
            pass
        return "Claro 😊 Buscamos otro producto.\nEnvíame una foto, nombre o código del producto que deseas."

    state = get_memory(phone)
    order_draft = state.get("order_draft") if isinstance(state.get("order_draft"), dict) else {}
    order_amount = yome_extract_amount(text)
    if order_draft or (order_amount and yome_order_intent(text)):
        return yome_order_flow_reply(phone, text, amount=order_amount)

    if yome_membership_intent(text) and not product_search(text, limit=1):
        return yome_membership_reply()

    candidates = state.get("last_candidates")

    # 8. 列表选择
    if isinstance(candidates, list) and candidates:
        option, qty = extract_choice_and_qty(text, len(candidates))
        if option:
            product = candidates[option - 1]
            set_memory(phone, last_product=product, selected_product=product, awaiting_quantity=not bool(qty), last_candidates=[])
            if qty:
                return qty_reply(product, qty) + "\n\n" + yome_order_flow_reply(phone, text, product=product, qty=qty)
            return f"Perfecto 😊 Elegiste la opción {option}: {product.get('product_name')}.\n" + product_reply(product)

    last_product = state.get("last_product") or state.get("selected_product")

    # 9. 要照片
    if v22_has_phrase(text, "photo") and isinstance(last_product, dict):
        return v22_photo_reply(last_product)

    # 10. 问批发/一打
    if v22_has_phrase(text, "wholesale") and isinstance(last_product, dict):
        return v22_wholesale_reply(last_product)

    # 11. 直接数量
    nums = [int(x) for x in _v22_re.findall(r"\b(\d{1,3})\b", low)]
    if nums and isinstance(last_product, dict):
        return qty_reply(last_product, nums[0]) + "\n\n" + yome_order_flow_reply(phone, text, product=last_product, qty=nums[0])

    # 12. 是的/确认
    if low in [v22_norm(x) for x in v22_load_words().get("yes", [])] and isinstance(last_product, dict):
        return yome_order_flow_reply(phone, text, product=last_product)

    # 13. 产品搜索
    matches = product_search(text, limit=6)

    if len(matches) == 1:
        set_memory(phone, last_product=matches[0], selected_product=matches[0], awaiting_quantity=True)
        return product_reply(matches[0])

    if len(matches) > 1:
        set_memory(phone, last_candidates=matches)
        tokens = v22_tokens(text)
        keyword = tokens[-1] if tokens else "producto"
        return list_reply(matches, keyword)

    # 14. 没找到产品
    try:
        clear_product_memory(phone)
    except Exception:
        pass
    return no_product_reply()


@app.get("/debug/v22-chat-words")
def debug_v22_chat_words():
    return _v22_jsonify(v22_load_words())


print("[YOME V2.2] 聊天词库加强已开启")



# =============================
# YOME V2 存款/付款凭证后台
# Deposit Admin / 存款管理
# =============================

import os as _dep_os
import re as _dep_re
import json as _dep_json
import uuid as _dep_uuid
import tempfile as _dep_tempfile
import requests as _dep_requests
from pathlib import Path as _dep_Path
from datetime import datetime as _dep_datetime
from flask import request as _dep_request, jsonify as _dep_jsonify, redirect as _dep_redirect

try:
    APP_DIR
except NameError:
    APP_DIR = _dep_Path("C:/yome_ai_v2")

DEPOSITS_JSON = APP_DIR / "deposits.json"
CUSTOMERS_JSON = APP_DIR / "customer_profiles.json"
DEPOSIT_WAIT_JSON = APP_DIR / "deposit_waiting.json"
DEPOSIT_LOG_JSON = APP_DIR / "deposit_upload_log.json"


def dep_now():
    return _dep_datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def dep_clean_phone(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def dep_norm(s):
    s = str(s or "").lower().strip()
    s = s.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n")
    return s


def dep_load_json(path, default):
    try:
        if not path.exists():
            return default
        return _dep_json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def dep_save_json(path, data):
    path.write_text(_dep_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def dep_log(item):
    old = dep_load_json(DEPOSIT_LOG_JSON, [])
    old.append(item)
    dep_save_json(DEPOSIT_LOG_JSON, old[-80:])


def dep_find_phone(obj):
    if not isinstance(obj, dict):
        return ""

    keys = ["waId", "wa_id", "from", "phone", "sender", "sourceId", "whatsappNumber", "phoneNumber", "number"]
    for k in keys:
        p = dep_clean_phone(obj.get(k))
        if p:
            return p

    for k in ["contact", "contacts", "sender", "customer", "waContact", "message", "payload", "data"]:
        v = obj.get(k)
        if isinstance(v, dict):
            p = dep_find_phone(v)
            if p:
                return p
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    p = dep_find_phone(item)
                    if p:
                        return p

    return ""


def dep_find_text(obj):
    if not isinstance(obj, dict):
        return ""

    keys = ["text", "messageText", "body", "content", "caption", "msg"]

    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            t = dep_find_text(v)
            if t:
                return t

    v = obj.get("message")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        t = dep_find_text(v)
        if t:
            return t

    for v in obj.values():
        if isinstance(v, (dict, list)):
            t = dep_find_text(v)
            if t:
                return t

    return ""


def dep_find_media_urls(obj, found=None):
    if found is None:
        found = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                vv = v.strip()
                low = vv.lower()
                if vv.startswith(("http://", "https://")):
                    if any(x in low for x in [".jpg", ".jpeg", ".png", ".webp", ".pdf", "showfile"]):
                        found.append(vv)
                elif vv.startswith("data/images/") or vv.startswith("data/files/"):
                    base = (_dep_os.getenv("WATI_API_ENDPOINT") or _dep_os.getenv("WATI_BASE_URL") or "").rstrip("/")
                    if base:
                        found.append(base + "/api/file/showFile?fileName=" + vv)

            elif isinstance(v, (dict, list)):
                dep_find_media_urls(v, found)

    elif isinstance(obj, list):
        for x in obj:
            dep_find_media_urls(x, found)

    return found


def dep_is_outgoing(data):
    if not isinstance(data, dict):
        return False

    owner = data.get("owner")
    event_type = str(data.get("eventType", "") or "").lower()

    if owner is True or str(owner).lower() == "true":
        return True
    if "sent" in event_type:
        return True

    return False


def dep_is_admin(phone):
    p = dep_clean_phone(phone)

    try:
        if "is_admin" in globals() and is_admin(p):
            return True
    except Exception:
        pass

    try:
        if "ADMIN_PHONES" in globals():
            for a in ADMIN_PHONES:
                aa = dep_clean_phone(a)
                if p == aa or p.endswith(aa) or aa.endswith(p):
                    return True
    except Exception:
        pass

    return False


def dep_payment_words(text):
    low = dep_norm(text)
    keys = [
        "comprobante", "pague", "pagué", "pago", "pagado", "transferencia",
        "deposito", "depósito", "deposite", "deposité", "recibo",
        "voucher", "capture", "captura", "confirmar pago", "ya pague", "ya pagué"
    ]
    return any(dep_norm(k) in low for k in keys)


def dep_asks_payment(text):
    low = dep_norm(text)
    keys = ["como pago", "cuenta", "banco", "transferencia", "datos bancarios", "donde pago", "metodo de pago"]
    return any(dep_norm(k) in low for k in keys)


def dep_extract_amount(text):
    s = str(text or "")
    patterns = [
        r"RD\$?\s*([\d,.]+)",
        r"\$\s*([\d,.]+)",
        r"(?:monto|deposito|depósito|pago|transferencia)\s*(?:de)?\s*([\d,.]+)",
    ]
    for p in patterns:
        m = _dep_re.search(p, s, flags=_dep_re.I)
        if m:
            return m.group(1).strip()
    return ""


def dep_update_customer_profile(phone, text):
    phone = dep_clean_phone(phone)
    if not phone:
        return

    profiles = dep_load_json(CUSTOMERS_JSON, {})
    prof = profiles.setdefault(phone, {"phone": phone, "name": "", "address": "", "updated_at": dep_now()})

    t = str(text or "")

    name_patterns = [
        r"nombre\s*[:：]\s*(.+)",
        r"mi nombre es\s+(.+)",
        r"me llamo\s+(.+)",
        r"soy\s+(.+)",
    ]

    addr_patterns = [
        r"direccion\s*[:：]\s*(.+)",
        r"dirección\s*[:：]\s*(.+)",
        r"zona\s*[:：]\s*(.+)",
        r"ubicacion\s*[:：]\s*(.+)",
        r"ubicación\s*[:：]\s*(.+)",
    ]

    for p in name_patterns:
        m = _dep_re.search(p, t, flags=_dep_re.I)
        if m:
            val = m.group(1).strip()
            if 2 <= len(val) <= 80:
                prof["name"] = val
            break

    for p in addr_patterns:
        m = _dep_re.search(p, t, flags=_dep_re.I)
        if m:
            val = m.group(1).strip()
            if 2 <= len(val) <= 180:
                prof["address"] = val
            break

    prof["updated_at"] = dep_now()
    profiles[phone] = prof
    dep_save_json(CUSTOMERS_JSON, profiles)


def dep_download_media(url):
    url = str(url or "").strip()
    if not url:
        return "", "no_url"

    try:
        from dotenv import load_dotenv
        load_dotenv(APP_DIR / ".env", override=True)
    except Exception:
        pass

    token = _dep_os.getenv("WATI_TOKEN", "")
    auths = []

    if token:
        if token.lower().startswith("bearer "):
            auths.append(token)
            auths.append(token[7:].strip())
        else:
            auths.append("Bearer " + token)
            auths.append(token)

    auths.append("")

    last_error = ""

    for auth in auths:
        try:
            headers = {"User-Agent": "YOME-AI-V2-Deposit/1.0"}
            if auth:
                headers["Authorization"] = auth

            r = _dep_requests.get(url, headers=headers, timeout=30)

            if r.status_code == 200 and r.content:
                ctype = r.headers.get("content-type", "").lower()
                suffix = ".jpg"
                if "png" in ctype:
                    suffix = ".png"
                elif "webp" in ctype:
                    suffix = ".webp"
                elif "pdf" in ctype:
                    suffix = ".pdf"

                tmp = _dep_tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(r.content)
                tmp.close()
                return tmp.name, ""

            last_error = f"download_status_{r.status_code}"

        except Exception as e:
            last_error = str(e)

    return "", last_error


def dep_upload_cloudinary(local_file):
    try:
        from dotenv import load_dotenv
        load_dotenv(APP_DIR / ".env", override=True)
    except Exception:
        pass

    cloud_name = _dep_os.getenv("CLOUDINARY_CLOUD_NAME", "")
    api_key = _dep_os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = _dep_os.getenv("CLOUDINARY_API_SECRET", "")

    if not cloud_name or not api_key or not api_secret:
        return "", "cloudinary_not_configured"

    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )

        res = cloudinary.uploader.upload(local_file, folder="yome_deposits", resource_type="auto")
        url = res.get("secure_url") or res.get("url") or ""
        return url, "" if url else "cloudinary_no_url"

    except Exception as e:
        return "", str(e)


def dep_media_to_cloud(media_url):
    local, err = dep_download_media(media_url)
    if not local:
        return "", err

    cloud, err2 = dep_upload_cloudinary(local)
    if cloud:
        return cloud, ""

    return "", err2


def dep_create_record(phone, text, media_urls):
    phone = dep_clean_phone(phone)
    profiles = dep_load_json(CUSTOMERS_JSON, {})
    prof = profiles.get(phone, {"phone": phone, "name": "", "address": ""})

    amount = dep_extract_amount(text)

    image_urls = []
    errors = []

    for url in media_urls[:4]:
        cloud, err = dep_media_to_cloud(url)
        if cloud:
            image_urls.append(cloud)
        else:
            errors.append(err or "upload_failed")
            image_urls.append(url)

    records = dep_load_json(DEPOSITS_JSON, [])

    rec = {
        "id": "dep_" + dep_now().replace("-", "").replace(":", "").replace(" ", "_") + "_" + str(_dep_uuid.uuid4())[:8],
        "phone": phone,
        "name": prof.get("name", ""),
        "address": prof.get("address", ""),
        "amount": amount,
        "status": "pendiente",
        "image_urls": image_urls,
        "note": text or "",
        "errors": errors,
        "created_at": dep_now(),
        "updated_at": dep_now(),
    }

    records.insert(0, rec)
    dep_save_json(DEPOSITS_JSON, records)

    dep_log({
        "time": dep_now(),
        "action": "deposit_created",
        "phone": phone,
        "images": image_urls,
        "errors": errors,
    })

    return rec


def dep_set_waiting(phone, waiting=True):
    phone = dep_clean_phone(phone)
    data = dep_load_json(DEPOSIT_WAIT_JSON, {})
    if waiting:
        data[phone] = {"waiting": True, "updated_at": dep_now()}
    else:
        data.pop(phone, None)
    dep_save_json(DEPOSIT_WAIT_JSON, data)


def dep_is_waiting(phone):
    phone = dep_clean_phone(phone)
    data = dep_load_json(DEPOSIT_WAIT_JSON, {})
    return bool(data.get(phone, {}).get("waiting"))


def yome_deposit_capture_guard():
    try:
        if _dep_request.path != "/wati-webhook" or _dep_request.method != "POST":
            return

        data = _dep_request.get_json(silent=True)
        if not isinstance(data, dict):
            data = _dep_request.form.to_dict() if _dep_request.form else {}

        if not isinstance(data, dict):
            return

        if dep_is_outgoing(data):
            return

        phone = dep_find_phone(data)
        if not phone or dep_is_admin(phone):
            return

        text = dep_find_text(data)
        media_urls = dep_find_media_urls(data)

        if text:
            dep_update_customer_profile(phone, text)

        # 客户问付款，标记等待付款截图，但让原来的付款逻辑继续回复银行资料
        if text and dep_asks_payment(text):
            dep_set_waiting(phone, True)
            return

        # 有付款关键词 + 图片，或者刚问过付款后发图片 => 保存存款截图
        if media_urls and (dep_payment_words(text) or dep_is_waiting(phone)):
            rec = dep_create_record(phone, text, media_urls)
            dep_set_waiting(phone, False)

            send_wati_text(
                phone,
                "Comprobante recibido ✅\n"
                "Vamos a verificar el pago y te confirmamos por aquí.\n\n"
                "付款截图已收到，我们会核实后回复你。"
            )

            return _dep_jsonify({"status": "deposit_saved", "deposit_id": rec["id"]}), 200

    except Exception as e:
        print("[YOME DEPOSIT] error:", e)


try:
    funcs = app.before_request_funcs.setdefault(None, [])
    if yome_deposit_capture_guard in funcs:
        funcs.remove(yome_deposit_capture_guard)
    funcs.insert(0, yome_deposit_capture_guard)
    print("[YOME DEPOSIT] 存款/付款凭证捕捉已开启")
except Exception as e:
    print("[YOME DEPOSIT] 插入失败:", e)


@app.route("/deposit-admin", methods=["GET"])
def yome_deposit_admin():
    records = dep_load_json(DEPOSITS_JSON, [])
    q = str(_dep_request.args.get("q", "") or "").strip().lower()

    if q:
        records = [
            r for r in records
            if q in str(r.get("phone","")).lower()
            or q in str(r.get("name","")).lower()
            or q in str(r.get("address","")).lower()
            or q in str(r.get("status","")).lower()
        ]

    rows_html = ""

    if not records:
        rows_html = "<tr><td colspan='9' style='padding:20px;text-align:center;color:#666;'>暂无存款记录 / No hay comprobantes todavía</td></tr>"

    for r in records:
        imgs = ""
        for u in r.get("image_urls", []):
            if str(u).lower().endswith(".pdf"):
                imgs += f"<a href='{u}' target='_blank'>PDF</a><br>"
            else:
                imgs += f"<a href='{u}' target='_blank'><img src='{u}' style='width:90px;max-height:90px;object-fit:cover;border-radius:10px;border:1px solid #ddd;'></a><br>"

        errs = "<br>".join(r.get("errors", []) or [])

        rows_html += f"""
<tr>
<form method="POST" action="/deposit-admin/update/{r.get('id')}">
<td>{r.get('created_at','')}</td>
<td><input name="phone" value="{r.get('phone','')}" style="width:110px"></td>
<td><input name="name" value="{r.get('name','')}" style="width:130px"></td>
<td><textarea name="address" style="width:180px;height:55px">{r.get('address','')}</textarea></td>
<td><input name="amount" value="{r.get('amount','')}" style="width:80px"></td>
<td>
<select name="status">
  <option value="pendiente" {'selected' if r.get('status')=='pendiente' else ''}>pendiente / 待确认</option>
  <option value="verificado" {'selected' if r.get('status')=='verificado' else ''}>verificado / 已确认</option>
  <option value="parcial" {'selected' if r.get('status')=='parcial' else ''}>parcial / 部分付款</option>
  <option value="rechazado" {'selected' if r.get('status')=='rechazado' else ''}>rechazado / 已拒绝</option>
</select>
</td>
<td>{imgs}<small style="color:red">{errs}</small></td>
<td><textarea name="note" style="width:180px;height:55px">{r.get('note','')}</textarea></td>
<td>
<button type="submit">保存<br>Guardar</button>
<a href="/deposit-admin/delete/{r.get('id')}" onclick="return confirm('Eliminar / 删除?')" style="display:block;margin-top:8px;color:red;">删除</a>
</td>
</form>
</tr>
"""

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YOME 存款管理 / Depósitos</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:20px;}}
.header{{background:#0f172a;color:white;padding:18px;border-radius:16px;margin-bottom:18px;}}
a{{color:#2563eb;text-decoration:none;}}
.card{{background:white;padding:18px;border-radius:16px;box-shadow:0 4px 12px #0001;}}
table{{width:100%;border-collapse:collapse;background:white;}}
th,td{{border-bottom:1px solid #e5e7eb;padding:10px;vertical-align:top;font-size:14px;}}
th{{background:#eff6ff;text-align:left;}}
input,textarea,select{{border:1px solid #cbd5e1;border-radius:8px;padding:6px;}}
button{{background:#2563eb;color:white;border:0;border-radius:8px;padding:8px 12px;cursor:pointer;}}
.nav a{{color:white;margin-right:15px;}}
</style>
</head>
<body>
<div class="header">
<h1>YOME 存款管理 / Depósitos y comprobantes</h1>
<div class="nav">
<a href="/manage">总后台 / Panel</a>
<a href="/livechat">聊天 / Chat</a>
<a href="/product-admin">产品 / Productos</a>
<a href="/bank-admin">银行 / Banco</a>
</div>
</div>

<div class="card">
<form method="GET">
搜索 / Buscar:
<input name="q" value="{q}" placeholder="电话 / 名字 / 地址 / 状态" style="width:300px;">
<button type="submit">搜索 / Buscar</button>
<a href="/deposit-admin">清空 / Limpiar</a>
</form>
<br>
<table>
<thead>
<tr>
<th>时间<br>Fecha</th>
<th>电话<br>Teléfono</th>
<th>名字<br>Nombre</th>
<th>地址/区域<br>Dirección/Zona</th>
<th>金额<br>Monto</th>
<th>状态<br>Estado</th>
<th>存款图片<br>Comprobante</th>
<th>备注<br>Nota</th>
<th>操作<br>Acción</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
</body>
</html>
"""


@app.route("/deposit-admin/update/<dep_id>", methods=["POST"])
def yome_deposit_update(dep_id):
    records = dep_load_json(DEPOSITS_JSON, [])
    for r in records:
        if r.get("id") == dep_id:
            r["phone"] = dep_clean_phone(_dep_request.form.get("phone", r.get("phone","")))
            r["name"] = _dep_request.form.get("name", r.get("name",""))
            r["address"] = _dep_request.form.get("address", r.get("address",""))
            r["amount"] = _dep_request.form.get("amount", r.get("amount",""))
            r["status"] = _dep_request.form.get("status", r.get("status","pendiente"))
            r["note"] = _dep_request.form.get("note", r.get("note",""))
            r["updated_at"] = dep_now()
            break
    dep_save_json(DEPOSITS_JSON, records)
    return _dep_redirect("/deposit-admin")


@app.route("/deposit-admin/delete/<dep_id>", methods=["GET"])
def yome_deposit_delete(dep_id):
    records = dep_load_json(DEPOSITS_JSON, [])
    records = [r for r in records if r.get("id") != dep_id]
    dep_save_json(DEPOSITS_JSON, records)
    return _dep_redirect("/deposit-admin")


@app.get("/debug/deposit-log")
def yome_debug_deposit_log():
    return _dep_jsonify(dep_load_json(DEPOSIT_LOG_JSON, []))



# =============================
# YOME AI V2.3 地址/配送/Google Maps 修复
# Delivery + Address + Google Maps priority
# =============================

import re as _v23_re
import json as _v23_json
from pathlib import Path as _v23_Path
from datetime import datetime as _v23_datetime
from flask import request as _v23_request, jsonify as _v23_jsonify

try:
    APP_DIR
except NameError:
    APP_DIR = _v23_Path("C:/yome_ai_new")

V23_CUSTOMERS_JSON = APP_DIR / "customer_profiles.json"
V23_DELIVERY_WAIT_JSON = APP_DIR / "delivery_waiting.json"
V23_DELIVERY_LOG_JSON = APP_DIR / "delivery_address_log.json"


def v23_now():
    return _v23_datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def v23_clean_phone(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def v23_norm(text):
    text = str(text or "").lower().strip()
    text = text.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    return text


def v23_load_json(path, default):
    try:
        if not path.exists():
            return default
        return _v23_json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def v23_save_json(path, data):
    path.write_text(_v23_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def v23_log(item):
    data = v23_load_json(V23_DELIVERY_LOG_JSON, [])
    data.append(item)
    v23_save_json(V23_DELIVERY_LOG_JSON, data[-100:])


def v23_find_phone(obj):
    if not isinstance(obj, dict):
        return ""

    keys = [
        "waId", "wa_id", "from", "phone", "sender", "sourceId",
        "whatsappNumber", "phoneNumber", "number", "mobile"
    ]

    for k in keys:
        p = v23_clean_phone(obj.get(k))
        if p:
            return p

    for v in obj.values():
        if isinstance(v, dict):
            p = v23_find_phone(v)
            if p:
                return p
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    p = v23_find_phone(item)
                    if p:
                        return p

    return ""


def v23_find_text(obj):
    if not isinstance(obj, dict):
        return ""

    keys = ["text", "messageText", "body", "content", "caption", "msg"]

    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            t = v23_find_text(v)
            if t:
                return t

    v = obj.get("message")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        t = v23_find_text(v)
        if t:
            return t

    for v in obj.values():
        if isinstance(v, dict):
            t = v23_find_text(v)
            if t:
                return t
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    t = v23_find_text(item)
                    if t:
                        return t

    return ""


def v23_is_outgoing(data):
    if not isinstance(data, dict):
        return False

    owner = data.get("owner")
    event_type = str(data.get("eventType", "") or "").lower()

    if owner is True or str(owner).lower() == "true":
        return True

    if "sent" in event_type:
        return True

    return False


def v23_is_admin(phone):
    phone = v23_clean_phone(phone)

    try:
        if "is_admin" in globals() and is_admin(phone):
            return True
    except Exception:
        pass

    try:
        if "ADMIN_PHONES" in globals():
            for a in ADMIN_PHONES:
                aa = v23_clean_phone(a)
                if phone == aa or phone.endswith(aa) or aa.endswith(phone):
                    return True
    except Exception:
        pass

    return False


def v23_is_google_maps(text):
    low = str(text or "").lower()
    return (
        "google.com/maps" in low
        or "maps.app.goo.gl" in low
        or "goo.gl/maps" in low
        or "waze.com" in low
    )


def v23_extract_coordinates(text):
    s = str(text or "")
    m = _v23_re.search(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", s)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def v23_is_delivery_question(text):
    low = v23_norm(text)

    keys = [
        "envio", "envío", "delivery", "entrega", "domicilio",
        "mandan", "llevan", "hacen delivery",
        "cuanto es el envio", "cuanto envio", "costo de envio",
        "precio de envio", "quiero un envio", "quiero envio",
        "para enviar", "me lo envian", "me lo envían"
    ]

    return any(v23_norm(k) in low for k in keys)


def v23_is_address_text(text):
    low = v23_norm(text)

    if v23_is_google_maps(text):
        return True

    address_words = [
        "calle", "avenida", "av ", "av.", "residencial", "sector",
        "numero", "número", "num ", "no.", "casa", "apto", "apartamento",
        "edificio", "manzana", "km ", "carretera", "frente a",
        "al lado", "cerca de", "detras", "detrás", "entrada",
        "santo domingo", "san isidro", "sde", "santo domingo este",
        "los frailes", "alma rosa", "invivienda", "charles", "mendoza"
    ]

    return any(w in low for w in address_words)


def v23_waiting_address(phone):
    phone = v23_clean_phone(phone)
    data = v23_load_json(V23_DELIVERY_WAIT_JSON, {})
    return bool(data.get(phone, {}).get("waiting"))


def v23_set_waiting_address(phone, waiting=True):
    phone = v23_clean_phone(phone)
    data = v23_load_json(V23_DELIVERY_WAIT_JSON, {})

    if waiting:
        data[phone] = {"waiting": True, "updated_at": v23_now()}
    else:
        data.pop(phone, None)

    v23_save_json(V23_DELIVERY_WAIT_JSON, data)


def v23_update_customer_address(phone, text):
    phone = v23_clean_phone(phone)
    profiles = v23_load_json(V23_CUSTOMERS_JSON, {})

    profile = profiles.setdefault(phone, {
        "phone": phone,
        "name": "",
        "address": "",
        "zone": "",
        "location_url": "",
        "latitude": "",
        "longitude": "",
        "updated_at": v23_now()
    })

    raw = str(text or "").strip()
    low = v23_norm(raw)

    lat, lng = v23_extract_coordinates(raw)

    if v23_is_google_maps(raw):
        profile["location_url"] = raw

        if lat and lng:
            profile["latitude"] = lat
            profile["longitude"] = lng

    if v23_is_address_text(raw) and not v23_is_google_maps(raw):
        old = profile.get("address", "")
        if raw not in old:
            profile["address"] = (old + " " + raw).strip()

    zones = [
        "San Isidro",
        "Santo Domingo Este",
        "Los Frailes",
        "Invivienda",
        "Alma Rosa",
        "Mendoza",
        "Charles de Gaulle",
        "Villa Faro",
        "Ensanche Ozama"
    ]

    for z in zones:
        if v23_norm(z) in low:
            profile["zone"] = z
            break

    profile["updated_at"] = v23_now()
    profiles[phone] = profile
    v23_save_json(V23_CUSTOMERS_JSON, profiles)

    return profile


def v23_clear_product_memory(phone):
    try:
        if "clear_product_memory" in globals():
            clear_product_memory(phone)
            return
    except Exception:
        pass

    try:
        mem_file = APP_DIR / "memory.json"
        data = v23_load_json(mem_file, {})
        phone = v23_clean_phone(phone)

        if isinstance(data.get(phone), dict):
            for k in [
                "last_product", "selected_product", "last_candidates",
                "candidates", "last_products", "awaiting_quantity"
            ]:
                data[phone].pop(k, None)

        v23_save_json(mem_file, data)
    except Exception:
        pass


def v23_delivery_question_reply(text):
    low = v23_norm(text)

    if "san isidro" in low:
        return (
            "Sí 😊 realizamos envío para San Isidro.\n"
            "Por favor envíame tu dirección exacta o ubicación de Google Maps para confirmarte disponibilidad y costo de envío."
        )

    return (
        "Sí 😊 podemos coordinar entrega según tu zona.\n"
        "Por favor envíame tu dirección exacta o ubicación de Google Maps para confirmarte disponibilidad y costo de envío."
    )


def v23_address_received_reply(profile):
    zone = profile.get("zone", "")
    address = profile.get("address", "")
    location_url = profile.get("location_url", "")

    lines = ["Ubicación recibida ✅"]

    if zone:
        lines.append(f"Zona: {zone}")

    if address:
        lines.append(f"Dirección: {address}")

    if location_url:
        lines.append("Recibimos tu ubicación de Google Maps.")

    lines.append("")
    lines.append("Para completar la entrega, envíame tu nombre y un punto de referencia si tienes.")
    lines.append("Te confirmaremos disponibilidad y costo de envío.")

    return "\n".join(lines)


def yome_v23_delivery_address_guard():
    try:
        if _v23_request.path != "/wati-webhook" or _v23_request.method != "POST":
            return

        data = _v23_request.get_json(silent=True)
        if not isinstance(data, dict):
            data = _v23_request.form.to_dict() if _v23_request.form else {}

        if not isinstance(data, dict):
            return

        if v23_is_outgoing(data):
            return

        phone = v23_find_phone(data)

        if not phone or v23_is_admin(phone):
            return

        text = v23_find_text(data)

        if not text:
            return

        is_delivery = v23_is_delivery_question(text)
        is_address = v23_is_address_text(text)
        is_waiting = v23_waiting_address(phone)

        # 地址/配送必须优先，不允许进入产品搜索或数量逻辑
        if is_delivery or is_address or is_waiting:
            v23_clear_product_memory(phone)

            if is_delivery and not is_address:
                v23_set_waiting_address(phone, True)
                reply = v23_delivery_question_reply(text)
                send_wati_text(phone, reply)

                v23_log({
                    "time": v23_now(),
                    "phone": phone,
                    "type": "delivery_question",
                    "text": text,
                    "reply": reply
                })

                return _v23_jsonify({"status": "delivery_question_sent"}), 200

            profile = v23_update_customer_address(phone, text)
            v23_set_waiting_address(phone, False)

            reply = v23_address_received_reply(profile)
            send_wati_text(phone, reply)

            v23_log({
                "time": v23_now(),
                "phone": phone,
                "type": "address_received",
                "text": text,
                "profile": profile,
                "reply": reply
            })

            return _v23_jsonify({"status": "address_saved"}), 200

    except Exception as e:
        print("[YOME V2.3 DELIVERY] error:", e)


try:
    funcs = app.before_request_funcs.setdefault(None, [])

    if yome_v23_delivery_address_guard in funcs:
        funcs.remove(yome_v23_delivery_address_guard)

    # 一定要第一位：地址/地图/配送优先于产品、数量
    funcs.insert(0, yome_v23_delivery_address_guard)

    print("[YOME V2.3] 地址/配送/Google Maps 优先处理已开启")

except Exception as e:
    print("[YOME V2.3] 插入失败:", e)


@app.get("/debug/v23-delivery-log")
def debug_v23_delivery_log():
    return _v23_jsonify(v23_load_json(V23_DELIVERY_LOG_JSON, []))


@app.get("/debug/customer-profiles")
def debug_customer_profiles():
    return _v23_jsonify(v23_load_json(V23_CUSTOMERS_JSON, {}))






# === YOME SAFE ADMIN CENTER ONLY V1 ===
# Safe admin entrance only. No AI logic. No product save logic. No webhook logic.
import csv, time
from pathlib import Path
from flask import render_template_string, redirect, jsonify

def yome_safe_admin_route_exists_v1(path):
    try:
        return any(str(rule.rule) == path for rule in app.url_map.iter_rules())
    except Exception:
        return False

def yome_safe_admin_add_route_v1(path, endpoint, func):
    try:
        if not yome_safe_admin_route_exists_v1(path):
            app.add_url_rule(path, endpoint, func, methods=["GET"])
            print("[YOME SAFE ADMIN V1] added:", path)
        else:
            print("[YOME SAFE ADMIN V1] exists:", path)
    except Exception as e:
        print("[YOME SAFE ADMIN V1] add failed:", path, e)

def yome_safe_admin_count_csv_v1(path):
    try:
        path = Path(path)
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0

def yome_safe_admin_product_count_v1():
    data_file = Path("/data/products.csv")
    local_file = Path("products.csv")

    data_count = yome_safe_admin_count_csv_v1(data_file)
    local_count = yome_safe_admin_count_csv_v1(local_file)

    return max(data_count, local_count), data_count, local_count

def yome_safe_admin_center_v1():
    routes = sorted(str(rule.rule) for rule in app.url_map.iter_rules())
    route_set = set(routes)

    total, data_count, local_count = yome_safe_admin_product_count_v1()

    links = [
        ("旧版主后台 / App", "/app", "旧版本主后台，如果存在就从这里进"),
        ("产品后台 / Product Admin", "/product-admin", "旧版产品管理页面"),
        ("聊天后台 / LiveChat", "/livechat", "客户聊天页面"),
        ("客户中心 / Customer Center", "/customer-center", "客户资料和聊天记录"),
        ("付款后台 / Payment", "/payment-dashboard", "付款截图和付款记录"),
        ("最新产品 / Latest Products", "/latest-products", "查看最新产品"),
        ("产品同步检查", "/product-sync-check", "如果存在，用来检查 products.csv 和 /data/products.csv"),
        ("人工客服链接检查", "/human-support-link-check", "如果存在，用来检查人工客服链接"),
        ("AI 检查", "/ai-check", "如果存在，用来检查 AI 开关"),
        ("收到消息日志", "/debug/incoming-log", "如果存在，用来查看客户消息有没有进来"),
        ("发送日志", "/debug/wati-send-log", "如果存在，用来查看 WATI 发送记录"),
        ("路由列表", "/yome-routes", "查看当前版本所有页面"),
        ("健康检查", "/yome-health", "检查系统是否运行"),
    ]

    page = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YOME Safe Admin Center</title>
<style>
body{font-family:Arial,'Microsoft YaHei',sans-serif;background:#f5f7fb;margin:0;color:#111827;}
.header{background:#0f6bff;color:white;padding:24px 28px;}
.header h1{margin:0;font-size:34px;}
.wrap{padding:24px;}
.card{background:white;border-radius:18px;padding:18px;margin-bottom:18px;box-shadow:0 3px 12px rgba(0,0,0,.07);}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;}
.btn{display:block;background:white;border-radius:16px;padding:18px;text-decoration:none;color:#111827;box-shadow:0 2px 10px rgba(0,0,0,.06);border:2px solid transparent;}
.btn:hover{border-color:#0f6bff;background:#eef4ff;}
.missing{opacity:.55;background:#f3f4f6;}
.title{font-size:21px;font-weight:900;color:#0f6bff;}
.missing .title{color:#6b7280;}
.desc{font-size:14px;color:#6b7280;margin-top:6px;}
.badge{display:inline-block;margin-top:10px;padding:5px 9px;border-radius:999px;font-size:12px;font-weight:900;}
.ok{background:#dcfce7;color:#166534;}
.no{background:#fee2e2;color:#991b1b;}
.num{font-size:34px;color:#0f6bff;font-weight:900;}
pre{background:#111827;color:#e5e7eb;padding:14px;border-radius:12px;white-space:pre-wrap;}
</style>
</head>
<body>
<div class="header">
<h1>YOME 安全后台入口</h1>
<div>只做入口页面，不改 AI，不改产品保存</div>
</div>

<div class="wrap">
<div class="card">
<p><b>产品数量:</b> <span class="num">{{total}}</span></p>
<p>/data/products.csv：{{data_count}} 个</p>
<p>本地 products.csv：{{local_count}} 个</p>
</div>

<div class="grid">
{% for name,path,desc,exists in links %}
<a class="btn {% if not exists %}missing{% endif %}" href="{{path if exists else '#'}}">
<div class="title">{{name}}</div>
<div class="desc">{{desc}}</div>
{% if exists %}
<span class="badge ok">可以打开</span>
{% else %}
<span class="badge no">当前干净版没有这个页面</span>
{% endif %}
</a>
{% endfor %}
</div>

<div class="card" style="margin-top:20px;">
<h2>当前版本说明</h2>
<p>如果某个按钮显示“当前干净版没有这个页面”，不是坏了，是旧版本本来没有这个后台。</p>
<p>现在先用能打开的旧后台，不要再动产品保存逻辑。</p>
</div>
</div>
</body>
</html>
"""
    link_data = []
    for name, path, desc in links:
        link_data.append((name, path, desc, path in route_set))

    return render_template_string(
        page,
        links=link_data,
        total=total,
        data_count=data_count,
        local_count=local_count
    )

def yome_safe_admin_redirect_v1():
    return redirect("/admin-center")

def yome_safe_admin_health_v1():
    total, data_count, local_count = yome_safe_admin_product_count_v1()
    return jsonify({
        "ok": True,
        "message": "YOME app is running",
        "products_total": total,
        "products_data": data_count,
        "products_local": local_count,
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })

def yome_safe_admin_routes_v1():
    routes = sorted(str(rule.rule) for rule in app.url_map.iter_rules())
    return "<pre>" + "\n".join(routes) + "</pre>"

yome_safe_admin_add_route_v1("/admin-center", "yome_safe_admin_center_v1", yome_safe_admin_center_v1)
yome_safe_admin_add_route_v1("/admin", "yome_safe_admin_redirect_v1", yome_safe_admin_redirect_v1)
yome_safe_admin_add_route_v1("/manage-clean", "yome_safe_admin_manage_redirect_v1", yome_safe_admin_redirect_v1)
yome_safe_admin_add_route_v1("/yome-health", "yome_safe_admin_health_v1", yome_safe_admin_health_v1)
yome_safe_admin_add_route_v1("/yome-routes", "yome_safe_admin_routes_v1", yome_safe_admin_routes_v1)

print("[YOME SAFE ADMIN V1] Safe admin center ready: /admin-center")
# === END YOME SAFE ADMIN CENTER ONLY V1 ===







# === YOME PRODUCT RESCUE ONLY V1 ===
# 只做产品恢复工具，不改AI、不改产品识别、不改webhook
import csv, json, re, shutil, time
from pathlib import Path
from flask import request, render_template_string

YOME_RESCUE_DATA_DIR_V1 = Path("/data")
YOME_RESCUE_LOCAL_PRODUCTS_V1 = Path("products.csv")
YOME_RESCUE_DATA_PRODUCTS_V1 = YOME_RESCUE_DATA_DIR_V1 / "products.csv"
YOME_RESCUE_BACKUP_DIR_V1 = YOME_RESCUE_DATA_DIR_V1 / "backups"

YOME_RESCUE_HEADERS_V1 = [
    "name", "code", "price", "mayor", "docena",
    "category", "description", "image_url", "updated_at"
]

def yome_rescue_norm_v1(s):
    s = str(s or "").strip().lower()
    s = s.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
    return re.sub(r"\s+", " ", s)

def yome_rescue_count_v1(path):
    try:
        path = Path(path)
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0

def yome_rescue_find_col_v1(headers, options):
    headers = list(headers or [])
    norm_map = {yome_rescue_norm_v1(h): h for h in headers}

    for op in options:
        n = yome_rescue_norm_v1(op)
        if n in norm_map:
            return norm_map[n]

    for h in headers:
        nh = yome_rescue_norm_v1(h)
        for op in options:
            if yome_rescue_norm_v1(op) in nh:
                return h

    return ""

def yome_rescue_read_any_csv_v1(path):
    path = Path(path)
    if not path.exists():
        return [], []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
            return rows, headers
    except Exception as e:
        print("[YOME PRODUCT RESCUE V1] read error:", path, e)
        return [], []

def yome_rescue_candidate_files_v1():
    files = []

    for p in [
        YOME_RESCUE_DATA_PRODUCTS_V1,
        YOME_RESCUE_LOCAL_PRODUCTS_V1
    ]:
        if p.exists() and p not in files:
            files.append(p)

    try:
        for p in YOME_RESCUE_DATA_DIR_V1.glob("*products*.csv"):
            if p.exists() and p not in files:
                files.append(p)
    except Exception:
        pass

    try:
        for p in YOME_RESCUE_BACKUP_DIR_V1.glob("*products*.csv"):
            if p.exists() and p not in files:
                files.append(p)
    except Exception:
        pass

    try:
        for p in Path(".").glob("*products*.csv"):
            if p.exists() and p not in files:
                files.append(p)
    except Exception:
        pass

    return files

def yome_rescue_standardize_row_v1(row, headers):
    name_col = yome_rescue_find_col_v1(headers, ["name", "nombre", "producto", "product", "title"])
    code_col = yome_rescue_find_col_v1(headers, ["code", "codigo", "código", "sku", "cod"])
    price_col = yome_rescue_find_col_v1(headers, ["price", "precio", "detalle", "precio_venta", "retail"])
    mayor_col = yome_rescue_find_col_v1(headers, ["mayor", "por_mayor", "precio_mayor", "wholesale"])
    docena_col = yome_rescue_find_col_v1(headers, ["docena", "precio_docena", "dozen"])
    cat_col = yome_rescue_find_col_v1(headers, ["category", "categoria", "categoría"])
    desc_col = yome_rescue_find_col_v1(headers, ["description", "descripcion", "descripción", "desc"])
    img_col = yome_rescue_find_col_v1(headers, ["image_url", "imagen", "foto", "photo", "image"])
    upd_col = yome_rescue_find_col_v1(headers, ["updated_at", "fecha", "time", "created_at"])

    r = {h: "" for h in YOME_RESCUE_HEADERS_V1}
    r["name"] = str(row.get(name_col, "") if name_col else row.get("name", "")).strip()
    r["code"] = str(row.get(code_col, "") if code_col else row.get("code", "")).strip()
    r["price"] = str(row.get(price_col, "") if price_col else row.get("price", "")).strip()
    r["mayor"] = str(row.get(mayor_col, "") if mayor_col else row.get("mayor", "")).strip()
    r["docena"] = str(row.get(docena_col, "") if docena_col else row.get("docena", "")).strip()
    r["category"] = str(row.get(cat_col, "") if cat_col else row.get("category", "")).strip()
    r["description"] = str(row.get(desc_col, "") if desc_col else row.get("description", "")).strip()
    r["image_url"] = str(row.get(img_col, "") if img_col else row.get("image_url", "")).strip()
    r["updated_at"] = str(row.get(upd_col, "") if upd_col else row.get("updated_at", "")).strip()

    if not r["category"]:
        r["category"] = "General"

    return r

def yome_rescue_product_key_v1(row):
    code = yome_rescue_norm_v1(row.get("code", ""))
    name = yome_rescue_norm_v1(row.get("name", ""))

    if code:
        return "code:" + code
    if name:
        return "name:" + name
    return ""

def yome_rescue_score_v1(row):
    score = 0
    for k, v in row.items():
        if str(v or "").strip():
            score += 1
    if str(row.get("image_url", "")).startswith("http"):
        score += 3
    return score

def yome_rescue_merge_rows_v1():
    merged = {}
    order = []
    sources = []

    for path in yome_rescue_candidate_files_v1():
        rows, headers = yome_rescue_read_any_csv_v1(path)
        count = len(rows)

        sources.append({
            "file": str(path),
            "count": count
        })

        for row in rows:
            r = yome_rescue_standardize_row_v1(row, headers)
            key = yome_rescue_product_key_v1(r)

            if not key:
                continue

            # 过滤明显错误记录
            bad_name = yome_rescue_norm_v1(r.get("name", ""))
            bad_code = yome_rescue_norm_v1(r.get("code", ""))

            if bad_name.startswith("productos guardados") or bad_name.startswith("foto recibida"):
                continue
            if bad_code.startswith("actualizados") or bad_code.startswith("precio"):
                continue

            if key not in merged:
                merged[key] = r
                order.append(key)
            else:
                old = merged[key]

                # 先保留资料更多的一条，再补空
                if yome_rescue_score_v1(r) > yome_rescue_score_v1(old):
                    base = r
                    extra = old
                else:
                    base = old
                    extra = r

                for h in YOME_RESCUE_HEADERS_V1:
                    if not base.get(h) and extra.get(h):
                        base[h] = extra.get(h)

                merged[key] = base

    return [merged[k] for k in order], sources

def yome_rescue_backup_current_v1():
    try:
        YOME_RESCUE_BACKUP_DIR_V1.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        made = []

        for src, label in [
            (YOME_RESCUE_DATA_PRODUCTS_V1, "data_before_rescue"),
            (YOME_RESCUE_LOCAL_PRODUCTS_V1, "local_before_rescue")
        ]:
            if src.exists():
                dst = YOME_RESCUE_BACKUP_DIR_V1 / f"products_{label}_{ts}.csv"
                shutil.copy2(str(src), str(dst))
                made.append(str(dst))

        return made
    except Exception as e:
        print("[YOME PRODUCT RESCUE V1] backup error:", e)
        return []

def yome_rescue_write_both_v1(rows):
    YOME_RESCUE_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)

    for target in [YOME_RESCUE_DATA_PRODUCTS_V1, YOME_RESCUE_LOCAL_PRODUCTS_V1]:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=YOME_RESCUE_HEADERS_V1)
            writer.writeheader()
            for r in rows:
                writer.writerow({h: r.get(h, "") for h in YOME_RESCUE_HEADERS_V1})
        print("[YOME PRODUCT RESCUE V1] wrote:", target, len(rows))

def yome_product_rescue_v1():
    action = request.args.get("action", "")
    confirm = request.args.get("confirm", "").upper() == "YES"

    merged_rows, sources = yome_rescue_merge_rows_v1()
    message = ""

    if action == "merge_all" and confirm:
        yome_rescue_backup_current_v1()
        yome_rescue_write_both_v1(merged_rows)
        message = f"已合并恢复产品：{len(merged_rows)} 个"

    data_count = yome_rescue_count_v1(YOME_RESCUE_DATA_PRODUCTS_V1)
    local_count = yome_rescue_count_v1(YOME_RESCUE_LOCAL_PRODUCTS_V1)

    page = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YOME Product Rescue</title>
<style>
body{font-family:Arial,'Microsoft YaHei',sans-serif;background:#f5f7fb;padding:24px;color:#111827;}
.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 2px 10px rgba(0,0,0,.07);}
.ok{color:#16a34a;font-weight:900;font-size:24px;}
.warn{color:#dc2626;font-weight:900;font-size:22px;}
.btn{display:inline-block;background:#0f6bff;color:white;text-decoration:none;padding:14px 20px;border-radius:12px;font-size:20px;font-weight:900;margin:8px 0;}
.red{background:#dc2626;}
pre{background:#111827;color:#e5e7eb;padding:14px;border-radius:12px;white-space:pre-wrap;}
a{color:#0f6bff;font-weight:900;}
</style>
</head>
<body>
<h1>YOME 产品恢复工具</h1>

{% if message %}
<div class="card ok">{{message}}</div>
{% endif %}

<div class="card">
<p><b>/data/products.csv:</b> {{data_count}} 个</p>
<p><b>本地 products.csv:</b> {{local_count}} 个</p>
<p><b>可以合并恢复:</b> {{merge_count}} 个</p>
</div>

<div class="card">
<h2>一键恢复</h2>
<p>这个操作会把 /data、旧 products.csv、备份文件里的产品合并，去重后写回 /data/products.csv 和 products.csv。</p>
<a class="btn red" href="/product-rescue?action=merge_all&confirm=YES">确认合并恢复所有产品</a>
</div>

<div class="card">
<p><a href="/admin-center">返回后台</a> · <a href="/product-rescue">刷新检查</a></p>
</div>

<h2>找到的产品文件</h2>
<pre>{{sources}}</pre>

<h2>合并后的前20个产品预览</h2>
<pre>{{preview}}</pre>

</body>
</html>
"""
    return render_template_string(
        page,
        message=message,
        data_count=data_count,
        local_count=local_count,
        merge_count=len(merged_rows),
        sources=json.dumps(sources, ensure_ascii=False, indent=2),
        preview=json.dumps(merged_rows[:20], ensure_ascii=False, indent=2)
    )

try:
    if not any(str(rule.rule) == "/product-rescue" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/product-rescue", "yome_product_rescue_v1", yome_product_rescue_v1, methods=["GET"])
    print("[YOME PRODUCT RESCUE V1] ready: /product-rescue")
except Exception as e:
    print("[YOME PRODUCT RESCUE V1] route error:", e)

# === END YOME PRODUCT RESCUE ONLY V1 ===







# === YOME CUSTOMER CHAT RECORDS VIEWER ONLY V1 ===
# 只记录/查看客户消息，不删除聊天，不改产品保存，不改AI逻辑
import csv, json, re, time
from pathlib import Path
from flask import request, render_template_string, jsonify

try:
    YOME_CCR_DATA_DIR_V1 = Path("/data")
    YOME_CCR_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)
except Exception:
    YOME_CCR_DATA_DIR_V1 = Path(".")
    YOME_CCR_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)

YOME_CCR_LOG_V1 = YOME_CCR_DATA_DIR_V1 / "customer_chat_records_v1.jsonl"

def yome_ccr_digits_v1(s):
    return re.sub(r"[^0-9]", "", str(s or ""))

def yome_ccr_phone_v1(phone):
    d = yome_ccr_digits_v1(phone)
    if len(d) == 10 and d[:3] in ["809", "829", "849"]:
        return "1" + d
    return d

def yome_ccr_admins_v1():
    import os
    raw = os.getenv("YOME_ADMIN_NUMBERS") or os.getenv("ADMIN_NUMBERS") or ""
    nums = []
    for x in re.split(r"[,;\s]+", raw):
        d = yome_ccr_phone_v1(x)
        if d:
            nums.append(d)
    for d in ["18293244477", "18495037888"]:
        if d not in nums:
            nums.append(d)
    return nums

def yome_ccr_is_admin_v1(phone):
    p = yome_ccr_phone_v1(phone)
    return any(p == a or p.endswith(a) or a.endswith(p) for a in yome_ccr_admins_v1())

def yome_ccr_walk_strings_v1(obj):
    arr = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, str):
            if x.strip():
                arr.append(x.strip())
        elif isinstance(x, (int, float)):
            arr.append(str(x))
    try:
        walk(obj)
    except Exception:
        pass
    return arr

def yome_ccr_is_outgoing_v1(data):
    txt = str(data).lower()
    flags = [
        "'fromme': true", '"fromme": true',
        "'isfromme': true", '"isfromme": true',
        "'direction': 'outbound'", '"direction": "outbound"',
        "'status': 'sent'", '"status": "sent"',
        "'status': 'delivered'", '"status": "delivered"',
        "'status': 'read'", '"status": "read"',
        "'eventtype': 'message_sent'", '"eventtype": "message_sent"',
    ]
    return any(x in txt for x in flags)

def yome_ccr_extract_v1(data):
    msg = ""
    phone = ""

    if not isinstance(data, dict):
        return msg, phone

    for k in ["text", "body", "message", "messageText", "textMessage", "caption", "content"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            msg = msg or v

    for k in ["waId", "wa_id", "from", "sender", "whatsappNumber", "phone", "phoneNumber"]:
        v = data.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            phone = phone or str(v)

    try:
        msgs = data.get("messages", [])
        if msgs:
            one = msgs[0]
            msg = msg or one.get("body", "") or one.get("text", {}).get("body", "") or one.get("caption", "")
            phone = phone or one.get("from", "")
    except Exception:
        pass

    try:
        contacts = data.get("contacts", [])
        if contacts:
            phone = phone or contacts[0].get("wa_id", "")
    except Exception:
        pass

    if not msg:
        strings = [s for s in yome_ccr_walk_strings_v1(data) if not s.startswith("http")]
        if strings:
            msg = sorted(strings, key=len, reverse=True)[0]

    return str(msg or "").strip(), yome_ccr_phone_v1(phone)

def yome_ccr_find_urls_v1(data):
    urls = []
    for s in yome_ccr_walk_strings_v1(data):
        for u in re.findall(r"https?://[^\s\"'<>]+", s):
            u = u.strip().rstrip(".,;)")
            if u not in urls:
                urls.append(u)
    return urls[:10]

def yome_ccr_append_record_v1(record):
    try:
        YOME_CCR_LOG_V1.parent.mkdir(parents=True, exist_ok=True)
        with YOME_CCR_LOG_V1.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[YOME CCR V1] append error:", e)















# === YOME PRODUCT AUTO REPLY READ ONLY V2 ===
# 客户问产品 -> 自动从产品目录读取并回复价格/图片
# 只读 products.csv，不删除、不覆盖、不修改产品目录
import csv, json, re, time, hashlib, unicodedata, os
from pathlib import Path
from flask import request, render_template_string

try:
    YOME_PAR_DATA_DIR_V2 = Path("/data")
    YOME_PAR_DATA_DIR_V2.mkdir(parents=True, exist_ok=True)
except Exception:
    YOME_PAR_DATA_DIR_V2 = Path(".")
    YOME_PAR_DATA_DIR_V2.mkdir(parents=True, exist_ok=True)

YOME_PAR_DATA_PRODUCTS_V2 = YOME_PAR_DATA_DIR_V2 / "products.csv"
YOME_PAR_LOCAL_PRODUCTS_V2 = Path("products.csv")
YOME_PAR_STATE_V2 = YOME_PAR_DATA_DIR_V2 / "product_auto_reply_v2_state.json"
YOME_PAR_CHAT_LOG_V2 = YOME_PAR_DATA_DIR_V2 / "customer_chat_records_v1.jsonl"

YOME_PAR_PUBLIC_URL_V2 = os.getenv(
    "YOME_PUBLIC_URL",
    "https://repository-name-yome-ai-new-production.up.railway.app"
).rstrip("/")

YOME_PAR_SUPPORT_PHONE_V2 = re.sub(
    r"[^0-9]",
    "",
    os.getenv("YOME_HUMAN_SUPPORT_PHONE", "18293244477")
)

def yome_par_digits_v2(s):
    return re.sub(r"[^0-9]", "", str(s or ""))

def yome_par_phone_v2(phone):
    d = yome_par_digits_v2(phone)
    if len(d) == 10 and d[:3] in ["809", "829", "849"]:
        return "1" + d
    return d

def yome_par_norm_v2(s):
    s = str(s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9ñ\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def yome_par_admins_v2():
    raw = os.getenv("YOME_ADMIN_NUMBERS") or os.getenv("ADMIN_NUMBERS") or ""
    nums = []
    for x in re.split(r"[,;\s]+", raw):
        d = yome_par_phone_v2(x)
        if d:
            nums.append(d)
    for d in ["18293244477", "18495037888"]:
        if d not in nums:
            nums.append(d)
    return nums

def yome_par_is_admin_v2(phone):
    p = yome_par_phone_v2(phone)
    return any(p == a or p.endswith(a) or a.endswith(p) for a in yome_par_admins_v2())

def yome_par_is_outgoing_v2(data):
    txt = str(data).lower()
    flags = [
        "'fromme': true", '"fromme": true',
        "'isfromme': true", '"isfromme": true',
        "'direction': 'outbound'", '"direction": "outbound"',
        "'status': 'sent'", '"status": "sent"',
        "'status': 'delivered'", '"status": "delivered"',
        "'status': 'read'", '"status": "read"',
        "'eventtype': 'message_sent'", '"eventtype": "message_sent"',
    ]
    return any(x in txt for x in flags)

def yome_par_walk_strings_v2(obj):
    arr = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, str):
            if x.strip():
                arr.append(x.strip())
        elif isinstance(x, (int, float)):
            arr.append(str(x))
    try:
        walk(obj)
    except Exception:
        pass
    return arr

def yome_par_extract_v2(data):
    msg = ""
    phone = ""

    if not isinstance(data, dict):
        return msg, phone

    for k in ["text", "body", "message", "messageText", "textMessage", "caption", "content"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            msg = msg or v

    for k in ["waId", "wa_id", "from", "sender", "whatsappNumber", "phone", "phoneNumber"]:
        v = data.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            phone = phone or str(v)

    try:
        msgs = data.get("messages", [])
        if msgs:
            one = msgs[0]
            msg = msg or one.get("body", "") or one.get("text", {}).get("body", "") or one.get("caption", "")
            phone = phone or one.get("from", "")
    except Exception:
        pass

    try:
        contacts = data.get("contacts", [])
        if contacts:
            phone = phone or contacts[0].get("wa_id", "")
    except Exception:
        pass

    if not msg:
        strings = [s for s in yome_par_walk_strings_v2(data) if not s.startswith("http")]
        if strings:
            msg = sorted(strings, key=len, reverse=True)[0]

    return str(msg or "").strip(), yome_par_phone_v2(phone)

def yome_par_append_chat_v2(phone, msg):
    try:
        if not phone or not msg:
            return
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phone": yome_par_phone_v2(phone),
            "message": str(msg)[:3000],
            "urls": [],
            "source": "product_auto_reply_v2"
        }
        with YOME_PAR_CHAT_LOG_V2.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[YOME PAR V2] chat log error:", e)

def yome_par_count_csv_v2(path):
    try:
        path = Path(path)
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0

def yome_par_best_products_file_v2():
    data_count = yome_par_count_csv_v2(YOME_PAR_DATA_PRODUCTS_V2)
    local_count = yome_par_count_csv_v2(YOME_PAR_LOCAL_PRODUCTS_V2)
    if YOME_PAR_DATA_PRODUCTS_V2.exists() and data_count >= local_count:
        return YOME_PAR_DATA_PRODUCTS_V2
    return YOME_PAR_LOCAL_PRODUCTS_V2

def yome_par_col_v2(headers, names):
    norm_map = {yome_par_norm_v2(h): h for h in headers}
    for n in names:
        nn = yome_par_norm_v2(n)
        if nn in norm_map:
            return norm_map[nn]
    for h in headers:
        nh = yome_par_norm_v2(h)
        for n in names:
            if yome_par_norm_v2(n) in nh:
                return h
    return ""

def yome_par_read_products_v2():
    path = yome_par_best_products_file_v2()
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])

            name_col = yome_par_col_v2(headers, ["name", "nombre", "producto", "product", "title"])
            code_col = yome_par_col_v2(headers, ["code", "codigo", "código", "sku", "cod"])
            price_col = yome_par_col_v2(headers, ["price", "precio", "detalle", "precio_venta"])
            mayor_col = yome_par_col_v2(headers, ["mayor", "por_mayor", "precio_mayor"])
            docena_col = yome_par_col_v2(headers, ["docena", "precio_docena", "dozen"])
            cat_col = yome_par_col_v2(headers, ["category", "categoria", "categoría"])
            desc_col = yome_par_col_v2(headers, ["description", "descripcion", "descripción", "desc"])
            img_col = yome_par_col_v2(headers, ["image_url", "foto", "imagen", "photo", "image"])

            products = []
            for r in reader:
                name = str(r.get(name_col, "")).strip()
                if not name:
                    continue

                # 过滤之前错误保存的系统回复
                bad_name = yome_par_norm_v2(name)
                bad_code = yome_par_norm_v2(r.get(code_col, ""))
                if bad_name.startswith("productos guardados") or bad_name.startswith("foto recibida"):
                    continue
                if bad_code.startswith("actualizados") or bad_code.startswith("precio"):
                    continue

                products.append({
                    "name": name,
                    "code": str(r.get(code_col, "")).strip(),
                    "price": str(r.get(price_col, "")).strip(),
                    "mayor": str(r.get(mayor_col, "")).strip(),
                    "docena": str(r.get(docena_col, "")).strip(),
                    "category": str(r.get(cat_col, "")).strip(),
                    "description": str(r.get(desc_col, "")).strip(),
                    "image_url": str(r.get(img_col, "")).strip(),
                })
            return products
    except Exception as e:
        print("[YOME PAR V2] read products error:", e)
        return []

def yome_par_money_v2(v):
    s = str(v or "").strip()
    if not s:
        return ""
    nums = re.findall(r"\d[\d,.]*", s)
    if not nums:
        return s
    n = re.sub(r"[^0-9]", "", nums[-1])
    if not n:
        return s
    try:
        return "RD$" + f"{int(n):,}"
    except Exception:
        return "RD$" + n

def yome_par_tokens_v2(s):
    stop = {
        "que","precio","cuanto","cuesta","cual","valor","tiene","tienes","hay",
        "de","la","el","los","las","con","para","quiero","me","das","dime",
        "por","favor","una","uno","un","en","del","rd","foto","fotos",
        "opcion","opción","numero","número","disponible","disponibles"
    }
    return [t for t in yome_par_norm_v2(s).split() if t not in stop and len(t) >= 2]

def yome_par_load_state_v2():
    try:
        if YOME_PAR_STATE_V2.exists():
            data = json.loads(YOME_PAR_STATE_V2.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("enabled", True)
                data.setdefault("context", {})
                data.setdefault("last", {})
                data.setdefault("logs", [])
                return data
    except Exception:
        pass
    return {"enabled": True, "context": {}, "last": {}, "logs": []}

def yome_par_save_state_v2(data):
    try:
        now = time.time()
        data["last"] = {k:v for k,v in data.get("last", {}).items() if now - float(v.get("time", 0)) < 3600}
        data["logs"] = data.get("logs", [])[-150:]
        YOME_PAR_STATE_V2.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[YOME PAR V2] save state error:", e)

def yome_par_find_matches_v2(query, limit=5):
    products = yome_par_read_products_v2()
    qn = yome_par_norm_v2(query)
    qtokens = set(yome_par_tokens_v2(query))

    matches = []
    for p in products:
        name = p.get("name", "")
        code = p.get("code", "")
        desc = p.get("description", "")
        cat = p.get("category", "")

        text = f"{name} {code} {desc} {cat}"
        tn = yome_par_norm_v2(text)
        ntokens = set(yome_par_tokens_v2(text))

        score = 0

        if code and yome_par_norm_v2(code) in qn:
            score += 120

        if name and yome_par_norm_v2(name) in qn:
            score += 100

        common = qtokens.intersection(ntokens)
        score += len(common) * 18

        if qtokens and len(common) == len(qtokens):
            score += 30

        if score > 0:
            matches.append((score, p))

    matches.sort(key=lambda x: x[0], reverse=True)

    clean = []
    seen = set()
    for score, p in matches:
        key = (p.get("code") or p.get("name")).lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(p)
        if len(clean) >= limit:
            break

    return clean

def yome_par_format_product_v2(p, index=None):
    if index is not None:
        out = f"{index}. {p.get('name','')}\n"
    else:
        out = f"Sí 😊 Tenemos {p.get('name','')}.\n"

    if p.get("code"):
        out += f"Código: {p.get('code')}\n"
    if p.get("price"):
        out += f"Precio regular: {yome_par_money_v2(p.get('price'))}\n"
    if p.get("mayor"):
        out += f"Precio miembro: {yome_par_money_v2(p.get('mayor'))}\n"
    if p.get("docena"):
        out += f"Precio docena: {yome_par_money_v2(p.get('docena'))}\n"
    if p.get("image_url"):
        out += f"Foto: {p.get('image_url')}\n"

    return out.strip()

def yome_par_catalog_link_v2():
    return YOME_PAR_PUBLIC_URL_V2 + "/products-view"

def yome_par_support_link_v2():
    phone = yome_par_phone_v2(YOME_PAR_SUPPORT_PHONE_V2) or "18293244477"
    return f"https://wa.me/{phone}?text=Hola%2C%20quiero%20hablar%20con%20un%20asesor%20de%20YOME"

def yome_par_human_intent_v2(msg):
    n = yome_par_norm_v2(msg)
    keys = [
        "asesor","humano","agente","representante","persona",
        "servicio al cliente","soporte","no quiero hablar con ai",
        "no quiero hablar con ia","人工","客服","真人"
    ]
    return any(k in n or k in str(msg) for k in keys)

def yome_par_bot_quote_v2(msg):
    n = yome_par_norm_v2(msg)
    if "si tenemos varias opciones" in n and "te envio algunas con precio" in n:
        return True
    if "perfecto elegiste la opcion" in n:
        return True
    if "responde con el numero" in n:
        return True
    if ("foto:" in n or "https://res.cloudinary.com" in n) and (
        "precio:" in n or "precio regular" in n or "precio miembro" in n or "precio docena" in n
        or "codigo:" in n or "código:" in n
    ):
        return True
    return False

def yome_par_recent_duplicate_v2(phone, incoming, reply, seconds=90):
    state = yome_par_load_state_v2()
    key_raw = yome_par_phone_v2(phone) + "|" + yome_par_norm_v2(incoming)[:300] + "|" + yome_par_norm_v2(reply)[:300]
    key = hashlib.sha256(key_raw.encode("utf-8", errors="ignore")).hexdigest()[:20]
    item = state.get("last", {}).get(key)
    if item and time.time() - float(item.get("time", 0)) < seconds:
        return True

    state.setdefault("last", {})[key] = {
        "time": time.time(),
        "phone": yome_par_phone_v2(phone),
        "incoming": str(incoming)[:150],
        "reply": str(reply)[:150]
    }
    yome_par_save_state_v2(state)
    return False

def yome_par_send_v2(phone, msg):
    phone = yome_par_phone_v2(phone)
    if not phone:
        return False

    for fname in ["send_wati_text", "wati_send_text", "send_text_message", "send_message", "wati_send_message", "send_wati_message"]:
        try:
            fn = globals().get(fname)
            if fn:
                fn(phone, msg)
                print("[YOME PAR V2] sent by", fname, "to", phone)
                return True
        except Exception as e:
            print("[YOME PAR V2] send failed:", fname, e)

    print("[YOME PAR V2] no send function found")
    return False

def yome_par_make_reply_v2(phone, msg):
    state = yome_par_load_state_v2()
    phone_key = yome_par_phone_v2(phone)

    if yome_par_human_intent_v2(msg):
        return (
            "Claro 😊 Si prefieres hablar con un asesor de YOME, puedes escribir directamente aquí:\n\n"
            + yome_par_support_link_v2()
        )

    memory = get_memory(phone)
    order_draft = memory.get("order_draft") if isinstance(memory.get("order_draft"), dict) else {}
    order_amount = yome_extract_amount(msg)
    if order_draft or (order_amount and yome_order_intent(msg)):
        return yome_order_flow_reply(phone, msg, amount=order_amount)

    if yome_membership_intent(msg) and not yome_par_find_matches_v2(msg, limit=1):
        return yome_membership_reply()

    # 客户回复 1/2/3 选择产品
    if re.fullmatch(r"\d{1,2}", str(msg).strip()):
        options = state.get("context", {}).get(phone_key, {}).get("options", [])
        idx = int(str(msg).strip())
        if 1 <= idx <= len(options):
            p = options[idx - 1]
            state.setdefault("context", {})[phone_key] = {
                "selected": p,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            yome_par_save_state_v2(state)
            return yome_par_format_product_v2(p) + "\n¿Cuántas deseas?"

    context = state.get("context", {}).get(phone_key, {})
    selected = context.get("selected")
    qty_match = re.fullmatch(r"\d{1,3}", str(msg).strip())
    if isinstance(selected, dict) and qty_match:
        qty = int(str(msg).strip())
        unit, rule = yome_unit_price_for_order(selected, qty)
        quote = yome_par_format_product_v2(selected)
        if unit:
            quote += f"\n\nPara {qty} unidad(es), usando {rule}: RD${money(unit)} c/u."
            quote += f"\nTotal: RD${money(unit * qty)}"
        return quote + "\n\n" + yome_order_flow_reply(phone, msg, product=selected, qty=qty)

    if isinstance(selected, dict) and yome_par_norm_v2(msg) in [yome_par_norm_v2(x) for x in v22_load_words().get("yes", [])]:
        return yome_order_flow_reply(phone, msg, product=selected)

    matches = yome_par_find_matches_v2(msg, limit=5)

    if matches:
        if len(matches) == 1:
            p = matches[0]
            state.setdefault("context", {})[phone_key] = {
                "selected": p,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            yome_par_save_state_v2(state)
            return yome_par_format_product_v2(p) + "\n¿Cuántas deseas?"

        state.setdefault("context", {})[phone_key] = {
            "options": matches,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        yome_par_save_state_v2(state)

        parts = ["Sí 😊 Tenemos varias opciones. Te envío algunas con precio y foto:\n"]
        for i, p in enumerate(matches, 1):
            parts.append(yome_par_format_product_v2(p, i))
        parts.append("\nResponde con el número de la opción que deseas.")
        return "\n\n".join(parts)

    # 没匹配到产品，给目录和人工链接
    n = yome_par_norm_v2(msg)
    if any(k in n for k in ["hola", "buenas", "producto", "precio", "tiene", "tienes", "catalogo", "catálogo"]):
        return (
            "Hola 😊 Gracias por escribir a YOME.\n\n"
            "Puedes ver nuestros productos con fotos y precios aquí:\n"
            + yome_par_catalog_link_v2()
            + "\n\nSi necesitas ayuda de un asesor:\n"
            + yome_par_support_link_v2()
        )

    return ""

@app.before_request
def yome_product_auto_reply_read_only_v2_before():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        if yome_par_is_outgoing_v2(data):
            return ("OK", 200)

        msg, phone = yome_par_extract_v2(data)

        if not phone or not msg:
            return None

        # 防止系统自己的报价回流造成重复
        if yome_par_bot_quote_v2(msg):
            print("[YOME PAR V2] bot quote ignored")
            return ("OK", 200)

        # 管理员发产品，不影响旧版本保存产品
        if yome_par_is_admin_v2(phone):
            return None

        # 保存客户聊天记录，不删除历史
        yome_par_append_chat_v2(phone, msg)

        state = yome_par_load_state_v2()
        if not state.get("enabled", True):
            return None

        reply = yome_par_make_reply_v2(phone, msg)
        if not reply:
            return None

        if yome_par_recent_duplicate_v2(phone, msg, reply, seconds=90):
            print("[YOME PAR V2] duplicate reply skipped:", phone)
            return ("OK", 200)

        sent = yome_par_send_v2(phone, reply)
        state = yome_par_load_state_v2()
        state.setdefault("logs", []).append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phone": yome_par_phone_v2(phone),
            "msg": str(msg)[:160],
            "reply": str(reply)[:160],
            "sent": bool(sent)
        })
        yome_par_save_state_v2(state)

        return ("OK", 200)

    except Exception as e:
        print("[YOME PAR V2] before error:", e)

    return None

try:
    funcs = app.before_request_funcs.get(None, [])
    if yome_product_auto_reply_read_only_v2_before in funcs:
        funcs.remove(yome_product_auto_reply_read_only_v2_before)
    app.before_request_funcs[None] = [yome_product_auto_reply_read_only_v2_before] + funcs
    print("[YOME PAR V2] 产品自动回复已开启，优先级第一")
except Exception as e:
    print("[YOME PAR V2] install error:", e)

@app.route("/product-auto-reply-check")
def yome_product_auto_reply_check_v2():
    state = yome_par_load_state_v2()
    products = yome_par_read_products_v2()
    q = request.args.get("q", "silla de escritorio con rueda")
    matches = yome_par_find_matches_v2(q, limit=5)

    funcs = []
    for fname in ["send_wati_text", "wati_send_text", "send_text_message", "send_message", "wati_send_message", "send_wati_message"]:
        funcs.append({"name": fname, "exists": bool(globals().get(fname))})

    page = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>YOME Product Auto Reply</title></head>
<body style="font-family:Arial;padding:25px;">
<h1>YOME 产品自动回复</h1>
<p style="color:green;font-size:22px;font-weight:bold;">已开启 ✅</p>
<p>只读取产品目录，不删除、不覆盖、不修改产品。</p>
<p><b>产品文件:</b> {{file}}</p>
<p><b>产品数量:</b> {{count}}</p>
<p><a href="/product-auto-reply-off">关闭产品自动回复</a> · <a href="/product-auto-reply-on">打开产品自动回复</a></p>
<form>
<input name="q" value="{{q}}" style="font-size:18px;padding:8px;width:360px;">
<button style="font-size:18px;padding:8px;">测试搜索</button>
</form>
<h2>匹配结果</h2>
<pre>{{matches}}</pre>
<h2>发送函数</h2>
<pre>{{funcs}}</pre>
<h2>最近记录</h2>
<pre>{{logs}}</pre>
</body></html>
"""
    return render_template_string(
        page,
        file=str(yome_par_best_products_file_v2()),
        count=len(products),
        q=q,
        matches=json.dumps(matches, ensure_ascii=False, indent=2),
        funcs=json.dumps(funcs, ensure_ascii=False, indent=2),
        logs=json.dumps(state.get("logs", [])[-30:], ensure_ascii=False, indent=2)
    )

@app.route("/product-auto-reply-off")
def yome_product_auto_reply_off_v2():
    state = yome_par_load_state_v2()
    state["enabled"] = False
    yome_par_save_state_v2(state)
    return "Product auto reply OFF"

@app.route("/product-auto-reply-on")
def yome_product_auto_reply_on_v2():
    state = yome_par_load_state_v2()
    state["enabled"] = True
    yome_par_save_state_v2(state)
    return "Product auto reply ON"

print("[YOME PAR V2] check page: /product-auto-reply-check")
# === END YOME PRODUCT AUTO REPLY READ ONLY V2 ===



@app.before_request
def yome_customer_chat_records_logger_v1():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        if yome_ccr_is_outgoing_v1(data):
            return None

        msg, phone = yome_ccr_extract_v1(data)
        urls = yome_ccr_find_urls_v1(data)

        if not phone:
            return None

        # 管理员发产品不算客户聊天记录
        if yome_ccr_is_admin_v1(phone):
            return None

        if not msg and not urls:
            return None

        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phone": phone,
            "message": msg[:3000],
            "urls": urls,
            "source": "wati-webhook"
        }

        yome_ccr_append_record_v1(record)
        print("[YOME CCR V1] customer message logged:", phone, msg[:80])

    except Exception as e:
        print("[YOME CCR V1] logger error:", e)

    return None

def yome_ccr_read_jsonl_v1(path):
    rows = []
    try:
        path = Path(path)
        if not path.exists():
            return rows
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except Exception:
                    pass
    except Exception:
        pass
    return rows

def yome_ccr_read_json_file_v1(path):
    rows = []
    try:
        path = Path(path)
        if not path.exists():
            return rows
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("records") or data.get("logs") or data.get("messages") or [data]
        else:
            items = []

        for item in items:
            if isinstance(item, dict):
                msg, phone = yome_ccr_extract_v1(item)
                urls = yome_ccr_find_urls_v1(item)
                if msg or urls:
                    rows.append({
                        "time": item.get("time") or item.get("created_at") or item.get("timestamp") or "",
                        "phone": phone or item.get("phone", ""),
                        "message": msg[:3000],
                        "urls": urls,
                        "source": str(path)
                    })
    except Exception:
        pass
    return rows

def yome_ccr_read_csv_file_v1(path):
    rows = []
    try:
        path = Path(path)
        if not path.exists():
            return rows
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                phone = (
                    r.get("phone") or r.get("waId") or r.get("wa_id") or
                    r.get("from") or r.get("customer_phone") or ""
                )
                msg = (
                    r.get("message") or r.get("text") or r.get("body") or
                    r.get("content") or r.get("snippet") or ""
                )
                t = r.get("time") or r.get("created_at") or r.get("timestamp") or r.get("date") or ""
                if not msg:
                    msg = json.dumps(r, ensure_ascii=False)
                rows.append({
                    "time": t,
                    "phone": yome_ccr_phone_v1(phone),
                    "message": str(msg)[:3000],
                    "urls": [],
                    "source": str(path)
                })
    except Exception:
        pass
    return rows

def yome_ccr_all_records_v1():
    records = []

    # 新记录
    records.extend(yome_ccr_read_jsonl_v1(YOME_CCR_LOG_V1))

    # 旧版本可能存在的日志文件
    possible_json = [
        YOME_CCR_DATA_DIR_V1 / "yome_incoming_log_v1.json",
        Path("yome_incoming_log_v1.json"),
        YOME_CCR_DATA_DIR_V1 / "incoming_log.json",
        Path("incoming_log.json"),
    ]

    possible_csv = [
        YOME_CCR_DATA_DIR_V1 / "customer_messages_big.csv",
        Path("customer_messages_big.csv"),
        YOME_CCR_DATA_DIR_V1 / "customer_messages.csv",
        Path("customer_messages.csv"),
    ]

    for p in possible_json:
        records.extend(yome_ccr_read_json_file_v1(p))

    for p in possible_csv:
        records.extend(yome_ccr_read_csv_file_v1(p))

    # 去重
    seen = set()
    clean = []
    for r in records:
        phone = yome_ccr_phone_v1(r.get("phone", ""))
        msg = str(r.get("message", "") or "")
        t = str(r.get("time", "") or "")
        key = phone + "|" + t + "|" + msg[:120]
        if key in seen:
            continue
        seen.add(key)
        r["phone"] = phone
        clean.append(r)

    return clean

def yome_customer_chat_records_page_v1():
    q = str(request.args.get("q", "") or "").strip().lower()
    phone_q = yome_ccr_digits_v1(request.args.get("phone", "") or "")

    records = yome_ccr_all_records_v1()

    if q:
        records = [r for r in records if q in str(r.get("message", "")).lower() or q in str(r.get("phone", ""))]

    if phone_q:
        records = [r for r in records if yome_ccr_digits_v1(r.get("phone", "")).endswith(phone_q)]

    records = list(reversed(records))[:300]

    page = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YOME 客户聊天记录</title>
<style>
body{font-family:Arial,'Microsoft YaHei',sans-serif;background:#f5f7fb;margin:0;color:#111827;}
.header{background:#0f6bff;color:white;padding:22px 28px;}
.header h1{margin:0;font-size:32px;}
.wrap{padding:22px;}
.card{background:white;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 2px 10px rgba(0,0,0,.07);}
input{font-size:18px;padding:10px;border:1px solid #d1d5db;border-radius:10px;margin:4px;}
button{font-size:18px;background:#0f6bff;color:white;border:0;border-radius:10px;padding:11px 16px;font-weight:900;}
.msg{background:white;border-radius:16px;padding:14px;margin:12px 0;box-shadow:0 2px 8px rgba(0,0,0,.06);}
.phone{font-size:20px;font-weight:900;color:#0f6bff;}
.time{color:#6b7280;font-size:14px;}
.text{white-space:pre-wrap;font-size:17px;line-height:1.4;margin-top:8px;}
.small{color:#6b7280;font-size:13px;margin-top:8px;}
a{color:#0f6bff;font-weight:900;}
</style>
</head>
<body>
<div class="header">
<h1>YOME 客户聊天记录</h1>
<div>只查看记录，不删除聊天</div>
</div>

<div class="wrap">
<div class="card">
<p><b>记录数量:</b> {{count}}</p>
<p><b>保存文件:</b> {{log_file}}</p>
<p><a href="/admin-center">返回后台</a> · <a href="/customer-chat-records">刷新</a></p>
<form method="get" action="/customer-chat-records">
<input name="phone" placeholder="客户号码" value="{{phone_q}}">
<input name="q" placeholder="搜索内容" value="{{q}}">
<button type="submit">搜索</button>
</form>
</div>

{% for r in records %}
<div class="msg">
<div class="phone">{{r.phone}}</div>
<div class="time">{{r.time}}</div>
<div class="text">{{r.message}}</div>
{% if r.urls %}
<div class="small">链接/图片：<br>
{% for u in r.urls %}
<a href="{{u}}">{{u}}</a><br>
{% endfor %}
</div>
{% endif %}
<div class="small">来源：{{r.source}}</div>
</div>
{% endfor %}

{% if not records %}
<div class="card">
<h2>暂时没有记录</h2>
<p>如果这里是空的，可能是干净版之前没有记录消息。让一个普通客户发一条消息测试，之后会出现在这里。</p>
<p>WATI 自己的聊天记录还在 WATI Inbox 里。</p>
</div>
{% endif %}
</div>
</body>
</html>
"""
    return render_template_string(
        page,
        records=records,
        count=len(records),
        log_file=str(YOME_CCR_LOG_V1),
        q=q,
        phone_q=phone_q
    )

def yome_customer_chat_records_json_v1():
    return jsonify({
        "count": len(yome_ccr_all_records_v1()),
        "records": yome_ccr_all_records_v1()[-300:]
    })

try:
    if not any(str(rule.rule) == "/customer-chat-records" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/customer-chat-records", "yome_customer_chat_records_page_v1", yome_customer_chat_records_page_v1, methods=["GET"])

    if not any(str(rule.rule) == "/chat-records" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/chat-records", "yome_customer_chat_records_page_redirect_v1", yome_customer_chat_records_page_v1, methods=["GET"])

    if not any(str(rule.rule) == "/customer-chat-records-json" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/customer-chat-records-json", "yome_customer_chat_records_json_v1", yome_customer_chat_records_json_v1, methods=["GET"])

    print("[YOME CCR V1] 客户聊天记录页面已开启 /customer-chat-records")
except Exception as e:
    print("[YOME CCR V1] route error:", e)

# === END YOME CUSTOMER CHAT RECORDS VIEWER ONLY V1 ===



























# === YOME NORMAL CHAT INBOX VIEW V1 ===
# 像正常聊天一样查看客户消息：客户左边，YOME回复右边
# 只记录/显示聊天，不删除产品，不删除聊天记录，不影响管理员上传产品
import os, re, json, time, html
from pathlib import Path
from flask import request, render_template_string, redirect

try:
    YOME_CHAT_DATA_DIR_V1 = Path("/data")
    YOME_CHAT_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)
except Exception:
    YOME_CHAT_DATA_DIR_V1 = Path(".")
    YOME_CHAT_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)

YOME_CHAT_LOG_V1 = YOME_CHAT_DATA_DIR_V1 / "normal_chat_inbox_v1.jsonl"
YOME_OLD_CUSTOMER_LOG_V1 = YOME_CHAT_DATA_DIR_V1 / "customer_chat_records_v1.jsonl"

def yome_chat_digits_v1(s):
    return re.sub(r"[^0-9]", "", str(s or ""))

def yome_chat_phone_v1(phone):
    d = yome_chat_digits_v1(phone)
    if len(d) == 10 and d[:3] in ["809", "829", "849"]:
        return "1" + d
    return d

def yome_chat_admins_v1():
    raw = os.getenv("YOME_ADMIN_NUMBERS") or os.getenv("ADMIN_NUMBERS") or ""
    nums = []
    for x in re.split(r"[,;\s]+", raw):
        d = yome_chat_phone_v1(x)
        if d:
            nums.append(d)
    for d in ["18293244477", "18495037888"]:
        if d not in nums:
            nums.append(d)
    return nums

def yome_chat_is_admin_v1(phone):
    p = yome_chat_phone_v1(phone)
    return any(p == a or p.endswith(a) or a.endswith(p) for a in yome_chat_admins_v1())

def yome_chat_is_outgoing_event_v1(data):
    txt = str(data).lower()
    flags = [
        "'fromme': true", '"fromme": true',
        "'isfromme': true", '"isfromme": true',
        "'direction': 'outbound'", '"direction": "outbound"',
        "'status': 'sent'", '"status": "sent"',
        "'status': 'delivered'", '"status": "delivered"',
        "'status': 'read'", '"status": "read"',
        "'eventtype': 'message_sent'", '"eventtype": "message_sent"',
    ]
    return any(x in txt for x in flags)

def yome_chat_walk_strings_v1(obj):
    arr = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, str):
            if x.strip():
                arr.append(x.strip())
        elif isinstance(x, (int, float)):
            arr.append(str(x))
    try:
        walk(obj)
    except Exception:
        pass
    return arr

def yome_chat_extract_incoming_v1(data):
    msg = ""
    phone = ""

    if not isinstance(data, dict):
        return msg, phone

    for k in ["text", "body", "message", "messageText", "textMessage", "caption", "content"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            msg = msg or v

    for k in ["waId", "wa_id", "from", "sender", "whatsappNumber", "phone", "phoneNumber"]:
        v = data.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            phone = phone or str(v)

    try:
        msgs = data.get("messages", [])
        if msgs:
            one = msgs[0]
            msg = msg or one.get("body", "") or one.get("text", {}).get("body", "") or one.get("caption", "")
            phone = phone or one.get("from", "")
    except Exception:
        pass

    try:
        contacts = data.get("contacts", [])
        if contacts:
            phone = phone or contacts[0].get("wa_id", "")
    except Exception:
        pass

    if not msg:
        strings = [s for s in yome_chat_walk_strings_v1(data) if not s.startswith("http")]
        if strings:
            msg = sorted(strings, key=len, reverse=True)[0]

    return str(msg or "").strip(), yome_chat_phone_v1(phone)

def yome_chat_find_urls_v1(data):
    urls = []
    for s in yome_chat_walk_strings_v1(data):
        for u in re.findall(r"https?://[^\s\"'<>]+", s):
            u = u.strip().rstrip(".,;)")
            if u not in urls:
                urls.append(u)
    return urls[:8]

def yome_chat_append_v1(phone, direction, message, source="system", urls=None):
    try:
        phone = yome_chat_phone_v1(phone)
        if not phone or yome_chat_is_admin_v1(phone):
            return

        message = str(message or "").strip()
        urls = urls or []

        if not message and not urls:
            return

        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
            "phone": phone,
            "direction": direction,  # incoming / outgoing
            "message": message[:5000],
            "urls": urls[:8],
            "source": source
        }

        YOME_CHAT_LOG_V1.parent.mkdir(parents=True, exist_ok=True)
        with YOME_CHAT_LOG_V1.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception as e:
        print("[YOME CHAT INBOX V1] append error:", e)

@app.before_request
def yome_normal_chat_inbox_log_incoming_v1():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        # 不记录WATI自己的发送状态
        if yome_chat_is_outgoing_event_v1(data):
            return None

        msg, phone = yome_chat_extract_incoming_v1(data)
        urls = yome_chat_find_urls_v1(data)

        if not phone or yome_chat_is_admin_v1(phone):
            return None

        yome_chat_append_v1(phone, "incoming", msg, "wati_customer", urls)

    except Exception as e:
        print("[YOME CHAT INBOX V1] incoming log error:", e)

    return None

def yome_chat_wrap_send_v1(fname):
    try:
        fn = globals().get(fname)
        if not fn or getattr(fn, "_yome_chat_wrapped_v1", False):
            return

        def wrapped(*args, **kwargs):
            phone = ""
            msg = ""

            if len(args) >= 1:
                phone = args[0]
            if len(args) >= 2:
                msg = args[1]

            phone = kwargs.get("phone") or kwargs.get("to") or kwargs.get("wa_id") or kwargs.get("waId") or phone
            msg = kwargs.get("message") or kwargs.get("text") or kwargs.get("body") or kwargs.get("content") or msg

            result = fn(*args, **kwargs)

            try:
                yome_chat_append_v1(phone, "outgoing", msg, "yome_reply", [])
            except Exception as e:
                print("[YOME CHAT INBOX V1] outgoing log error:", e)

            return result

        wrapped._yome_chat_wrapped_v1 = True
        globals()[fname] = wrapped
        print("[YOME CHAT INBOX V1] wrapped send:", fname)

    except Exception as e:
        print("[YOME CHAT INBOX V1] wrap send error:", fname, e)

for _fname in ["send_wati_text", "wati_send_text", "send_text_message", "send_message", "wati_send_message", "send_wati_message"]:
    yome_chat_wrap_send_v1(_fname)

def yome_chat_read_jsonl_v1(path):
    rows = []
    try:
        path = Path(path)
        if not path.exists():
            return rows
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except Exception:
                    pass
    except Exception:
        pass
    return rows

def yome_chat_all_records_v1():
    records = []

    # 新聊天记录
    records.extend(yome_chat_read_jsonl_v1(YOME_CHAT_LOG_V1))

    # 兼容旧客户记录，只作为 incoming 显示
    old = yome_chat_read_jsonl_v1(YOME_OLD_CUSTOMER_LOG_V1)
    for r in old:
        phone = yome_chat_phone_v1(r.get("phone", ""))
        if not phone or yome_chat_is_admin_v1(phone):
            continue
        records.append({
            "time": r.get("time", ""),
            "ts": r.get("ts", 0),
            "phone": phone,
            "direction": "incoming",
            "message": r.get("message", ""),
            "urls": r.get("urls", []),
            "source": r.get("source", "old_customer_log")
        })

    # 去重
    seen = set()
    clean = []
    for r in records:
        phone = yome_chat_phone_v1(r.get("phone", ""))
        msg = str(r.get("message", "") or "")
        direction = r.get("direction", "")
        t = str(r.get("time", "") or "")
        key = phone + "|" + direction + "|" + t + "|" + msg[:160]
        if key in seen:
            continue
        seen.add(key)

        if not phone or yome_chat_is_admin_v1(phone):
            continue

        r["phone"] = phone
        r["direction"] = direction or "incoming"
        clean.append(r)

    def sort_key(x):
        try:
            return float(x.get("ts") or 0)
        except Exception:
            return 0

    clean.sort(key=sort_key)
    return clean

def yome_chat_threads_v1():
    records = yome_chat_all_records_v1()
    threads = {}
    for r in records:
        phone = yome_chat_phone_v1(r.get("phone", ""))
        if not phone:
            continue
        threads.setdefault(phone, []).append(r)

    result = []
    for phone, msgs in threads.items():
        last = msgs[-1] if msgs else {}
        unread = sum(1 for m in msgs if m.get("direction") == "incoming")
        result.append({
            "phone": phone,
            "last_time": last.get("time", ""),
            "last_message": str(last.get("message", ""))[:90],
            "count": len(msgs),
            "incoming_count": unread
        })

    result.sort(key=lambda x: x.get("last_time", ""), reverse=True)
    return result, threads

def yome_chat_linkify_v1(text):
    text = html.escape(str(text or ""))
    text = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank">\1</a>', text)
    return text

def yome_chat_inbox_page_v1():
    q = str(request.args.get("q", "") or "").strip()
    phone = yome_chat_phone_v1(request.args.get("phone", "") or "")

    thread_list, threads = yome_chat_threads_v1()

    if q:
        ql = q.lower()
        filtered = []
        for t in thread_list:
            msgs = threads.get(t["phone"], [])
            hay = t["phone"] + " " + " ".join(str(m.get("message","")) for m in msgs[-20:])
            if ql in hay.lower():
                filtered.append(t)
        thread_list = filtered

    if not phone and thread_list:
        phone = thread_list[0]["phone"]

    messages = threads.get(phone, []) if phone else []

    page = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YOME Chat Inbox</title>
<style>
body{font-family:Arial,'Microsoft YaHei',sans-serif;background:#e5ddd5;margin:0;color:#111827;}
.app{display:flex;height:100vh;}
.sidebar{width:340px;background:#ffffff;border-right:1px solid #d1d5db;overflow:auto;}
.header{background:#0f6bff;color:white;padding:16px;font-size:24px;font-weight:900;}
.search{padding:12px;background:#f3f4f6;}
.search input{width:92%;font-size:16px;padding:10px;border:1px solid #d1d5db;border-radius:12px;}
.thread{display:block;padding:14px 16px;border-bottom:1px solid #f1f5f9;text-decoration:none;color:#111827;}
.thread:hover{background:#eef4ff;}
.thread.active{background:#dbeafe;}
.phone{font-size:18px;font-weight:900;color:#0f6bff;}
.last{font-size:14px;color:#6b7280;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.time{font-size:12px;color:#9ca3af;margin-top:4px;}
.main{flex:1;display:flex;flex-direction:column;}
.top{background:#f9fafb;padding:14px 18px;border-bottom:1px solid #d1d5db;}
.chat{flex:1;overflow:auto;padding:18px;background:#efeae2;}
.bubble-row{display:flex;margin:8px 0;}
.bubble-row.incoming{justify-content:flex-start;}
.bubble-row.outgoing{justify-content:flex-end;}
.bubble{max-width:68%;padding:11px 13px;border-radius:16px;box-shadow:0 1px 2px rgba(0,0,0,.15);white-space:pre-wrap;line-height:1.35;font-size:16px;}
.incoming .bubble{background:white;border-top-left-radius:4px;}
.outgoing .bubble{background:#d9fdd3;border-top-right-radius:4px;}
.msgtime{font-size:11px;color:#6b7280;margin-top:5px;text-align:right;}
.empty{padding:40px;text-align:center;color:#6b7280;font-size:22px;}
a{color:#0f6bff;font-weight:700;}
.actions{font-size:13px;margin-top:5px;}
.actions a{margin-right:12px;}
@media(max-width:760px){
 .app{display:block;height:auto;}
 .sidebar{width:100%;height:40vh;}
 .main{height:60vh;}
 .bubble{max-width:85%;}
}
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="header">YOME Chat</div>
    <div class="search">
      <form method="get" action="/chat-inbox">
        <input name="q" value="{{q}}" placeholder="Buscar cliente / mensaje">
      </form>
      <div class="actions">
        <a href="/admin-center">Admin</a>
        <a href="/chat-inbox">刷新</a>
        <a href="/customer-chat-records">旧记录</a>
      </div>
    </div>

    {% for t in thread_list %}
    <a class="thread {% if t.phone == phone %}active{% endif %}" href="/chat-inbox?phone={{t.phone}}">
      <div class="phone">{{t.phone}}</div>
      <div class="last">{{t.last_message}}</div>
      <div class="time">{{t.last_time}} · {{t.count}} mensajes</div>
    </a>
    {% endfor %}
  </div>

  <div class="main">
    {% if phone %}
      <div class="top">
        <div class="phone">Cliente: {{phone}}</div>
        <div class="actions">
          <a href="https://wa.me/{{phone}}" target="_blank">Abrir WhatsApp</a>
          <a href="/customer-chat-records?phone={{phone}}">Ver旧记录</a>
        </div>
      </div>
      <div class="chat" id="chatBox">
        {% for m in messages %}
        <div class="bubble-row {{m.direction}}">
          <div class="bubble">
            {{linkify(m.message)|safe}}
            {% if m.urls %}
              {% for u in m.urls %}
                <br><a href="{{u}}" target="_blank">{{u}}</a>
              {% endfor %}
            {% endif %}
            <div class="msgtime">{{m.time}} · {{'Cliente' if m.direction == 'incoming' else 'YOME'}}</div>
          </div>
        </div>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty">暂时没有客户聊天记录。客户发消息后会显示在这里。</div>
    {% endif %}
  </div>
</div>

<script>
var box = document.getElementById("chatBox");
if (box) { box.scrollTop = box.scrollHeight; }
</script>
</body>
</html>
"""
    return render_template_string(
        page,
        thread_list=thread_list,
        threads=threads,
        phone=phone,
        messages=messages,
        q=q,
        linkify=yome_chat_linkify_v1
    )

try:
    if not any(str(rule.rule) == "/chat-inbox" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/chat-inbox", "yome_chat_inbox_page_v1", yome_chat_inbox_page_v1, methods=["GET"])

    if not any(str(rule.rule) == "/chat" for rule in app.url_map.iter_rules()):
        app.add_url_rule("/chat", "yome_chat_redirect_v1", lambda: redirect("/chat-inbox"), methods=["GET"])

    # 把聊天记录器放在最前面：只记录，不拦截
    funcs = app.before_request_funcs.get(None, [])
    if yome_normal_chat_inbox_log_incoming_v1 in funcs:
        funcs.remove(yome_normal_chat_inbox_log_incoming_v1)
    app.before_request_funcs[None] = [yome_normal_chat_inbox_log_incoming_v1] + funcs

    print("[YOME CHAT INBOX V1] 正常聊天后台已开启 /chat-inbox")
except Exception as e:
    print("[YOME CHAT INBOX V1] route/install error:", e)

# === END YOME NORMAL CHAT INBOX VIEW V1 ===







# === YOME HUMAN OPTION AND CLOUD SYNC V1 ===
# 客户选择人工客服 + 云端资料只读下载备份
# 不修改产品表格，不删除产品，不删除客户聊天记录
import os, re, json, time, zipfile, unicodedata
from pathlib import Path
from flask import request, send_file, jsonify, render_template_string

try:
    YOME_HCS_DATA_DIR_V1 = Path("/data")
    YOME_HCS_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)
except Exception:
    YOME_HCS_DATA_DIR_V1 = Path(".")
    YOME_HCS_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)

YOME_HCS_STATE_V1 = YOME_HCS_DATA_DIR_V1 / "human_option_cloud_sync_v1.json"
YOME_HCS_BACKUP_KEY_V1 = os.getenv("YOME_CLOUD_SYNC_KEY", "YOME829SYNC")
YOME_HCS_PUBLIC_URL_V1 = os.getenv("YOME_PUBLIC_URL", "https://repository-name-yome-ai-new-production.up.railway.app").rstrip("/")
YOME_HCS_SUPPORT_PHONE_V1 = re.sub(r"[^0-9]", "", os.getenv("YOME_HUMAN_SUPPORT_PHONE", "18293244477"))

def yome_hcs_digits_v1(s):
    return re.sub(r"[^0-9]", "", str(s or ""))

def yome_hcs_phone_v1(phone):
    d = yome_hcs_digits_v1(phone)
    if len(d) == 10 and d[:3] in ["809", "829", "849"]:
        return "1" + d
    return d

def yome_hcs_norm_v1(s):
    s = str(s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9ñ\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def yome_hcs_admins_v1():
    raw = os.getenv("YOME_ADMIN_NUMBERS") or os.getenv("ADMIN_NUMBERS") or ""
    nums = []
    for x in re.split(r"[,;\s]+", raw):
        d = yome_hcs_phone_v1(x)
        if d:
            nums.append(d)
    for d in ["18293244477", "18495037888"]:
        if d not in nums:
            nums.append(d)
    return nums

def yome_hcs_is_admin_v1(phone):
    p = yome_hcs_phone_v1(phone)
    return any(p == a or p.endswith(a) or a.endswith(p) for a in yome_hcs_admins_v1())

def yome_hcs_is_outgoing_v1(data):
    txt = str(data).lower()
    flags = [
        "'fromme': true", '"fromme": true',
        "'isfromme': true", '"isfromme": true',
        "'direction': 'outbound'", '"direction": "outbound"',
        "'status': 'sent'", '"status": "sent"',
        "'status': 'delivered'", '"status": "delivered"',
        "'status': 'read'", '"status": "read"',
        "'eventtype': 'message_sent'", '"eventtype": "message_sent"',
    ]
    return any(x in txt for x in flags)

def yome_hcs_walk_strings_v1(obj):
    arr = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, str):
            if x.strip():
                arr.append(x.strip())
        elif isinstance(x, (int, float)):
            arr.append(str(x))
    try:
        walk(obj)
    except Exception:
        pass
    return arr

def yome_hcs_extract_v1(data):
    msg = ""
    phone = ""

    if not isinstance(data, dict):
        return msg, phone

    for k in ["text", "body", "message", "messageText", "textMessage", "caption", "content"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            msg = msg or v

    for k in ["waId", "wa_id", "from", "sender", "whatsappNumber", "phone", "phoneNumber"]:
        v = data.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            phone = phone or str(v)

    try:
        msgs = data.get("messages", [])
        if msgs:
            one = msgs[0]
            msg = msg or one.get("body", "") or one.get("text", {}).get("body", "") or one.get("caption", "")
            phone = phone or one.get("from", "")
    except Exception:
        pass

    try:
        contacts = data.get("contacts", [])
        if contacts:
            phone = phone or contacts[0].get("wa_id", "")
    except Exception:
        pass

    if not msg:
        strings = [s for s in yome_hcs_walk_strings_v1(data) if not s.startswith("http")]
        if strings:
            msg = sorted(strings, key=len, reverse=True)[0]

    return str(msg or "").strip(), yome_hcs_phone_v1(phone)

def yome_hcs_load_v1():
    try:
        if YOME_HCS_STATE_V1.exists():
            data = json.loads(YOME_HCS_STATE_V1.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("last_human_link", {})
                data.setdefault("logs", [])
                return data
    except Exception:
        pass
    return {"last_human_link": {}, "logs": []}

def yome_hcs_save_v1(data):
    try:
        now = time.time()
        data["last_human_link"] = {
            k: v for k, v in data.get("last_human_link", {}).items()
            if now - float(v.get("time", 0)) < 86400
        }
        data["logs"] = data.get("logs", [])[-150:]
        YOME_HCS_STATE_V1.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[YOME HCS V1] save error:", e)

def yome_hcs_support_link_v1():
    phone = yome_hcs_phone_v1(YOME_HCS_SUPPORT_PHONE_V1) or "18293244477"
    return f"https://wa.me/{phone}?text=Hola%2C%20quiero%20hablar%20con%20un%20asesor%20de%20YOME"

def yome_hcs_wants_human_v1(msg):
    raw = str(msg or "").strip()
    n = yome_hcs_norm_v1(raw)

    if raw == "0":
        return True

    keys = [
        "asesor", "humano", "agente", "representante",
        "persona", "servicio al cliente", "soporte",
        "quiero hablar con alguien", "quiero hablar con una persona",
        "no quiero hablar con ai", "no quiero hablar con ia",
        "人工", "客服", "真人", "老板", "经理"
    ]
    return any(k in n or k in raw for k in keys)

def yome_hcs_send_v1(phone, msg):
    phone = yome_hcs_phone_v1(phone)
    if not phone:
        return False

    for fname in ["send_wati_text", "wati_send_text", "send_text_message", "send_message", "wati_send_message", "send_wati_message"]:
        try:
            fn = globals().get(fname)
            if fn:
                fn(phone, msg)
                print("[YOME HCS V1] sent by", fname, "to", phone)
                return True
        except Exception as e:
            print("[YOME HCS V1] send failed:", fname, e)

    print("[YOME HCS V1] no send function found")
    return False

@app.before_request
def yome_human_option_before_v1():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        if yome_hcs_is_outgoing_v1(data):
            return None

        msg, phone = yome_hcs_extract_v1(data)

        if not msg or not phone:
            return None

        if yome_hcs_is_admin_v1(phone):
            return None

        if not yome_hcs_wants_human_v1(msg):
            return None

        state = yome_hcs_load_v1()
        phone_key = yome_hcs_phone_v1(phone)
        last = state.get("last_human_link", {}).get(phone_key)
        if last and time.time() - float(last.get("time", 0)) < 180:
            return ("OK", 200)

        reply = (
            "Claro 😊 Puedes hablar directamente con un asesor de YOME aquí:\n\n"
            + yome_hcs_support_link_v1()
            + "\n\nTambién puedes responder 0 cuando quieras atención humana."
        )

        sent = yome_hcs_send_v1(phone, reply)

        state.setdefault("last_human_link", {})[phone_key] = {
            "time": time.time(),
            "msg": msg[:200],
            "sent": bool(sent)
        }
        state.setdefault("logs", []).append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phone": phone_key,
            "msg": msg[:200],
            "sent": bool(sent)
        })
        yome_hcs_save_v1(state)

        return ("OK", 200)

    except Exception as e:
        print("[YOME HCS V1] human option error:", e)

    return None

try:
    funcs = app.before_request_funcs.get(None, [])
    if yome_human_option_before_v1 in funcs:
        funcs.remove(yome_human_option_before_v1)
    app.before_request_funcs[None] = [yome_human_option_before_v1] + funcs
    print("[YOME HCS V1] 客户选择人工客服已开启")
except Exception as e:
    print("[YOME HCS V1] install error:", e)

def yome_hcs_count_file_v1(path):
    try:
        p = Path(path)
        if p.exists():
            return p.stat().st_size
    except Exception:
        pass
    return 0

def yome_hcs_best_products_file_v1():
    data = Path("/data/products.csv")
    local = Path("products.csv")
    if data.exists() and data.stat().st_size >= local.stat().st_size if local.exists() else data.exists():
        return data
    return local

def yome_hcs_backup_files_v1():
    files = []

    candidates = [
        (yome_hcs_best_products_file_v1(), "products.csv"),
        (Path("/data/normal_chat_inbox_v1.jsonl"), "normal_chat_inbox_v1.jsonl"),
        (Path("/data/customer_chat_records_v1.jsonl"), "customer_chat_records_v1.jsonl"),
        (Path("/data/payment_success_admin_notify_v1.json"), "payment_success_admin_notify_v1.json"),
        (Path("/data/product_auto_reply_v2_state.json"), "product_auto_reply_v2_state.json"),
        (Path("/data/unanswered_to_support_v1.json"), "unanswered_to_support_v1.json"),
    ]

    for src, name in candidates:
        try:
            if Path(src).exists():
                files.append((Path(src), name))
        except Exception:
            pass

    # 最近的产品备份也带上，不删除，只读
    try:
        backup_dir = Path("/data/backups")
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("*products*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]
            for b in backups:
                files.append((b, "backups/" + b.name))
    except Exception:
        pass

    return files

@app.route("/human-service-check")
def yome_human_service_check_v1():
    state = yome_hcs_load_v1()
    page = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>YOME Human Service</title></head>
<body style="font-family:Arial;padding:25px;">
<h1>YOME 人工客服选择</h1>
<p style="color:green;font-size:22px;font-weight:bold;">已开启 ✅</p>
<p>客户发送 0 / asesor / humano / 人工客服，会收到客服链接。</p>
<p><b>客服链接:</b> <a href="{{link}}">{{link}}</a></p>
<h2>最近记录</h2>
<pre>{{state}}</pre>
</body></html>
"""
    return render_template_string(page, link=yome_hcs_support_link_v1(), state=json.dumps(state, ensure_ascii=False, indent=2))

@app.route("/cloud-sync-info")
def yome_cloud_sync_info_v1():
    key = request.args.get("key", "")
    if key != YOME_HCS_BACKUP_KEY_V1:
        return "Forbidden", 403

    files = []
    for src, name in yome_hcs_backup_files_v1():
        files.append({
            "name": name,
            "source": str(src),
            "size": yome_hcs_count_file_v1(src)
        })

    return jsonify({
        "ok": True,
        "message": "只读云端资料备份，不修改、不删除",
        "files": files,
        "download_zip": YOME_HCS_PUBLIC_URL_V1 + "/cloud-sync-backup.zip?key=" + YOME_HCS_BACKUP_KEY_V1
    })

@app.route("/cloud-sync-backup.zip")
def yome_cloud_sync_backup_zip_v1():
    key = request.args.get("key", "")
    if key != YOME_HCS_BACKUP_KEY_V1:
        return "Forbidden", 403

    tmp = Path("/tmp/yome_cloud_sync_backup.zip")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            manifest = {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "YOME cloud backup, read only export",
                "files": []
            }

            for src, name in yome_hcs_backup_files_v1():
                if src.exists():
                    z.write(str(src), arcname=name)
                    manifest["files"].append({"name": name, "source": str(src), "size": src.stat().st_size})

            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return send_file(str(tmp), mimetype="application/zip", as_attachment=True, download_name="yome_cloud_sync_backup.zip")

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

print("[YOME HCS V1] human service: /human-service-check")
print("[YOME HCS V1] cloud sync info: /cloud-sync-info?key=YOME829SYNC")
# === END YOME HUMAN OPTION AND CLOUD SYNC V1 ===







# === YOME BUSINESS HOURS ORDER ADMIN NOTIFY V1 ===
# 客服时间 + 晚上提示 + 客户下单/付款成功通知管理员
# 不修改产品表格，不删除产品，不删除客户聊天记录
import os, re, json, time, hashlib, unicodedata
from pathlib import Path
from flask import request, jsonify, render_template_string

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    YOME_OSN_DATA_DIR_V1 = Path("/data")
    YOME_OSN_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)
except Exception:
    YOME_OSN_DATA_DIR_V1 = Path(".")
    YOME_OSN_DATA_DIR_V1.mkdir(parents=True, exist_ok=True)

YOME_OSN_STATE_V1 = YOME_OSN_DATA_DIR_V1 / "business_hours_order_notify_v1.json"
YOME_OSN_NOTIFY_KEY_V1 = os.getenv("YOME_ORDER_NOTIFY_KEY", "YOME829ORDER")
YOME_OSN_PUBLIC_URL_V1 = os.getenv(
    "YOME_PUBLIC_URL",
    "https://repository-name-yome-ai-new-production.up.railway.app"
).rstrip("/")

YOME_OSN_OPEN_HOUR_V1 = int(os.getenv("YOME_SERVICE_OPEN_HOUR", "9"))
YOME_OSN_CLOSE_HOUR_V1 = int(os.getenv("YOME_SERVICE_CLOSE_HOUR", "21"))

def yome_osn_digits_v1(s):
    return re.sub(r"[^0-9]", "", str(s or ""))

def yome_osn_phone_v1(phone):
    d = yome_osn_digits_v1(phone)
    if len(d) == 10 and d[:3] in ["809", "829", "849"]:
        return "1" + d
    return d

def yome_osn_norm_v1(s):
    s = str(s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9ñ\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def yome_osn_now_v1():
    try:
        if ZoneInfo:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    except Exception:
        pass
    return time.strftime("%Y-%m-%d %H:%M:%S")

def yome_osn_local_hour_v1():
    try:
        if ZoneInfo:
            from datetime import datetime
            return datetime.now(ZoneInfo("America/Santo_Domingo")).hour
    except Exception:
        pass
    # fallback：Railway UTC，Dominican Republic UTC-4
    return int(time.strftime("%H", time.gmtime(time.time() - 4 * 3600)))

def yome_osn_is_service_open_v1():
    h = yome_osn_local_hour_v1()
    return YOME_OSN_OPEN_HOUR_V1 <= h < YOME_OSN_CLOSE_HOUR_V1

def yome_osn_admins_v1():
    raw = (
        os.getenv("YOME_ORDER_NOTIFY_ADMINS")
        or os.getenv("YOME_PAYMENT_NOTIFY_ADMINS")
        or os.getenv("YOME_ADMIN_NUMBERS")
        or os.getenv("ADMIN_NUMBERS")
        or ""
    )
    nums = []
    for x in re.split(r"[,;\s]+", raw):
        d = yome_osn_phone_v1(x)
        if d:
            nums.append(d)

    for d in ["18293244477", "18495037888"]:
        if d not in nums:
            nums.append(d)

    return nums

def yome_osn_is_admin_v1(phone):
    p = yome_osn_phone_v1(phone)
    return any(p == a or p.endswith(a) or a.endswith(p) for a in yome_osn_admins_v1())

def yome_osn_is_outgoing_v1(data):
    txt = str(data).lower()
    flags = [
        "'fromme': true", '"fromme": true',
        "'isfromme': true", '"isfromme": true',
        "'direction': 'outbound'", '"direction": "outbound"',
        "'status': 'sent'", '"status": "sent"',
        "'status': 'delivered'", '"status": "delivered"',
        "'status': 'read'", '"status": "read"',
        "'eventtype': 'message_sent'", '"eventtype": "message_sent"',
    ]
    return any(x in txt for x in flags)

def yome_osn_walk_v1(obj):
    arr = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                arr.append((str(k), v))
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, str):
            if x.strip():
                arr.append(("", x.strip()))
        elif isinstance(x, (int, float)):
            arr.append(("", str(x)))
    try:
        walk(obj)
    except Exception:
        pass
    return arr

def yome_osn_find_value_v1(data, names):
    names = [yome_osn_norm_v1(x) for x in names]
    for k, v in yome_osn_walk_v1(data):
        nk = yome_osn_norm_v1(k)
        if any(n == nk or n in nk for n in names):
            if isinstance(v, (str, int, float)):
                return str(v)
    return ""

def yome_osn_extract_v1(data):
    msg = ""
    phone = ""

    if not isinstance(data, dict):
        return msg, phone

    for k in ["text", "body", "message", "messageText", "textMessage", "caption", "content"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            msg = msg or v

    for k in ["waId", "wa_id", "from", "sender", "whatsappNumber", "phone", "phoneNumber", "customerPhone"]:
        v = data.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            phone = phone or str(v)

    try:
        msgs = data.get("messages", [])
        if msgs:
            one = msgs[0]
            msg = msg or one.get("body", "") or one.get("text", {}).get("body", "") or one.get("caption", "")
            phone = phone or one.get("from", "")
    except Exception:
        pass

    try:
        contacts = data.get("contacts", [])
        if contacts:
            phone = phone or contacts[0].get("wa_id", "")
    except Exception:
        pass

    if not phone:
        phone = yome_osn_find_value_v1(data, ["phone", "customer_phone", "customerPhone", "waId", "from", "sender"])

    if not msg:
        strings = []
        for k, v in yome_osn_walk_v1(data):
            if isinstance(v, str) and v.strip() and not v.startswith("http"):
                strings.append(v.strip())
        if strings:
            msg = sorted(strings, key=len, reverse=True)[0]

    return str(msg or "").strip(), yome_osn_phone_v1(phone)

def yome_osn_find_urls_v1(data):
    urls = []
    for k, v in yome_osn_walk_v1(data):
        if isinstance(v, str):
            for u in re.findall(r"https?://[^\s\"'<>]+", v):
                u = u.strip().rstrip(".,;)")
                if u not in urls:
                    urls.append(u)
    return urls[:8]

def yome_osn_line_value_v1(msg, keys):
    lines = str(msg or "").splitlines()
    keys_norm = [yome_osn_norm_v1(k) for k in keys]

    for line in lines:
        n = yome_osn_norm_v1(line)
        for k in keys_norm:
            if n.startswith(k) or k + " " in n or k + ":" in n:
                val = re.sub(r"^[^:：]+[:：]\s*", "", line).strip()
                if val and val != line:
                    return val
                # 没有冒号时，去掉关键词
                raw = line
                for kk in keys:
                    raw = re.sub(kk, "", raw, flags=re.I).strip(" :-：")
                if raw:
                    return raw
    return ""

def yome_osn_amount_v1(data, msg=""):
    val = yome_osn_find_value_v1(data, [
        "amount", "total", "monto", "payment_amount", "paid_amount",
        "order_total", "total_paid", "precio", "valor"
    ])
    if val:
        return val

    val = yome_osn_line_value_v1(msg, ["total", "monto", "amount", "pago", "pagado", "valor", "金额"])
    if val:
        return val

    text = str(msg or "")
    m = re.search(r"(?:rd\$|\$)?\s*(\d[\d,.]{2,})", text, re.I)
    if m:
        return m.group(1)

    return ""

def yome_osn_order_v1(data, msg=""):
    val = yome_osn_find_value_v1(data, [
        "order", "order_id", "orderId", "order_number", "pedido",
        "invoice", "factura", "reference", "referencia", "transaction_id"
    ])
    if val:
        return val

    val = yome_osn_line_value_v1(msg, ["orden", "pedido", "order", "factura", "referencia", "ref", "订单"])
    if val:
        return val

    return ""

def yome_osn_extract_order_fields_v1(data, msg, phone):
    name = (
        yome_osn_find_value_v1(data, ["name", "nombre", "customer_name", "cliente", "full_name"])
        or yome_osn_line_value_v1(msg, ["nombre", "name", "cliente", "名字", "姓名"])
    )

    address = (
        yome_osn_find_value_v1(data, ["address", "direccion", "dirección", "delivery_address", "zona", "ubicacion", "ubicación"])
        or yome_osn_line_value_v1(msg, ["direccion", "dirección", "zona", "ubicacion", "ubicación", "address", "地址"])
    )

    product = (
        yome_osn_find_value_v1(data, ["product", "producto", "products", "items", "item", "articulo", "artículo"])
        or yome_osn_line_value_v1(msg, ["producto", "productos", "articulo", "artículo", "item", "pedido", "产品"])
    )

    quantity = (
        yome_osn_find_value_v1(data, ["quantity", "cantidad", "qty", "units", "unidades"])
        or yome_osn_line_value_v1(msg, ["cantidad", "cant", "qty", "unidades", "unidad", "数量"])
    )

    amount = yome_osn_amount_v1(data, msg)
    order = yome_osn_order_v1(data, msg)

    payment = (
        yome_osn_find_value_v1(data, ["payment_method", "metodo_pago", "método_pago", "bank", "banco"])
        or yome_osn_line_value_v1(msg, ["metodo de pago", "método de pago", "banco", "forma de pago", "付款"])
    )

    return {
        "customer_phone": yome_osn_phone_v1(phone),
        "name": name,
        "address": address,
        "product": product,
        "quantity": quantity,
        "amount": amount,
        "order": order,
        "payment": payment,
        "message": str(msg or "").strip()
    }

def yome_osn_is_order_success_v1(data, msg):
    raw = str(data) + "\n" + str(msg or "")
    n = yome_osn_norm_v1(raw)

    # 不要把问价格当成下单
    if "que precio" in n or "cuanto cuesta" in n or "precio de" in n:
        return False

    payment_words = [
        "payment received", "payment successful", "payment success", "paid",
        "pagado", "ya pague", "ya pagué", "pago recibido", "pago exitoso",
        "pago realizado", "pago confirmado", "transferencia realizada",
        "transferencia recibida", "deposito realizado", "deposito recibido",
        "comprobante de pago", "recibo de pago", "checkout session completed",
        "order paid", "orden pagada", "pedido pagado",
        "下单成功", "付款成功", "支付成功", "已经付款", "客人下单"
    ]

    if any(w in n for w in payment_words):
        return True

    # 如果客户一次性发了姓名、地址、产品、数量、金额，认为是下单资料
    field_keys = [
        ["nombre", "name", "cliente", "名字"],
        ["direccion", "dirección", "zona", "ubicacion", "地址"],
        ["producto", "productos", "articulo", "item", "产品"],
        ["cantidad", "cant", "qty", "unidades", "数量"],
        ["total", "monto", "amount", "金额"]
    ]

    score = 0
    for group in field_keys:
        if any(k in n for k in group):
            score += 1

    if score >= 3:
        return True

    order_words = ["pedido", "orden", "quiero ordenar", "quiero comprar", "delivery", "envio", "envío"]
    if any(w in n for w in order_words) and score >= 2:
        return True

    return False

def yome_osn_load_v1():
    try:
        if YOME_OSN_STATE_V1.exists():
            data = json.loads(YOME_OSN_STATE_V1.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("order_sent", {})
                data.setdefault("after_hours", {})
                data.setdefault("logs", [])
                return data
    except Exception:
        pass
    return {"order_sent": {}, "after_hours": {}, "logs": []}

def yome_osn_save_v1(data):
    try:
        now = time.time()
        data["order_sent"] = {
            k: v for k, v in data.get("order_sent", {}).items()
            if now - float(v.get("time", 0)) < 86400
        }
        data["after_hours"] = {
            k: v for k, v in data.get("after_hours", {}).items()
            if now - float(v.get("time", 0)) < 86400
        }
        data["logs"] = data.get("logs", [])[-200:]
        YOME_OSN_STATE_V1.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[YOME ORDER SERVICE V1] save error:", e)

def yome_osn_dedupe_key_v1(fields):
    raw = "|".join([
        fields.get("customer_phone", ""),
        fields.get("name", ""),
        fields.get("address", ""),
        fields.get("product", ""),
        fields.get("quantity", ""),
        fields.get("amount", ""),
        fields.get("message", "")[:300],
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]

def yome_osn_send_v1(phone, msg):
    phone = yome_osn_phone_v1(phone)
    if not phone:
        return False

    for fname in ["send_wati_text", "wati_send_text", "send_text_message", "send_message", "wati_send_message", "send_wati_message"]:
        try:
            fn = globals().get(fname)
            if fn:
                fn(phone, msg)
                print("[YOME ORDER SERVICE V1] sent by", fname, "to", phone)
                return True
        except Exception as e:
            print("[YOME ORDER SERVICE V1] send failed:", fname, e)

    print("[YOME ORDER SERVICE V1] no send function found")
    return False

def yome_osn_admin_message_v1(fields, urls=None, source="wati"):
    urls = urls or []
    customer = fields.get("customer_phone", "")

    lines = [
        "✅ YOME：客户下单/付款成功，请查看",
        "",
        f"Cliente WhatsApp: {customer or 'No detectado'}",
        f"Nombre: {fields.get('name') or 'No detectado'}",
        f"Dirección/Zona: {fields.get('address') or 'No detectado'}",
        f"Producto: {fields.get('product') or 'No detectado'}",
        f"Cantidad: {fields.get('quantity') or 'No detectado'}",
        f"Monto: {fields.get('amount') or 'No detectado'}",
    ]

    if fields.get("order"):
        lines.append(f"Orden/Referencia: {fields.get('order')}")

    if fields.get("payment"):
        lines.append(f"Método/Pago: {fields.get('payment')}")

    if fields.get("message"):
        lines.append("")
        lines.append("Mensaje original:")
        lines.append(fields.get("message")[:900])

    if urls:
        lines.append("")
        lines.append("Comprobante/links:")
        lines.extend(urls[:5])

    if customer:
        lines.append("")
        lines.append("Ver chat:")
        lines.append(f"{YOME_OSN_PUBLIC_URL_V1}/chat-inbox?phone={customer}")

    lines.append("")
    lines.append("Admin:")
    lines.append(f"{YOME_OSN_PUBLIC_URL_V1}/admin-center")

    return "\n".join(lines)

def yome_osn_notify_admins_v1(fields, urls=None, source="wati"):
    state = yome_osn_load_v1()
    key = yome_osn_dedupe_key_v1(fields)
    item = state.get("order_sent", {}).get(key)

    if item and time.time() - float(item.get("time", 0)) < 1800:
        print("[YOME ORDER SERVICE V1] duplicate order notify skipped")
        return False, "duplicate"

    text = yome_osn_admin_message_v1(fields, urls or [], source)
    ok_count = 0

    for admin in yome_osn_admins_v1():
        if yome_osn_send_v1(admin, text):
            ok_count += 1

    info = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "sent_admins": ok_count,
        "fields": fields
    }

    state.setdefault("order_sent", {})[key] = {"time": time.time(), "info": info}
    state.setdefault("logs", []).append({"type": "order_notify", "info": info})
    yome_osn_save_v1(state)

    return ok_count > 0, info

def yome_osn_human_intent_v1(msg):
    raw = str(msg or "").strip()
    n = yome_osn_norm_v1(raw)

    if raw == "0":
        return True

    keys = [
        "asesor", "humano", "agente", "representante", "persona",
        "servicio al cliente", "soporte", "no quiero hablar con ai",
        "no quiero hablar con ia", "人工", "客服", "真人"
    ]
    return any(k in n or k in raw for k in keys)

def yome_osn_hours_question_v1(msg):
    n = yome_osn_norm_v1(msg)
    keys = ["horario", "hora", "abierto", "abren", "cierran", "atienden", "客服时间", "营业时间"]
    return any(k in n or k in str(msg) for k in keys)

def yome_osn_support_link_v1():
    phone = yome_osn_phone_v1(os.getenv("YOME_HUMAN_SUPPORT_PHONE", "18293244477")) or "18293244477"
    return f"https://wa.me/{phone}?text=Hola%2C%20quiero%20hablar%20con%20un%20asesor%20de%20YOME"

def yome_osn_afterhours_message_v1():
    return (
        "Hola 😊 Nuestro horario de atención personalizada es de 9:00 AM a 9:00 PM.\n\n"
        "En este momento puedes dejar tu consulta, pedido, dirección o comprobante de pago por aquí. "
        "Yo te ayudo a dejarlo registrado, y cuando el equipo entre en horario de atención podrán confirmar y preparar tu envío más rápido.\n\n"
        "Si prefieres hablar con un asesor, puedes escribir aquí:\n"
        + yome_osn_support_link_v1()
    )

def yome_osn_hours_message_v1():
    if yome_osn_is_service_open_v1():
        status = "Ahora estamos dentro del horario de atención 😊"
    else:
        status = "Ahora estamos fuera del horario de atención, pero puedes dejar tu pedido o consulta por aquí 😊"

    return (
        f"{status}\n\n"
        "Horario de atención personalizada YOME:\n"
        "9:00 AM a 9:00 PM.\n\n"
        "Fuera de horario puedes dejar tu consulta, pedido o comprobante de pago. "
        "Al iniciar el horario de atención, el equipo podrá ayudarte más rápido."
    )

def yome_osn_customer_reply_cooldown_v1(phone, kind, seconds=14400):
    state = yome_osn_load_v1()
    phone = yome_osn_phone_v1(phone)
    key = phone + "|" + kind
    item = state.get("after_hours", {}).get(key)
    if item and time.time() - float(item.get("time", 0)) < seconds:
        return True

    state.setdefault("after_hours", {})[key] = {"time": time.time(), "kind": kind}
    yome_osn_save_v1(state)
    return False

def yome_order_admin_notify_before_v1():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        if yome_osn_is_outgoing_v1(data):
            return None

        msg, phone = yome_osn_extract_v1(data)

        if phone and yome_osn_is_admin_v1(phone):
            return None

        if not yome_osn_is_order_success_v1(data, msg):
            return None

        fields = yome_osn_extract_order_fields_v1(data, msg, phone)
        urls = yome_osn_find_urls_v1(data)

        yome_osn_notify_admins_v1(fields, urls, source="wati-webhook")

        # 不拦截后面的自动回复/聊天记录
        return None

    except Exception as e:
        print("[YOME ORDER SERVICE V1] order notify error:", e)

    return None

def yome_business_hours_afterhours_fallback_v1():
    try:
        if request.path != "/wati-webhook" or request.method != "POST":
            return None

        data = request.get_json(silent=True) or {}

        if yome_osn_is_outgoing_v1(data):
            return None

        msg, phone = yome_osn_extract_v1(data)

        if not msg or not phone:
            return None

        if yome_osn_is_admin_v1(phone):
            return None

        if yome_osn_human_intent_v1(msg):
            if yome_osn_customer_reply_cooldown_v1(phone, "human", seconds=180):
                return ("OK", 200)

            reply = (
                "Claro 😊 Puedes hablar directamente con un asesor de YOME aquí:\n\n"
                + yome_osn_support_link_v1()
                + "\n\nTambién puedes responder 0 cuando quieras atención humana."
            )
            yome_osn_send_v1(phone, reply)
            return ("OK", 200)

        if yome_osn_hours_question_v1(msg):
            if yome_osn_customer_reply_cooldown_v1(phone, "hours", seconds=600):
                return ("OK", 200)
            yome_osn_send_v1(phone, yome_osn_hours_message_v1())
            return ("OK", 200)

        # 晚上才兜底发服务时间提示；放最后，前面的产品自动回复能回答就不会到这里
        if not yome_osn_is_service_open_v1():
            if yome_osn_customer_reply_cooldown_v1(phone, "afterhours", seconds=14400):
                return None
            yome_osn_send_v1(phone, yome_osn_afterhours_message_v1())
            return ("OK", 200)

    except Exception as e:
        print("[YOME ORDER SERVICE V1] afterhours fallback error:", e)

    return None

@app.route("/order-success-notify", methods=["GET", "POST"])
def yome_order_success_notify_v1():
    key = request.args.get("key") or request.form.get("key") or ""
    if key != YOME_OSN_NOTIFY_KEY_V1:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}

    fields = {
        "customer_phone": yome_osn_phone_v1(
            request.args.get("phone")
            or request.form.get("phone")
            or data.get("phone")
            or data.get("customer_phone")
            or ""
        ),
        "name": request.args.get("name") or request.form.get("name") or data.get("name") or data.get("nombre") or "",
        "address": request.args.get("address") or request.form.get("address") or data.get("address") or data.get("direccion") or data.get("dirección") or "",
        "product": request.args.get("product") or request.form.get("product") or data.get("product") or data.get("producto") or "",
        "quantity": request.args.get("quantity") or request.form.get("quantity") or data.get("quantity") or data.get("cantidad") or "",
        "amount": request.args.get("amount") or request.form.get("amount") or data.get("amount") or data.get("total") or data.get("monto") or "",
        "order": request.args.get("order") or request.form.get("order") or data.get("order") or data.get("pedido") or "",
        "payment": request.args.get("payment") or request.form.get("payment") or data.get("payment") or data.get("pago") or "",
        "message": request.args.get("msg") or request.form.get("msg") or data.get("message") or "Pedido/pago confirmado desde sistema"
    }

    ok, info = yome_osn_notify_admins_v1(fields, [], source="order-success-notify")
    return jsonify({"ok": bool(ok), "info": info})

@app.route("/order-service-check")
def yome_order_service_check_v1():
    state = yome_osn_load_v1()
    test_url = (
        YOME_OSN_PUBLIC_URL_V1
        + "/order-success-notify?key="
        + YOME_OSN_NOTIFY_KEY_V1
        + "&phone=18290000000&name=Cliente%20Prueba&address=San%20Isidro&product=Silla&quantity=2&amount=7000&order=TEST001"
    )

    page = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>YOME Order Service</title></head>
<body style="font-family:Arial;padding:25px;">
<h1>YOME 客服时间 + 下单通知管理员</h1>
<p style="color:green;font-size:22px;font-weight:bold;">已开启 ✅</p>
<p>不修改产品表格，不删除产品，不删除客户聊天记录。</p>

<h2>客服时间</h2>
<pre>9:00 AM - 9:00 PM
当前小时: {{hour}}
当前状态: {{status}}</pre>

<h2>管理员号码</h2>
<pre>{{admins}}</pre>

<h2>测试通知管理员</h2>
<p><a href="{{test_url}}">{{test_url}}</a></p>

<h2>网站/付款系统调用格式</h2>
<pre>{{callback}}</pre>

<h2>最近记录</h2>
<pre>{{state}}</pre>
</body></html>
"""

    callback = (
        YOME_OSN_PUBLIC_URL_V1
        + "/order-success-notify?key="
        + YOME_OSN_NOTIFY_KEY_V1
        + "&phone=客户号码&name=客户名字&address=地址&product=产品&quantity=数量&amount=金额&order=订单号"
    )

    return render_template_string(
        page,
        hour=yome_osn_local_hour_v1(),
        status="OPEN" if yome_osn_is_service_open_v1() else "CLOSED",
        admins=json.dumps(yome_osn_admins_v1(), ensure_ascii=False, indent=2),
        test_url=test_url,
        callback=callback,
        state=json.dumps(state, ensure_ascii=False, indent=2)
    )

try:
    funcs = app.before_request_funcs.get(None, [])
    for f in [yome_order_admin_notify_before_v1, yome_business_hours_afterhours_fallback_v1]:
        if f in funcs:
            funcs.remove(f)

    # 订单通知放最前：只通知管理员，不拦截
    # 晚上提示/人工客服放最后：产品自动回复答不了时才处理
    app.before_request_funcs[None] = [yome_order_admin_notify_before_v1] + funcs + [yome_business_hours_afterhours_fallback_v1]
    print("[YOME ORDER SERVICE V1] 客服时间 + 下单通知管理员已开启")
except Exception as e:
    print("[YOME ORDER SERVICE V1] install error:", e)

print("[YOME ORDER SERVICE V1] check page: /order-service-check")
# === END YOME BUSINESS HOURS ORDER ADMIN NOTIFY V1 ===



if __name__ == "__main__":
    print("[YOME V2] Starting clean system on port 5000")
    print("[YOME V2] Panel: http://127.0.0.1:5000/manage")
    app.run(host="0.0.0.0", port=5000, debug=True)
