import unittest
import xml.etree.ElementTree as ET
import build_feed as b

HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n<channel>\n<title>UATAC</title>\n'
TAIL = "\n</channel>\n</rss>\n"


def item(i, title, extra="", desc="<![CDATA[<p>Опис &amp; склад</p>]]>"):
    return (f"<item>\n<g:id>{i}</g:id>\n<g:title>{title}</g:title>\n<g:description>{desc}</g:description>\n"
            f"<g:link>https://uatac.ua/x-{i}/</g:link>\n<g:availability>in stock</g:availability>\n"
            f"<g:price>480 UAH</g:price>{extra}\n</item>\n")


FEED = HEAD + "".join([
    item("431010", "Тактична шапка зимова флісова UATAC Multicam M"),
    item("431011", "Тактична шапка зимова флісова UATAC Multicam L"),
    item("10301011S", "Тактичні штани Lite UATAC Олива | S/Short"),
    item("10301012S", "Тактичні штани Lite UATAC Олива | M/Short"),
    item("10301012L", "Тактичні штани Lite UATAC Олива | M/Long"),
    item("LPB3XS", "Тактичні штани Legion Olive (Олива)"),
    item("LPB3ML", "Тактичні штани Legion Olive (Олива)"),
    item("41562010", "Кепка UATAC з велкро Pixel (Піксель)"),
    item("41562011", "Кепка UATAC з велкро Pixel (Піксель)"),
    item("55205111", "Бавовняні труси UATAC Хакі | S"),
    item("55205311", "Бавовняні труси UATAC Світло-зелений | S"),
    item("636012", "Трекінгові шкарпетки UATAC Олива 43-45"),
    item("636011", "Трекінгові шкарпетки UATAC Олива 40-42"),
    item("341415", "Тактична демісезонна куртка UATAC Gen 5.6 Multicam Ripstop | XXL"),
    item("777771", "Вже згрупований товар L", extra="\n<g:item_group_id>MY</g:item_group_id>"),
]) + TAIL


class T(unittest.TestCase):
    def setUp(self):
        self.out, self.stats = b.transform(FEED)
        b.validate(FEED, self.out, self.stats)
        ns = {"g": "http://base.google.com/ns/1.0"}
        self.items = {i.find("g:id", ns).text: i for i in ET.fromstring(self.out.encode()).iter("item")}
        self.ns = ns

    def grp(self, i):
        return self.items[i].find("g:item_group_id", self.ns).text

    def size(self, i):
        e = self.items[i].find("g:size", self.ns)
        return e.text if e is not None else None

    def test_sizes_of_one_model_share_group(self):
        self.assertEqual(self.grp("431010"), self.grp("431011"))
        self.assertEqual(self.grp("41562010"), self.grp("41562011"))
        self.assertEqual(self.grp("636011"), self.grp("636012"))

    def test_short_long_same_model(self):
        self.assertEqual(self.grp("10301011S"), self.grp("10301012L"))

    def test_letter_ids_group_by_title(self):
        self.assertEqual(self.grp("LPB3XS"), self.grp("LPB3ML"))
        self.assertTrue(self.grp("LPB3XS").startswith("t"))

    def test_different_models_not_merged(self):
        self.assertNotEqual(self.grp("55205111"), self.grp("55205311"))
        self.assertNotEqual(self.grp("431010"), self.grp("41562010"))

    def test_sizes_parsed_only_when_present(self):
        self.assertEqual(self.size("431010"), "M")
        self.assertEqual(self.size("10301012L"), "M/Long")
        self.assertEqual(self.size("341415"), "XXL")
        self.assertEqual(self.size("636012"), "43-45")
        self.assertIsNone(self.size("41562010"))
        self.assertIsNone(self.size("LPB3XS"))

    def test_gen56_is_not_a_size(self):
        self.assertNotIn("5.6", (self.size("341415") or ""))

    def test_existing_group_not_overwritten(self):
        self.assertEqual(self.grp("777771"), "MY")
        self.assertEqual(self.out.count("<g:item_group_id>MY</g:item_group_id>"), 1)

    def test_original_untouched(self):
        self.assertIn("<![CDATA[<p>Опис &amp; склад</p>]]>", self.out)
        self.assertEqual(self.stats["items"], 15)

    def test_validate_catches_damage(self):
        with self.assertRaises(Exception):
            b.validate(FEED, self.out.replace("480 UAH", "481 UAH", 1), self.stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)
