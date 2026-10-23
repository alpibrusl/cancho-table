"""The hints that the adoption of cancho-tools' main (#42, docs/architecture.md "The pin") gave to four refusals that had none.

`toolbox.cli` and `toolbox.path` write the hint of `args.missing-value`, `args.bad-value`, `path.empty` and `path.too-long`; at the earlier pin (919e7ba) each was null. That is the whole change of
the bytes of `table` (docs/architecture.md, "The pin", lists how it was measured), so the golden files of the earlier slices, which hold the md5 of what the tool answered then, are not re-recorded: `before` puts the
four hints back to null in an answer, and the digests of those goldens read the answer through it. A golden therefore still fails on any other change of a byte, and `test_toolbox_pin.py` pins the new
hints text by text.

`table`'s own `path.empty` and `path.too-long` (`qpath.cho`: a query's `from` and `join` paths, the file of `--join`) say the same two sentences as the package's operand refusals, since the follow-up of the
move; they were null at the earlier pin too, so `before` puts them back to null as well, by the same rule and text, and no golden changes for them.
"""

import re

HINTS = {
    "args.bad-value": ["give the flag a value of the kind detail.expected names"],
    "args.missing-value": ["put the value after the flag, or write the two as --flag=value"],
    "path.empty": ["name a directory, or leave --root out", "name a path; an empty argument names nothing"],
    "path.too-long": ["name a root of at most 4096 bytes", "use a path of at most 4096 bytes"],
}

_ALTERNATIVES = "|".join(re.escape(h) for hints in HINTS.values() for h in hints)
_RULES = "|".join(re.escape(r) for r in HINTS)
_PATTERN = re.compile(r'("rule":"(?:%s)","message":"(?:[^"\\]|\\.)*","hint":)"(?:%s)"' % (_RULES, _ALTERNATIVES))
_BYTES = re.compile(_PATTERN.pattern.encode())


def before(answer):
    """The answer (text or bytes) as the tool at the earlier pin wrote it: the four hints of HINTS are null again."""
    if isinstance(answer, bytes):
        return _BYTES.sub(rb"\1null", answer)
    return _PATTERN.sub(r"\1null", answer)
