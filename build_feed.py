#!/usr/bin/env python3
"""
UATAC: виправлений товарний фід для Yespo (eSputnik) і Google Merchant Center.

Бере оригінальний Google-фід Хорошопа і додає до кожного товару:
  * <g:item_group_id>  - спільний ідентифікатор моделі (усі розміри однієї моделі)
  * <g:size>           - розмір, ЛИШЕ якщо він є в назві (нічого не вигадуємо)
Усе інше лишається байт-у-байт як в оригіналі.

Правило групування:
  * ID = цифри (+ до 2 літер): група = ID без останньої цифри (остання цифра = розмір,
    літери L/S = Long/Short), напр. 41561410, 41561411 -> g4156141
  * ID з літерами (LPB3XS, LPB3ML...): група = хеш назви без розміру
Підозрілі групи (різні назви в одній групі) потрапляють у report.json для перегляду.

Тільки стандартна бібліотека Python 3.9+.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ITEM_RE = re.compile(r"<item>.*?</item>", re.S)
ID_RE = re.compile(r"<g:id>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</g:id>", re.S)
TITLE_RE = re.compile(r"<g:title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</g:title>", re.S)
HAS_GROUP_RE = re.compile(r"<g:item_group_id>")
HAS_SIZE_RE = re.compile(r"<g:size>")

# Розмір в кінці назви: "... | XXL", "... L", "... M/Long", "... 43-45"
SIZE_RE = re.compile(
    r"(?:\|\s*)?(?<![\w/-])"
    r"(?P<size>(?:XXS|XS|S|M|L|XL|XXL|[3-5]\s?XL)(?:\s*/\s*(?:Long|Short))?|4\d-4\d)"
    r"\s*$",
    re.I,
)
NUMERIC_ID_RE = re.compile(r"^(\d{4,})([A-Za-z]{0,2})$")


def unescape_xml(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'"))


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_size(title: str):
    m = SIZE_RE.search(title)
    if not m:
        return None
    size = re.sub(r"\s+", "", m.group("size")).upper()
    size = size.replace("/LONG", "/Long").replace("/SHORT", "/Short")
    return size


def strip_size(title: str) -> str:
    t = SIZE_RE.sub("", title)
    t = re.sub(r"\s*\|\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def group_key(item_id: str, title: str) -> str:
    m = NUMERIC_ID_RE.match(item_id)
    if m:
        return "g" + m.group(1)[:-1]
    return "t" + hashlib.sha1(strip_size(title).encode("utf-8")).hexdigest()[:10]


def token_set(title: str):
    return set(re.findall(r"\w+", strip_size(title)))


def fetch(url: str, retries: int = 3, timeout: int = 120) -> str:
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uatac-feed-grouper/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * attempt)
    raise RuntimeError(f"Не вдалося завантажити {url.split('?')[0]}: {last}")


def transform(source: str):
    """Повертає (новий_xml, статистика)."""
    items = ITEM_RE.findall(source)
    if not items:
        raise ValueError("У фіді немає жодного <item>")

    parsed = []
    for raw in items:
        mid, mt = ID_RE.search(raw), TITLE_RE.search(raw)
        if not mid or not mt:
            raise ValueError("Товар без g:id або g:title - фід пошкоджений")
        parsed.append((raw, mid.group(1).strip(), unescape_xml(mt.group(1)).strip()))

    groups = {}
    for raw, item_id, title in parsed:
        groups.setdefault(group_key(item_id, title), []).append((item_id, title))

    out_parts, last_end = [], 0
    with_size = 0
    pos = 0
    for raw, item_id, title in parsed:
        start = source.index(raw, pos)
        end = start + len(raw)
        pos = end
        out_parts.append(source[last_end:start])
        last_end = end

        inject = ""
        if not HAS_GROUP_RE.search(raw):
            inject += f"\n<g:item_group_id>{group_key(item_id, title)}</g:item_group_id>"
        size = parse_size(title)
        if size and not HAS_SIZE_RE.search(raw):
            inject += f"\n<g:size>{escape_xml(size)}</g:size>"
            with_size += 1
        idm = ID_RE.search(raw)
        out_parts.append(raw[: idm.end()] + inject + raw[idm.end():])
    out_parts.append(source[last_end:])
    result = "".join(out_parts)

    # Підозрілі групи: назви суттєво різняться (можлива помилка групування)
    suspicious = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        sets = [token_set(t) for _, t in members]
        base = sets[0]
        worst = min((len(base & s) / max(len(base | s), 1)) for s in sets)
        if worst < 0.6:
            suspicious.append({"group": key, "ids": [i for i, _ in members][:12],
                               "titles": sorted({strip_size(t) for _, t in members})[:4]})

    stats = {
        "items": len(parsed),
        "groups": len(groups),
        "groups_with_variants": sum(1 for g in groups.values() if len(g) > 1),
        "largest_group": max(len(g) for g in groups.values()),
        "items_with_size": with_size,
        "suspicious_groups": suspicious[:30],
        "suspicious_count": len(suspicious),
    }
    return result, stats


INJECT_RE = re.compile(r"\n<g:(?:item_group_id|size)>[^<]*</g:(?:item_group_id|size)>")


def validate(source: str, result: str, stats: dict):
    """Жорсткі перевірки: ми нічого не зламали в оригіналі."""
    ET.fromstring(result.encode("utf-8"))  # well-formed XML
    if INJECT_RE.sub("", result) != INJECT_RE.sub("", source):
        raise AssertionError("Після видалення доданих тегів результат відрізняється від оригіналу")
    src_ids = [m.strip() for m in ID_RE.findall(source)]
    res_ids = [m.strip() for m in ID_RE.findall(result)]
    if src_ids != res_ids or len(set(res_ids)) != len(res_ids):
        raise AssertionError("ID товарів змінились або не унікальні")
    items = ITEM_RE.findall(result)
    if any(not HAS_GROUP_RE.search(i) for i in items):
        raise AssertionError("Є товари без item_group_id")
    if not (1 <= stats["groups"] <= stats["items"]):
        raise AssertionError("Некоректна кількість груп")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-url", default=os.environ.get("SOURCE_FEED_URL"))
    ap.add_argument("--source-file")
    ap.add_argument("--prev-url", default=os.environ.get("PREV_FEED_URL"),
                    help="URL попереднього опублікованого фіду (для перевірки різкого падіння)")
    ap.add_argument("--out", default="dist/uatac_feed.xml")
    ap.add_argument("--report", default="dist/report.json")
    ap.add_argument("--min-items", type=int, default=500)
    args = ap.parse_args()

    if args.source_file:
        source = open(args.source_file, encoding="utf-8").read()
    elif args.source_url:
        source = fetch(args.source_url)
    else:
        sys.exit("Потрібен --source-url (або змінна SOURCE_FEED_URL) чи --source-file")

    if "<rss" not in source[:2000]:
        sys.exit("Відповідь не схожа на RSS-фід - публікацію зупинено")

    result, stats = transform(source)
    validate(source, result, stats)

    if stats["items"] < args.min_items:
        sys.exit(f"Підозріло мало товарів ({stats['items']} < {args.min_items}) - публікацію зупинено")

    if args.prev_url:
        try:
            prev = fetch(args.prev_url, retries=1, timeout=60)
            prev_n = len(ITEM_RE.findall(prev))
            stats["previous_items"] = prev_n
            if prev_n and stats["items"] < 0.8 * prev_n:
                sys.exit(f"Товарів стало {stats['items']} проти {prev_n} раніше (падіння >20%) - публікацію зупинено")
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            stats["previous_items"] = f"недоступний ({e.__class__.__name__})"

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(result)
    stats["generated_at_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in stats.items() if k != "suspicious_groups"}, ensure_ascii=False))
    if stats["suspicious_count"]:
        print(f"УВАГА: {stats['suspicious_count']} груп з різними назвами - див. report.json")


if __name__ == "__main__":
    main()
