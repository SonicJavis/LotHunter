"""Validate and persist listing records and their submitted evidence."""
import csv
import io
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .valuation import Fees, number, percent, units

LOGGER = logging.getLogger(__name__)
REVIEW_STATES = ("unreviewed", "researching", "shortlisted", "rejected")
CONFIDENCE_STATES = ("unknown", "low", "medium", "high")
LABELS = {
    "source": "Source marketplace",
    "listing_id": "Original listing ID",
    "url": "Original listing URL",
    "title": "Title",
    "description": "Description",
    "seller": "Public seller identifier",
    "category": "Hardware category",
    "manufacturer": "Manufacturer",
    "model": "Model",
    "quantity": "Quantity",
    "cpu": "CPU",
    "ram": "RAM",
    "storage": "Storage / capacity",
    "condition": "Condition and defects",
    "price": "Current bid or asking price (£)",
    "premium_pct": "Buyer premium (%)",
    "fixed_fee": "Fixed buyer fee before VAT (£)",
    "hammer_vat_pct": "VAT on hammer (%)",
    "fee_vat_pct": "VAT on buyer fees (%)",
    "inbound_cost": "Shipping or collection before VAT (£)",
    "inbound_vat_pct": "VAT on shipping or collection (%)",
    "first_bid": "First valid bid (£)",
    "increment": "Bid increment (£)",
    "auction_end": "Auction end with timezone (ISO 8601)",
    "evidence": "Evidence and provenance",
    "confidence": "Reported evidence confidence",
    "review_status": "Human review status",
}
FIELDS = tuple(LABELS)
FEE_FIELDS = tuple(Fees.__dataclass_fields__)
REQUIRED_HEADERS = {"source", "listing_id", "title"}
SOURCE_ALIASES = {
    "ebay-uk": "ebay", "ebay-co-uk": "ebay", "www-ebay-co-uk": "ebay",
    "bid-spotter": "bidspotter", "www-bidspotter-co-uk": "bidspotter",
    "i-bidder": "i-bidder", "ibidder": "i-bidder",
    "john-pye-auctions": "john-pye", "simon-charles-auctions": "simon-charles",
}
LONG_FIELDS = {"description", "condition", "evidence", "url"}
MAX_CSV_ROWS = 1000


class ListingNotFound(LookupError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_identity(source, original_id):
    slug = re.sub(r"[^\w]+", "-", source.casefold()).strip("-")
    if not slug:
        raise ValueError("Source marketplace must contain a letter or number.")
    return SOURCE_ALIASES.get(slug, slug), original_id


def validate(values):
    record = {}
    for field in FIELDS:
        value = values.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"{LABELS[field]} must be text.")
        if len(value) > (12000 if field in LONG_FIELDS else 300) or "\x00" in value:
            raise ValueError(f"{LABELS[field]} contains invalid text or is too long.")
        record[field] = value.strip()
    for field in REQUIRED_HEADERS:
        if not record[field]:
            raise ValueError(f"{LABELS[field]} is required.")
    canonical_identity(record["source"], record["listing_id"])
    if record["url"]:
        try:
            url = urlsplit(record["url"])
            valid = (url.scheme in {"http", "https"} and url.hostname and not url.username
                     and not url.password and not any(c.isspace() for c in record["url"]))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Listing URL must be an absolute HTTP(S) URL without credentials.")
    if record["quantity"]:
        record["quantity"] = str(units(record["quantity"], "Quantity", minimum=1))
    for field in ("price", *FEE_FIELDS):
        if record[field]:
            if field.endswith("_pct"):
                percent(record[field], LABELS[field])
            else:
                value = number(record[field], LABELS[field])
                if field in ("first_bid", "increment") and not value:
                    raise ValueError(f"{LABELS[field]} must be positive.")
    if record["auction_end"]:
        try:
            end = datetime.fromisoformat(record["auction_end"])
            if end.utcoffset() is None:
                raise ValueError
            record["auction_end"] = end.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise ValueError("Auction end must include date, time and timezone.") from None
    for field, choices in (("confidence", CONFIDENCE_STATES), ("review_status", REVIEW_STATES)):
        record[field] = record[field] or choices[0]
        if record[field] not in choices:
            raise ValueError(f"{LABELS[field]} must be one of {', '.join(choices)}.")
    return record


def assess_confidence(record):
    required = ["category", "manufacturer", "model", "quantity", "condition", "evidence"]
    category = record["category"].casefold()
    if any(word in category for word in ("laptop", "desktop", "computer", "workstation", "server")):
        required.extend(("cpu", "ram", "storage"))
    elif any(word in category for word in ("ssd", "drive", "storage")):
        required.append("storage")
    missing = [LABELS[field] for field in required
               if record[field].casefold() in ("", "mixed", "varies", "unknown", "n/a")]
    confidence = record["confidence"]
    if missing and confidence in {"medium", "high"}:
        confidence = "low"
    return confidence, missing


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    try:
        headers = reader.fieldnames or []
        if (not REQUIRED_HEADERS.issubset(headers) or set(headers) - set(FIELDS)
                or len(headers) != len(set(headers))):
            raise ValueError("Invalid CSV header. Use the template and include source, listing_id and title.")
        rows = []
        for row in reader:
            if len(rows) == MAX_CSV_ROWS:
                raise ValueError(f"CSV imports are limited to {MAX_CSV_ROWS} data rows.")
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Row width does not match the header.")
                rows.append((validate(row), row))
            except ValueError as error:
                raise ValueError(f"CSV line {reader.line_num}: {error}") from None
        if not rows:
            raise ValueError("CSV contains no listings.")
        return rows
    except csv.Error as error:
        raise ValueError(f"Malformed CSV near line {reader.line_num}: {error}") from None


@contextmanager
def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            return
        if version != 0 or db.execute("SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise RuntimeError("Database is not an empty or supported LotHunter database.")
        db.execute("""
            CREATE TABLE listings (
                id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL,
                original_id TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                payload TEXT NOT NULL CHECK (json_valid(payload)),
                UNIQUE (source_key, original_id)
            )
        """)
        db.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                listing INTEGER NOT NULL REFERENCES listings(id),
                observed_at TEXT NOT NULL,
                method TEXT NOT NULL CHECK (method IN ('manual', 'csv')),
                payload TEXT NOT NULL CHECK (json_valid(payload))
            )
        """)
        db.execute("CREATE INDEX observations_by_listing ON observations(listing, id)")
        db.execute("PRAGMA user_version = 1")


def add(db, record, original, method, timestamp):
    source_key, listing_id = canonical_identity(record["source"], record["listing_id"])
    cursor = db.execute("""
        INSERT INTO listings(source_key, original_id, first_seen, last_seen, payload)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_key, original_id) DO NOTHING
    """, (source_key, listing_id, timestamp, timestamp, json.dumps(record)))
    created = cursor.rowcount == 1
    item = db.execute("SELECT id FROM listings WHERE source_key = ? AND original_id = ?",
                      (source_key, listing_id)).fetchone()[0]
    if not created:
        db.execute("UPDATE listings SET last_seen = max(last_seen, ?) WHERE id = ?", (timestamp, item))
    observe(db, item, original, method, timestamp)
    return item, created


def observe(db, item, original, method, timestamp):
    payload = json.dumps(original, sort_keys=True)
    previous = db.execute("""
        SELECT 1 FROM observations WHERE listing = ? AND method = ? AND payload = ? LIMIT 1
    """, (item, method, payload)).fetchone()
    if previous is None:
        db.execute("INSERT INTO observations(listing, observed_at, method, payload) VALUES (?, ?, ?, ?)",
                   (item, timestamp, method, payload))


def create_listing(path, values):
    record = validate(values)
    with connect(path) as db:
        result = add(db, record, values, "manual", utc_now())
    LOGGER.info("manual_save %s", json.dumps({"id": result[0], "created": result[1]}))
    return result


def edit_listing(path, item, values):
    record = validate(values)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT source_key, original_id FROM listings WHERE id = ?", (item,)).fetchone()
        if current is None:
            raise ListingNotFound("Listing does not exist.")
        if tuple(current) != canonical_identity(record["source"], record["listing_id"]):
            raise ValueError("Source and original listing ID identify the record and cannot be changed.")
        db.execute("UPDATE listings SET payload = ? WHERE id = ?", (json.dumps(record), item))
        observe(db, item, values, "manual", utc_now())
    LOGGER.info("listing_edited %s", json.dumps({"id": item}))


def import_csv(path, text):
    rows = parse_csv(text)
    created = 0
    timestamp = utc_now()
    with connect(path) as db:
        for record, original in rows:
            _, is_new = add(db, record, original, "csv", timestamp)
            created += is_new
    duplicates = len(rows) - created
    LOGGER.info("csv_imported %s", json.dumps({"created": created, "duplicates": duplicates}))
    return created, duplicates


def get_listings(path):
    with connect(path) as db:
        return [unpack(row) for row in db.execute("SELECT * FROM listings ORDER BY last_seen DESC, id DESC")]


def unpack(row):
    return {
        "id": row["id"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "record": json.loads(row["payload"]),
    }


def get_listing(path, item):
    with connect(path) as db:
        row = db.execute("SELECT * FROM listings WHERE id = ?", (item,)).fetchone()
        if row is None:
            raise ListingNotFound("Listing does not exist.")
        listing = unpack(row)
        listing["observations"] = [
            {"observed_at": entry["observed_at"], "method": entry["method"],
             "record": json.loads(entry["payload"])}
            for entry in db.execute("SELECT * FROM observations WHERE listing = ? ORDER BY id", (item,))
        ]
        return listing
