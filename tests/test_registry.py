from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lothunter import registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.sqlite"
        registry.initialize(self.path)
        self.record = {
            "source": "eBay UK", "listing_id": "100", "title": "Business laptops",
            "description": "Seller says tested", "evidence": "  Original seller wording  ",
            "confidence": "high", "category": "laptop",
        }

    def test_manual_edit_persistence_and_original_evidence(self):
        item, created = registry.create_listing(self.path, self.record)
        self.assertTrue(created)
        registry.edit_listing(self.path, item, {**self.record, "title": "Reviewed", "review_status": "shortlisted"})
        registry.initialize(self.path)
        listing = registry.get_listing(self.path, item)
        self.assertEqual(listing["record"]["title"], "Reviewed")
        self.assertEqual(listing["record"]["review_status"], "shortlisted")
        self.assertEqual(listing["observations"][0]["record"]["evidence"], self.record["evidence"])
        self.assertEqual(listing["first_seen"], listing["last_seen"])
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            registry.edit_listing(self.path, item, {**self.record, "listing_id": "200"})

    def test_duplicate_csv_preserves_curated_data_and_observations(self):
        item, _ = registry.create_listing(self.path, self.record)
        csv = "source,listing_id,title,evidence\nebay.co.uk,100,New title,New evidence\neBay,200,Other,Other evidence\n"
        self.assertEqual(registry.import_csv(self.path, csv), (1, 1))
        self.assertEqual(registry.import_csv(self.path, csv), (0, 2))
        listing = registry.get_listing(self.path, item)
        self.assertEqual(listing["record"]["title"], self.record["title"])
        self.assertEqual(len(listing["observations"]), 2)
        self.assertEqual(listing["observations"][1]["record"]["evidence"], "New evidence")
        self.assertEqual(len(registry.get_listings(self.path)), 2)

    def test_missing_specs_are_unknown_and_reduce_confidence(self):
        record = registry.validate(self.record)
        confidence, missing = registry.assess_confidence(record)
        self.assertEqual(record["cpu"], "")
        self.assertEqual(record["quantity"], "")
        self.assertEqual(confidence, "low")
        self.assertIn("RAM", missing)

    def test_corrupt_csv_never_writes_partial_results(self):
        header = "source,listing_id,title,quantity\n"
        for bad in ('A,2,"unclosed', "A,2,test,NaN", "A,2,test", "A,2,test,9,extra"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                registry.import_csv(self.path, header + "A,1,valid,3\n" + bad)
            self.assertEqual(registry.get_listings(self.path), [])
        with self.assertRaisesRegex(ValueError, "header"):
            registry.import_csv(self.path, "source,listing_id,title,title\nA,1,x,y\n")

    def test_validated_link_and_timezone(self):
        with self.assertRaises(ValueError):
            registry.validate({**self.record, "url": "javascript:alert(1)"})
        with self.assertRaises(ValueError):
            registry.validate({**self.record, "auction_end": "2026-01-01"})
        record = registry.validate({**self.record, "auction_end": "2026-06-01T14:00+01:00"})
        self.assertEqual(record["auction_end"], "2026-06-01T13:00:00+00:00")

    def test_unrelated_database_is_rejected(self):
        other = self.path.with_name("other.sqlite")
        with registry.connect(other) as db:
            db.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaises(RuntimeError):
            registry.initialize(other)
        with registry.connect(other) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema").fetchone()[0], "unrelated")
