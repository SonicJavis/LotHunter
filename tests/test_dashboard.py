import io
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from lothunter import registry
from lothunter.__main__ import LotHunter
from lothunter.dashboard import calculate, calculator_defaults


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.sqlite"
        registry.initialize(self.path)
        self.app = LotHunter(self.path, 8765)
        self.wsgi = validator(self.app)

    def request(self, path, data=None, *, body=None, content_type=None, **overrides):
        route, _, query = path.partition("?")
        env = {}
        setup_testing_defaults(env)
        if data is not None:
            body = urlencode(data).encode()
            content_type = "application/x-www-form-urlencoded"
        env.update(PATH_INFO=route, QUERY_STRING=query, HTTP_HOST=self.app.host)
        if body is not None:
            env.update(REQUEST_METHOD="POST", CONTENT_TYPE=content_type,
                       CONTENT_LENGTH=str(len(body)), **{"wsgi.input": io.BytesIO(body)})
        env.update(overrides)
        meta = {}

        def start(status, headers, exc_info=None):
            meta.update(status=int(status.split()[0]), headers=dict(headers))

        output = self.wsgi(env, start)
        try:
            payload = b"".join(output).decode("utf-8")
        finally:
            output.close()
        return meta["status"], meta["headers"], payload

    def token(self, page):
        return re.search(r'name="_csrf" value="([^"]+)"', page)[1]

    def test_manual_routes_evidence_and_calculator(self):
        status, _, form = self.request("/new")
        self.assertEqual(status, 200)
        values = {"_csrf": self.token(form), "source": "Example", "listing_id": "test",
                  "title": "<script>example</script>", "category": "Laptop",
                  "quantity": "3", "confidence": "high"}
        status, headers, _ = self.request("/new", values)
        self.assertEqual(status, 303)
        listing = headers["Location"]
        status, _, detail = self.request(listing)
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;example&lt;/script&gt;", detail)
        self.assertIn("confidence <strong>low</strong>", detail)
        self.assertIn("Submitted observations", detail)
        self.assertEqual(self.request(listing + "/edit", {**values, "title": "Reviewed"})[0], 303)
        self.assertIn("Reviewed", self.request("/")[2])

        status, _, form = self.request(listing + "/value")
        self.assertEqual(status, 200)
        fields = calculator_defaults(registry.get_listings(self.path)[0]["record"])
        fields.update({"_csrf": self.token(form), "hammer": "5", "expected.whole.quantity": "2",
                       "expected.whole.price": "10", "expected.failed": "1"})
        status, _, result = self.request(listing + "/value", fields)
        self.assertEqual(status, 200)
        self.assertIn("£15.00", result)
        self.assertIn("No viable bid", result)
        fields["expected.failed"] = "2"
        status, _, result = self.request(listing + "/value", fields)
        self.assertEqual(status, 400)
        self.assertIn("Allocate exactly 3 units", result)

    def test_real_multipart_csv_upload_and_examples(self):
        token = self.token(self.request("/import")[2])
        examples = self.request("/examples.csv")[2]
        boundary = "test-boundary"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="_csrf"\r\n\r\n{token}\r\n',
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="lots.csv"\r\n'
            f"Content-Type: text/csv\r\n\r\n{examples}\r\n--{boundary}--\r\n",
        ]
        content_type = f"multipart/form-data; boundary={boundary}"
        for expected in ("Added 2 listing(s)", "matched 2 duplicate(s)"):
            status, _, page = self.request("/import", body="".join(parts).encode(), content_type=content_type)
            self.assertEqual(status, 200)
            self.assertIn(expected, page)
        parts[1] = parts[1].replace(examples, "source,listing_id,title\nAdded,3,Café\n")
        self.assertEqual(self.request("/import", body="".join(parts).encode(), content_type=content_type)[0], 200)
        self.assertIn("Café", self.request("/")[2])
        result = self.request("/listing/1/value?demo=1")[2]
        self.assertIn("Illustrative scenario assumptions", result)
        self.assertIn("£55.00", result)
        self.assertIn("£1,265.00", result)
        self.assertIn("£50.00", self.request("/listing/2/value?demo=1")[2])

    def test_loopback_origin_token_and_invalid_input(self):
        self.assertEqual(self.request("/", HTTP_HOST="unrelated.example")[0], 403)
        self.assertEqual(self.request("/new", {"_csrf": "wrong"})[0], 403)
        self.assertEqual(self.request("/new", {"_csrf": self.app.token}, HTTP_ORIGIN="https://example.com")[0], 403)
        self.assertEqual(self.request("/new", body=b"\xff",
                                      content_type="application/x-www-form-urlencoded")[0], 400)
        self.assertEqual(self.request("/listing/999")[0], 404)
        self.assertEqual(self.request("/")[1]["Cache-Control"], "no-store")

    def test_fixture_sensitivity(self):
        laptop = self.app.examples["case-a"]
        ssd = self.app.examples["case-b"]
        self.assertEqual(calculate(laptop)["expected"]["maximum_bid"], 55)
        self.assertIsNone(calculate({**laptop, "expected.refurbish.processing": "33"})["expected"]["maximum_bid"])
        self.assertEqual(calculate({**laptop, "expected.refurbish.quantity": "60",
                                    "expected.failed": "19"})["expected"]["maximum_bid"], 25)
        self.assertEqual(calculate({**ssd, "expected.whole.labour": "3",
                                    "expected.whole.returns_pct": "8"})["expected"]["maximum_bid"], 15)
