"""
Material Price & Vendor Finder — Cloud Edition
Full app with login + Gemini AI + Web Scraper, deployable on Render.

Local run:  python app.py
Cloud run:  gunicorn app:app   (Render does this automatically)
"""

from flask import Flask, request, jsonify, session, send_from_directory, redirect
from flask_cors import CORS
import requests
import re
import os
import json
import hashlib
import secrets
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")
# Secret key for sessions — set SECRET_KEY in Render env vars for production
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
CORS(app, supports_credentials=True)

SERPER_URL = "https://google.serper.dev/search"

# Serper key comes from Render environment variable (kept off the frontend = secure)
SERPER_KEY = os.environ.get("SERPER_KEY", "")


# ══════════════════════════════════════════
# USER ACCOUNTS
# Passwords are stored as salted SHA-256 hashes, never plain text.
# To add/change users: edit the USERS dict below and redeploy.
# Generate a hash with:  python -c "import hashlib;print(hashlib.sha256(('SALT'+'yourpassword').encode()).hexdigest())"
# ══════════════════════════════════════════
SALT = os.environ.get("LOGIN_SALT", "mpvf-printo-2026")

def hash_pw(password):
    return hashlib.sha256((SALT + password).encode()).hexdigest()

# username : password-hash
# Default password for all three below is shown in comments — CHANGE THESE.
USERS = {
    "mohan":   hash_pw("printo123"),    # password: printo123
    "team1":   hash_pw("welcome123"),   # password: welcome123
    "team2":   hash_pw("welcome123"),   # password: welcome123
}


def is_logged_in():
    return session.get("user") is not None


def require_login():
    """Return None if logged in, else a 401 JSON response."""
    if not is_logged_in():
        return jsonify({"error": "NOT_AUTHENTICATED"}), 401
    return None


# ══════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if username in USERS and USERS[username] == hash_pw(password):
        session.permanent = True
        session["user"] = username
        log.info(f"Login OK: {username}")
        return jsonify({"ok": True, "user": username})

    log.info(f"Login FAILED: {username}")
    return jsonify({"ok": False, "error": "Invalid username or password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def me():
    if is_logged_in():
        return jsonify({"loggedIn": True, "user": session["user"]})
    return jsonify({"loggedIn": False})


# ══════════════════════════════════════════
# SCRAPER CORE (Serper.dev Google Search)
# ══════════════════════════════════════════

def extract_phone(text):
    if not text:
        return ""
    # Normalise: strip spaces, dashes, dots, brackets so spaced/dotted numbers match
    raw = text
    compact = re.sub(r'[\s\-\.\(\)]', '', raw)

    # 1) Mobile: 10-digit starting 6-9, optional +91/0 prefix
    m = re.search(r'(?:\+?91|0)?([6-9]\d{9})', compact)
    if m:
        return "+91-" + m.group(1)

    # 2) Landline with STD code: +91 followed by 2-4 digit area code + 6-8 digit number
    #    e.g. 080-23570863, 011 4567 8900
    m = re.search(r'(?:\+?91)?(0\d{2,4}\d{6,8})', compact)
    if m:
        num = m.group(1)
        if 8 <= len(num) <= 12:
            return num

    # 3) Bare landline 8 digits (rare, last resort)
    m = re.search(r'(?<!\d)(\d{8})(?!\d)', compact)
    if m:
        return m.group(1)

    return ""

def extract_price(text):
    if not text:
        return "Contact for quote"
    patterns = [
        r'₹\s*[\d,]+(?:\.\d+)?(?:\s*(?:per|/)\s*[\w]+)?',
        r'Rs\.?\s*[\d,]+(?:\.\d+)?(?:\s*(?:per|/)\s*[\w]+)?',
        r'INR\s*[\d,]+(?:\.\d+)?',
        r'[\d,]+(?:\.\d+)?\s*(?:per\s+(?:ream|kg|sheet|roll|piece|box|pack|unit))',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return "Contact for quote"

def detect_city(text):
    city_map = {
        "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
        "mumbai": "Mumbai", "bombay": "Mumbai",
        "delhi": "Delhi", "new delhi": "Delhi",
        "chennai": "Chennai", "madras": "Chennai",
        "hyderabad": "Hyderabad",
        "pune": "Pune", "kolkata": "Kolkata",
        "ahmedabad": "Ahmedabad", "jaipur": "Jaipur",
        "surat": "Surat", "coimbatore": "Coimbatore",
    }
    t = text.lower()
    for k, v in city_map.items():
        if k in t:
            return v
    return ""

def source_label(url):
    if not url:
        return "Web"
    u = url.lower()
    if "indiamart" in u:   return "IndiaMART"
    if "tradeindia" in u:  return "TradeIndia"
    if "justdial" in u:    return "JustDial"
    if "sulekha" in u:     return "Sulekha"
    if "exportersindia" in u: return "ExportersIndia"
    if "alibaba" in u:     return "Alibaba"
    if "amazon" in u:      return "Amazon"
    if "flipkart" in u:    return "Flipkart"
    return "Web"

def clean(t):
    if not t:
        return ""
    return re.sub(r'\s+', ' ', str(t).strip())


# ═════════════════════════════════════════════
# DISTRIBUTOR / WHOLESALER FILTERING
# ═════════════════════════════════════════════

# Layer 1 — marketplace aggregators & retailers to BLOCK entirely.
# CEO requirement: no IndiaMART, JustDial, TradeIndia, Sulekha, etc.
BLOCKED_MARKETPLACES = [
    "indiamart.com", "dir.indiamart.com",
    "justdial.com", "jdmart.com",
    "tradeindia.com",
    "sulekha.com",
    "exportersindia.com",
    "alibaba.com", "aliexpress.com",
    "amazon.in", "amazon.com",
    "flipkart.com",
    "meesho.com", "snapdeal.com",
    "udaan.com",
    "indiabizclub.com", "go4worldbusiness.com",
    "tradeford.com", "exporthub.com",
    "made-in-china.com", "globalsources.com",
]

# Layer 2 — words that signal a genuine distributor / wholesaler.
# Split by strength so weak/generic words don't over-promote random firms.
STRONG_SIGNALS = [
    "distributor", "distributors", "distribution",
    "wholesale", "wholesaler", "wholesalers",
    "authorised dealer", "authorized dealer",
    "authorised distributor", "authorized distributor",
    "sole distributor", "exclusive distributor",
    "stockist", "stockists", "c&f agent",
]
MEDIUM_SIGNALS = [
    "bulk supplier", "bulk supply", "bulk order", "bulk quantity",
    "importer", "importers", "manufacturer", "manufacturers",
    "trading company", "trading co", "paper mart", "paper boards",
    "paper traders", "b2b", "minimum order", "moq", "per ton", "per ream",
]
WEAK_SIGNALS = [
    "traders", "enterprises", "agencies", "agency",
    "industries", "mills", "supplier", "suppliers",
    "corporation", "company", "trading",
]

# Words that signal a RETAILER / end-consumer shop (demote these).
RETAIL_SIGNALS = [
    "online store", "buy online", "add to cart", "shop now",
    "ecommerce", "e-commerce", "retail", "retailer", "checkout",
    "marketplace", "compare prices", "best price online",
    "free delivery", "cod available", "stationery shop",
    "buy now", "shopping cart", "wishlist",
]


def is_blocked_marketplace(url):
    """Layer 1 — True if the URL is a marketplace/retailer we must exclude."""
    if not url:
        return False
    u = url.lower()
    return any(domain in u for domain in BLOCKED_MARKETPLACES)


def distributor_score(text):
    """
    Layer 2 — score how likely a vendor is a real distributor/wholesaler.
    Uses word-boundary matching so partial words don't falsely match.
    Returns (score, likelihood_label).
    """
    if not text:
        return 0, "Low"
    t = text.lower()

    def has(word):
        # Word-boundary match: "mills" won't match "millses", "b2b" stays exact
        return re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', t) is not None

    score = 0
    strong_hits = 0
    for kw in STRONG_SIGNALS:
        if has(kw):
            score += 3
            strong_hits += 1
    for kw in MEDIUM_SIGNALS:
        if has(kw):
            score += 2
    for kw in WEAK_SIGNALS:
        if has(kw):
            score += 1

    # Penalise retail signals more firmly
    for kw in RETAIL_SIGNALS:
        if has(kw):
            score -= 3

    # Label thresholds:
    # - Any strong signal (distributor/wholesaler/stockist) = High outright
    # - Otherwise rely on accumulated score
    if strong_hits >= 1 or score >= 5:
        label = "High"
    elif score >= 2:
        label = "Medium"
    else:
        label = "Low"
    return score, label


# ─────────────────────────────────────────
# Core: Serper.dev Google Search
# ─────────────────────────────────────────
def serper_search(query, serper_key, num=10):
    """Call Serper.dev — returns real Google Search results as JSON."""
    headers = {
        "X-API-KEY": serper_key,
        "Content-Type": "application/json"
    }
    payload = {
        "q": query,
        "gl": "in",       # India
        "hl": "en",
        "num": num,
        "autocorrect": True
    }
    try:
        resp = requests.post(SERPER_URL, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if resp.status_code in (401, 403):
            raise ValueError("Invalid Serper API key. Get a free key at serper.dev")
        # 400, 429, 500 etc — query-level problem, not a key problem
        raise RuntimeError(f"Serper returned {resp.status_code} for this query")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network error: {e}")


PLACES_URL = "https://google.serper.dev/places"

def places_search(query, serper_key):
    """Call Serper.dev Places endpoint — returns Google Maps business listings
    with verified phone numbers and addresses. Best source of real contacts."""
    headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": "in", "hl": "en"}
    try:
        resp = requests.post(PLACES_URL, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        if resp.status_code in (401, 403):
            raise ValueError("Invalid Serper API key. Get a free key at serper.dev")
        raise RuntimeError(f"Places returned {resp.status_code}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network error: {e}")


def parse_places(data, city):
    """Parse Serper Places JSON into vendor cards with verified phone + address."""
    vendors = []
    for place in data.get("places", []):
        try:
            name    = clean(place.get("title", ""))
            addr    = clean(place.get("address", ""))
            phone   = clean(place.get("phoneNumber", ""))
            rating  = place.get("rating", "")
            reviews = place.get("ratingCount", "") or place.get("reviewsCount", "")
            link    = clean(place.get("website", ""))
            category = clean(place.get("category", ""))

            if not name:
                continue
            if is_blocked_marketplace(link):
                continue

            notes = ""
            if rating:
                notes += f"Rating: {rating}/5"
            if reviews:
                notes += f" ({reviews} reviews)"

            pscore, plabel = distributor_score(f"{name} {category}")

            vendors.append({
                "vendorName": name[:90],
                "price":      "Contact for quote",
                "address":    addr[:120] or (city if city != "All" else "India"),
                "contact":    phone or "Visit website",
                "website":    link[:200],
                "notes":      notes,
                "source":     "Google Maps",
                "distributor": plabel,
                "_dscore":     pscore
            })
        except Exception:
            continue
    return vendors


def parse_results(data, city):
    """Parse Serper JSON into vendor cards."""
    vendors = []
    seen = set()

    skip_domains = ["wikipedia", "youtube", "facebook", "instagram",
                    "twitter", "linkedin", "quora", "reddit", "pinterest",
                    "slideshare", "scribd"]

    # ── Organic results ──
    for r in data.get("organic", []):
        try:
            title   = clean(r.get("title", ""))
            link    = clean(r.get("link", ""))
            snippet = clean(r.get("snippet", ""))
            sitelinks = r.get("sitelinks", [])

            if not title or len(title) < 4:
                continue
            if any(s in link.lower() for s in skip_domains):
                continue
            # Layer 1 — drop marketplace aggregators / retailers entirely
            if is_blocked_marketplace(link):
                continue

            # Deduplicate by domain
            domain = re.sub(r'https?://(www\.)?', '', link).split('/')[0]
            if domain in seen:
                continue
            seen.add(domain)

            full_text = f"{title} {snippet}"
            price   = extract_price(full_text)
            phone   = extract_phone(full_text)
            addr    = detect_city(full_text) or (city if city != "All" else "India")
            src     = source_label(link)
            # Layer 2 — distributor likelihood
            dscore, dlabel = distributor_score(full_text)

            vendors.append({
                "vendorName": title[:90],
                "price":      price,
                "address":    addr,
                "contact":    phone if phone else "Visit website",
                "website":    link[:200],
                "notes":      snippet[:200],
                "source":     src,
                "distributor": dlabel,
                "_dscore":     dscore
            })
        except Exception:
            continue

    # ── Knowledge graph (if present — often has direct contact) ──
    kg = data.get("knowledgeGraph", {})
    if kg.get("title") and not is_blocked_marketplace(kg.get("website", "")):
        phone = extract_phone(str(kg))
        addr  = clean(kg.get("address", ""))
        kg_text = f"{kg.get('title','')} {kg.get('description','')} {kg.get('type','')}"
        kscore, klabel = distributor_score(kg_text)
        vendors.insert(0, {
            "vendorName": clean(kg.get("title", ""))[:90],
            "price":      "Contact for quote",
            "address":    addr or (city if city != "All" else "India"),
            "contact":    phone or clean(kg.get("phone", "")) or "Visit website",
            "website":    clean(kg.get("website", ""))[:200],
            "notes":      clean(kg.get("description", ""))[:200],
            "source":     "Google Knowledge",
            "distributor": klabel,
            "_dscore":     kscore
        })

    # ── Local results (Google Maps listings — best for phone + address) ──
    for place in data.get("places", []):
        try:
            name    = clean(place.get("title", ""))
            addr    = clean(place.get("address", ""))
            phone   = clean(place.get("phoneNumber", ""))
            rating  = place.get("rating", "")
            reviews = place.get("reviewsCount", "")
            link    = clean(place.get("website", ""))
            category = clean(place.get("category", ""))

            if not name:
                continue
            if is_blocked_marketplace(link):
                continue
            key = name.lower()[:20]
            if key in seen:
                continue
            seen.add(key)

            notes = ""
            if rating:
                notes += f"Rating: {rating}/5"
            if reviews:
                notes += f" ({reviews} reviews)"

            # Distributor scoring from name + category
            pscore, plabel = distributor_score(f"{name} {category}")

            vendors.insert(0, {   # Put local results first — highest quality
                "vendorName": name[:90],
                "price":      "Contact for quote",
                "address":    addr[:100],
                "contact":    phone or "Visit website",
                "website":    link[:200],
                "notes":      notes,
                "source":     "Google Maps",
                "distributor": plabel,
                "_dscore":     pscore
            })
        except Exception:
            continue

    return vendors


# ─────────────────────────────────────────
# Multi-query strategy for more vendors
# ─────────────────────────────────────────
def build_queries(material, city):
    """Build distributor-targeted search queries (Layer 3)."""
    city_str = city if city != "All" else "India"
    queries = [
        f"{material} distributor {city_str} contact",
        f"{material} wholesaler {city_str}",
        f"{material} authorised dealer {city_str}",
        f"{material} bulk supplier {city_str}",
        f"{material} manufacturer {city_str} contact number",
        f"{material} importer distributor {city_str}",
        f"{material} stockist {city_str}",
        f"{material} trading company {city_str}",
        f"{material} wholesale price {city_str}",
        f"{material} paper mart distributor {city_str}",
    ]
    return queries


# ─────────────────────────────────────────
# API Endpoints


# ══════════════════════════════════════════
# SCRAPE ENDPOINT (login required)
# ══════════════════════════════════════════
@app.route("/api/scrape", methods=["GET"])
def api_scrape():
    auth = require_login()
    if auth:
        return auth

    material  = request.args.get("q", "").strip()
    city      = request.args.get("city", "All").strip()
    limit     = min(int(request.args.get("limit", 15)), 100)
    dist_only = request.args.get("distributors_only", "0").strip() == "1"

    if not material:
        return jsonify({"error": "No query provided"}), 400
    if not SERPER_KEY:
        return jsonify({"error": "Server is missing SERPER_KEY. Admin must set it in Render env vars."}), 500

    log.info(f"\n{'='*50}")
    log.info(f"[{session['user']}] Query: '{material}' | City: '{city}' | Limit: {limit} | DistOnly: {dist_only}")

    all_vendors = []
    queries = build_queries(material, city)
    seen_names = set()

    # ── Maps/Places first: these carry verified phone numbers ──
    city_str = city if city != "All" else "India"
    place_queries = [
        f"{material} distributor {city_str}",
        f"{material} wholesaler {city_str}",
        f"{material} dealer {city_str}",
    ]
    for pq in place_queries:
        try:
            log.info(f"[Places] {pq}")
            pdata = places_search(pq, SERPER_KEY)
            pvendors = parse_places(pdata, city)
            log.info(f"  -> {len(pvendors)} map listings")
            for v in pvendors:
                key = v["vendorName"].lower()[:25]
                if key not in seen_names:
                    seen_names.add(key)
                    all_vendors.append(v)
        except ValueError as e:
            if "Invalid Serper API key" in str(e):
                return jsonify({"error": str(e)}), 400
            log.error(f"Places failed: {e}")
            continue
        except Exception as e:
            log.error(f"Places failed: {e}")
            continue

    for i, q in enumerate(queries):
        if len(all_vendors) >= limit * 2:
            break
        try:
            log.info(f"[Query {i+1}/{len(queries)}] {q}")
            data = serper_search(q, SERPER_KEY, num=10)
            vendors = parse_results(data, city)
            log.info(f"  -> {len(vendors)} vendors parsed")
            for v in vendors:
                key = v["vendorName"].lower()[:25]
                if key not in seen_names:
                    seen_names.add(key)
                    all_vendors.append(v)
        except ValueError as e:
            if "Invalid Serper API key" in str(e):
                if not all_vendors:
                    return jsonify({"error": str(e)}), 400
                break
            log.error(f"Query {i+1} failed: {e}")
            continue
        except Exception as e:
            log.error(f"Query {i+1} failed: {e}")
            continue

    # Distributors-only filter (CEO requirement)
    if dist_only:
        before = len(all_vendors)
        all_vendors = [v for v in all_vendors if v.get("distributor") in ("High", "Medium")]
        log.info(f"Distributor filter: {before} -> {len(all_vendors)} kept")

    def rank(v):
        s = 0
        s += v.get("_dscore", 0) * 2
        if v.get("source") == "Google Maps":      s += 6
        if v.get("source") == "Google Knowledge": s += 5
        if v.get("price") != "Contact for quote": s += 4
        ph = v.get("contact", "")
        if ph and ph != "Visit website" and any(c.isdigit() for c in ph): s += 3
        if v.get("website", "").startswith("http"):  s += 1
        if len(v.get("address", "")) > 4:            s += 1
        return s

    ranked = sorted(all_vendors, key=rank, reverse=True)[:limit]
    for v in ranked:
        v.pop("_dscore", None)

    log.info(f"Total unique: {len(all_vendors)} -> Returning top {len(ranked)}")
    return jsonify({"vendors": ranked, "total": len(ranked), "query": material, "city": city})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "version": "cloud-2.0-distributor",
        "serper_configured": bool(SERPER_KEY)
    })


# ══════════════════════════════════════════
# SERVE FRONTEND
# ══════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*56)
    print("  Material Price & Vendor Finder - Cloud Edition")
    print("="*56)
    print(f"  Local:  http://localhost:{port}")
    print(f"  Serper key configured: {bool(SERPER_KEY)}")
    print("="*56 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
