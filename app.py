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
        elif k in ["mayor", "por mayor", "wholesale", "批发价"]:
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
            elif low.startswith(("mayor ", "por mayor ")):
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
        lines.append(f"Precio: RD${money(product.get('price_retail'))} c/u.")
    if product.get("price_wholesale"):
        lines.append(f"Por mayor desde 3 unidades: RD${money(product.get('price_wholesale'))} c/u.")
    if product.get("price_dozen"):
        lines.append(f"Por docena desde 12 unidades: RD${money(product.get('price_dozen'))} c/u.")
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
            lines.append(f"Precio: RD${money(p.get('price_retail'))}")
        if p.get("price_wholesale"):
            lines.append(f"Por mayor: RD${money(p.get('price_wholesale'))}")
        if p.get("price_dozen"):
            lines.append(f"Docena: RD${money(p.get('price_dozen'))}")
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
    wholesale = parse_money(product.get("price_wholesale"))
    dozen = parse_money(product.get("price_dozen"))

    unit = retail
    rule = "precio detalle"
    if qty >= 12 and dozen:
        unit = dozen
        rule = "precio por docena"
    elif qty >= 3 and wholesale:
        unit = wholesale
        rule = "precio por mayor"

    lines = [product_reply(product)]
    if unit:
        total = qty * unit
        lines.append("")
        lines.append(f"Para {qty} unidad(es), usando {rule}: RD${money(unit)} c/u.")
        lines.append(f"Total: RD${money(total)}")
        lines.append("¿Deseas que te lo separe?")
        lines.append("Para completar el pedido, envíame tu nombre, zona/dirección y método de pago 😊")
    return "\n".join(lines)


def no_product_reply() -> str:
    return (
        "Por ahora no tengo ese producto registrado 😊\n\n"
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
            lines.append(f"Mayor / 批发价: RD${money(saved.get('price_wholesale'))}")
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
    candidates = state.get("last_candidates")
    if isinstance(candidates, list) and candidates:
        option, qty = extract_choice_and_qty(text, len(candidates))
        if option:
            product = candidates[option - 1]
            set_memory(phone, last_product=product, selected_product=product, awaiting_quantity=not bool(qty), last_candidates=[])
            if qty:
                return qty_reply(product, qty)
            return f"Perfecto 😊 Elegiste la opción {option}: {product.get('product_name')}.\n" + product_reply(product)

    nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", norm(text))]
    last_product = state.get("last_product") or state.get("selected_product")
    if nums and isinstance(last_product, dict):
        return qty_reply(last_product, nums[0])

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
<table><tr><th>图片/Foto</th><th>产品/Nombre</th><th>编号/Código</th><th>分类/Categoría</th><th>零售/Detalle</th><th>批发/Mayor</th><th>一打/Docena</th><th>操作</th></tr>
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
{inp('price_wholesale','批发价 / Precio mayor')}
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
# 重点：口语、拼写错误、更多产品、批发价、照片请求
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
    mayor = product.get("price_wholesale", "")
    docena = product.get("price_dozen", "")
    price = product.get("price_retail", "")

    lines = [f"Para {name} 😊"]

    if price:
        lines.append(f"Precio detalle: RD${money(price)} c/u.")
    if mayor:
        lines.append(f"Por mayor desde 3 unidades: RD${money(mayor)} c/u.")
    if docena:
        lines.append(f"Por docena desde 12 unidades: RD${money(docena)} c/u.")

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
    candidates = state.get("last_candidates")

    # 8. 列表选择
    if isinstance(candidates, list) and candidates:
        option, qty = extract_choice_and_qty(text, len(candidates))
        if option:
            product = candidates[option - 1]
            set_memory(phone, last_product=product, selected_product=product, awaiting_quantity=not bool(qty), last_candidates=[])
            if qty:
                return qty_reply(product, qty)
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
        return qty_reply(last_product, nums[0])

    # 12. 是的/确认
    if low in [v22_norm(x) for x in v22_load_words().get("yes", [])] and isinstance(last_product, dict):
        return (
            f"Perfecto 😊 Te separo {last_product.get('product_name')}.\n"
            "¿Cuántas unidades deseas?\n"
            "También envíame tu nombre, zona/dirección y método de pago."
        )

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


if __name__ == "__main__":
    print("[YOME V2] Starting clean system on port 5000")
    print("[YOME V2] Panel: http://127.0.0.1:5000/manage")
    app.run(host="0.0.0.0", port=5000, debug=True)
