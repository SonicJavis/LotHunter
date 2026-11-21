"""Run LotHunter as a foreground-only, loopback WSGI application."""
import argparse
import csv
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import dashboard, registry

ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger(__name__)
MAX_BODY = 2 * 1024 * 1024
LISTING_ROUTE = re.compile(r"/listing/([1-9][0-9]{0,17})(/edit|/value)?")
HEADERS = [
    ("Content-Security-Policy", "default-src 'none'; style-src 'self'; form-action 'self'; "
     "frame-ancestors 'none'; base-uri 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
]
ASSETS = {
    "/style.css": (ROOT / "lothunter/style.css", "text/css; charset=utf-8"),
    "/examples.csv": (ROOT / "fixtures/lots.csv", "text/csv; charset=utf-8"),
}


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def parse_form(environ):
    content_type = environ.get("CONTENT_TYPE", "")
    if "\r" in content_type or "\n" in content_type or len(content_type) > 512:
        raise RequestError(400, "Invalid content type.")
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        raise RequestError(400, "Invalid request size.") from None
    if not 0 < length <= MAX_BODY:
        raise RequestError(413, "Request must contain between 1 byte and 2 MiB.")
    raw = environ["wsgi.input"].read(length)
    if len(raw) != length:
        raise RequestError(400, "Incomplete request body.")
    try:
        if content_type.split(";", 1)[0] == "application/x-www-form-urlencoded":
            pairs = parse_qs(raw.decode("utf-8"), keep_blank_values=True, errors="strict", max_num_fields=150)
            if any(len(values) != 1 for values in pairs.values()):
                raise ValueError
            return {key: values[0] for key, values in pairs.items()}
        if content_type.split(";", 1)[0] == "multipart/form-data":
            mime_header = f"Content-Type: {content_type}\r\n\r\n".encode("ascii")
            message = BytesParser(policy=policy.default).parsebytes(mime_header + raw)
            parts = {}
            if not message.is_multipart() or message.defects:
                raise ValueError
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if (part.is_multipart() or part.defects or name not in {"_csrf", "file"} or name in parts
                        or part.get_content_disposition() != "form-data"):
                    raise ValueError
                parts[name] = part.get_payload(decode=True).decode("utf-8")
            return parts
    except (UnicodeError, ValueError):
        raise RequestError(400, "Malformed form or CSV content. Save files as UTF-8.") from None
    raise RequestError(415, "Unsupported form content type.")


def response(body, status=200, content_type="text/html; charset=utf-8", extra=()):
    return status, body, [("Content-Type", content_type), *extra]


def redirect(location):
    return response("", 303, extra=[("Location", location)])


class LotHunter:
    def __init__(self, database, port):
        self.database = database
        self.host = f"127.0.0.1:{port}"
        self.token = secrets.token_urlsafe(32)
        self.examples = json.loads((ROOT / "fixtures/scenarios.json").read_text(encoding="utf-8"))

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"]
        try:
            if environ.get("HTTP_HOST") != self.host:
                raise RequestError(403, "Use the configured loopback URL.")
            data = {}
            if method == "POST":
                if environ.get("HTTP_ORIGIN", f"http://{self.host}") != f"http://{self.host}":
                    raise RequestError(403, "Use a form from the local dashboard.")
                data = parse_form(environ)
                supplied = data.pop("_csrf", "")
                if not secrets.compare_digest(supplied.encode("utf-8"), self.token.encode("ascii")):
                    raise RequestError(403, "Form token is invalid. Reload the dashboard.")
            elif method != "GET":
                raise RequestError(405, "Unsupported HTTP method.")
            status, body, headers = self.route(method, environ["PATH_INFO"], environ["QUERY_STRING"], data)
        except registry.ListingNotFound:
            status, body, headers = response(dashboard.page("Listing unavailable", ""), 404)
        except RequestError as error:
            status, body, headers = response(dashboard.page("Request unavailable", "", str(error)), error.status)
        except Exception:
            LOGGER.exception("request_failed method=%r", method)
            status, body, headers = response(dashboard.page(
                "Local dashboard error", "", "The request failed. Check the local terminal log."), 500)
        payload = body.encode("utf-8") if isinstance(body, str) else body
        start_response(f"{status} {HTTPStatus(status).phrase}",
                       [*headers, *HEADERS, ("Content-Length", str(len(payload)))])
        LOGGER.info("request_finished %s", json.dumps({"method": method, "status": status}))
        return [payload]

    def route(self, method, path, query, data):
        posting = method == "POST"
        if not posting and path in ASSETS:
            asset, content_type = ASSETS[path]
            return response(asset.read_bytes(), content_type=content_type)
        if not posting and path == "/template.csv":
            output = io.StringIO(newline="")
            csv.writer(output).writerow(registry.FIELDS)
            return response(output.getvalue(), content_type="text/csv; charset=utf-8",
                            extra=[("Content-Disposition", 'attachment; filename="listings-template.csv"')])
        if not posting and path == "/":
            return response(dashboard.overview(registry.get_listings(self.database)))
        if path == "/import":
            if not posting:
                return response(dashboard.import_form(self.token))
            try:
                result = registry.import_csv(self.database, data.get("file", ""))
            except ValueError as error:
                return response(dashboard.import_form(self.token, str(error)), 400)
            return response(dashboard.import_form(self.token, result=result))
        if path == "/new":
            values = {key: data[key] for key in registry.FIELDS if key in data}
            if not posting:
                return response(dashboard.listing_form(values, self.token))
            try:
                item, created = registry.create_listing(self.database, values)
            except ValueError as error:
                return response(dashboard.listing_form(values, self.token, error=str(error)), 400)
            return redirect(f"/listing/{item}" + ("" if created else "?duplicate=1"))
        match = LISTING_ROUTE.fullmatch(path)
        if not match:
            raise RequestError(404, "This page does not exist.")
        item = int(match[1])
        entry = registry.get_listing(self.database, item)
        record = entry["record"]
        source, original = registry.canonical_identity(record["source"], record["listing_id"])
        example = self.examples.get(original) if source == "illustrative" else None
        if match[2] == "/edit":
            if not posting:
                return response(dashboard.listing_form(record, self.token, item))
            values = {key: data[key] for key in registry.FIELDS if key in data}
            try:
                registry.edit_listing(self.database, item, values)
            except ValueError as error:
                return response(dashboard.listing_form({**values, "source": record["source"],
                                                        "listing_id": record["listing_id"]},
                                                       self.token, item, str(error)), 400)
            return redirect(f"/listing/{item}")
        if match[2] == "/value":
            illustrative = bool(example and query == "demo=1")
            values = data if posting else dashboard.calculator_defaults(record)
            if illustrative and not posting:
                values.update(example)
            results = None
            try:
                if posting or illustrative:
                    results = dashboard.calculate(values)
            except ValueError as error:
                return response(dashboard.calculator(entry, values, self.token, error=str(error),
                                                     illustrative=illustrative), 400)
            return response(dashboard.calculator(entry, values, self.token, results, illustrative=illustrative))
        if posting:
            raise RequestError(405, "Use the listing edit form.")
        return response(dashboard.listing_detail(entry, query == "duplicate=1", bool(example)))


class LocalServer(WSGIServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


class RequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # The application logs status and method without raw URLs or form values.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "data/lothunter.sqlite3")
    parser.add_argument("--load-examples", action="store_true", help="Import the illustrative listings at startup.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    os.umask(0o077)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    registry.initialize(args.db)
    if args.load_examples:
        registry.import_csv(args.db, (ROOT / "fixtures/lots.csv").read_text(encoding="utf-8"))
    with make_server("127.0.0.1", args.port, LotHunter(args.db, args.port),
                     server_class=LocalServer, handler_class=RequestHandler) as server:
        LOGGER.info("listening %s", json.dumps({"url": f"http://127.0.0.1:{args.port}"}))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            LOGGER.info("stopped")


if __name__ == "__main__":
    main()
