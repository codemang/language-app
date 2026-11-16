#!/usr/bin/env python3
"""Object-pronoun drills: direct (me, te, lo, la...), indirect (me, te, le...) and both
together (me lo, se lo...), one sentence each, English then Spanish.

  "Juan sees me." · Juan me ve.      "Juan gives it to him." · Juan se lo da.

Same voices and clip store as verbs.py (free, Kokoro on the Pi); existing clips
are reused. Output: web/verbs/pronouns.json, clips in web/verbs/clips/.

  python pronouns.py
"""

import json
import sys

from verbs import OUT, Speaker

# set id, title, [(item id, chip label, English, Spanish)]
SETS = [
    ("direct", "Direct object", [
        ("me", "me · me", "Juan sees me.", "Juan me ve."),
        ("te", "te · you", "Juan sees you.", "Juan te ve."),
        ("lo", "lo · him", "Juan sees him.", "Juan lo ve."),
        ("la", "la · her", "Juan sees her.", "Juan la ve."),
        ("nos", "nos · us", "Juan sees us.", "Juan nos ve."),
        ("los", "los · them", "Juan sees them.", "Juan los ve."),
        ("las", "las · them (f)", "Juan sees them, feminine.", "Juan las ve."),
    ]),
    ("indirect", "Indirect object", [
        ("me", "me · to me", "Juan gives me a book.", "Juan me da un libro."),
        ("te", "te · to you", "Juan gives you a book.", "Juan te da un libro."),
        ("le", "le · to him/her", "Juan gives him a book.", "Juan le da un libro."),
        ("nos", "nos · to us", "Juan gives us a book.", "Juan nos da un libro."),
        ("les", "les · to them", "Juan gives them a book.", "Juan les da un libro."),
    ]),
    # le/les + lo becomes se lo
    ("both", "Both together", [
        ("melo", "me lo · it to me", "Juan gives it to me.", "Juan me lo da."),
        ("telo", "te lo · it to you", "Juan gives it to you.", "Juan te lo da."),
        ("selo", "se lo · it to him/her", "Juan gives it to him.", "Juan se lo da."),
        ("noslo", "nos lo · it to us", "Juan gives it to us.", "Juan nos lo da."),
        ("selos", "se lo · it to them", "Juan gives it to them.", "Juan se lo da."),
    ]),
]


def main():
    spk = Speaker()
    out = {"sets": []}
    for sid, title, items in SETS:
        print(title, file=sys.stderr)
        out["sets"].append({"id": sid, "title": title, "items": [
            {"id": iid, "label": label, "en": en, "en_clip": spk.clip(en, "en"), "es": es, "clip": spk.clip(es, "es")}
            for iid, label, en, es in items]})
    (OUT / "pronouns.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"done, {spk.made} new clips", file=sys.stderr)


if __name__ == "__main__":
    main()
