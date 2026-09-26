"""Tests for deterministic normalization. Run:

    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m unittest -v test_normalize
"""
import os
import sys
import unicodedata
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from er import normalize as nz  # noqa: E402


class TestUnicode(unittest.TestCase):
    def test_nfkc_composed_equals_decomposed(self):
        base = "Café"                              # Café, é precomposed
        composed = unicodedata.normalize("NFC", base)
        decomposed = unicodedata.normalize("NFD", base)  # e + U+0301 combining acute
        self.assertNotEqual(composed, decomposed)         # genuinely different bytes
        self.assertEqual(nz.name_norm(composed), nz.name_norm(decomposed))
        self.assertEqual(nz.name_norm(composed), "cafe")

    def test_fullwidth_and_ligature_nfkc(self):
        self.assertEqual(nz.name_norm("ＡＣＭＥ"), "acme")  # fullwidth
        self.assertEqual(nz.name_norm("ﬁrst"), "first")               # fi ligature

    def test_casefold_german_sharp_s(self):
        self.assertEqual(nz.name_norm("STRAßE"), nz.name_norm("strasse"))

    def test_devanagari_preserved(self):
        s = "मुंबई"  # मुंबई — must not be emptied/accent-stripped
        out = nz.name_norm(s)
        self.assertTrue(out)
        self.assertTrue(any("DEVANAGARI" in unicodedata.name(c, "") for c in out))

    def test_idempotent(self):
        for s in ["Café Ltd.", "  A.B.  C&D ", "मुंबई", "ACME"]:
            self.assertEqual(nz.name_norm(s), nz.name_norm(nz.name_norm(s)))


class TestPunctuationAndAmp(unittest.TestCase):
    def test_punctuation_becomes_space(self):
        self.assertEqual(nz.name_norm("A.C.M.E, Inc.!!"), "a c m e inc")

    def test_ampersand_to_and(self):
        self.assertEqual(nz.name_norm("Barnes & Noble"), nz.name_norm("Barnes and Noble"))

    def test_leading_junk_stripped(self):
        self.assertEqual(nz.name_norm("-- <<Acme>>"), "acme")


class TestSuffixes(unittest.TestCase):
    def test_variants_share_nosuffix(self):
        self.assertEqual(nz.name_nosuffix("Acme Pvt Ltd"), "acme")
        self.assertEqual(nz.name_nosuffix("Acme Private Limited"), "acme")
        self.assertEqual(nz.name_nosuffix("Acme Corp"), nz.name_nosuffix("Acme Corporation"))

    def test_all_suffix_name_not_emptied(self):
        self.assertNotEqual(nz.name_nosuffix("Pvt Ltd"), "")  # fallback keeps tokens


class TestKeys(unittest.TestCase):
    def test_word_order_invariance(self):
        self.assertEqual(nz.name_sorted("John Smith Traders"),
                         nz.name_sorted("Traders Smith John"))

    def test_acronym(self):
        self.assertEqual(nz.name_acronym("International Business Machines"), "ibm")
        self.assertEqual(nz.name_acronym("Acme"), "")  # single token -> no acronym

    def test_num_tokens_and_blank_address(self):
        self.assertEqual(nz.num_tokens("Flat 12, Sector 7, PIN 400001"),
                         ["12", "400001", "7"])
        self.assertEqual(nz.num_tokens(""), [])
        self.assertEqual(nz.addr_norm(""), "")


class TestCountryOpenSet(unittest.TestCase):
    def test_unseen_country_passthrough(self):
        self.assertEqual(nz.country_norm("France"), "france")
        self.assertEqual(nz.country_norm(" Brazil "), "brazil")  # never-before-seen
        self.assertEqual(nz.country_norm(""), "")


class TestRecordPreservesRaw(unittest.TestCase):
    def test_record_is_additive(self):
        rec = nz.normalize_record("Café & Co", "12 Main St", "France")
        self.assertEqual(rec["norm_version"], nz.NORM_VERSION)
        self.assertEqual(rec["name_norm"], "cafe and co")
        self.assertEqual(rec["country_norm"], "france")
        self.assertEqual(rec["num_tokens"], ["12"])


if __name__ == "__main__":
    unittest.main()
