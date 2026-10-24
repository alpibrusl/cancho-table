"""The hints that the adoption of cancho-tools' main (#42, then #53; docs/architecture.md "The pin") gave to seven refusals that had none.

`toolbox.cli` and `toolbox.path` write the hint of `args.missing-value`, `args.bad-value`, `path.empty` and `path.too-long`; at the earlier pin (919e7ba) each was null. `toolbox.fail` writes, since
cancho-tools#53 (d3fed0c), the hint of `io.not-a-directory`, `io.permission-denied`, `io.read-failed` and `io.write-failed`; at af73b1b each was null (`io.write-failed` is listed so that `before` is true of the
package, but `table` writes that refusal on standard error as text, where there is no hint). That is the whole change of
the bytes of `table` (docs/architecture.md, "The pin", lists how it was measured), so the golden files of the earlier slices, which hold the md5 of what the tool answered then, are not re-recorded: `before` puts the
seven hints back to null in an answer, and the digests of those goldens read the answer through it. A golden therefore still fails on any other change of a byte, and `test_toolbox_pin.py` pins the new
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
    "io.not-a-directory": ["a name in the path that has to be a directory is a file; name a path whose directories are all directories"],
    "io.permission-denied": ["the user running the tool lacks permission for this path; check the permissions of the path and of the directories above it"],
    "io.read-failed": ["the operating system refused the operation (detail.errno is its code); check the path and the storage, then try again"],
    "io.write-failed": ["the operating system refused the operation (detail.errno is its code); check the path and the storage, then try again"],
}

_ALTERNATIVES = "|".join(re.escape(h) for hints in HINTS.values() for h in hints)
_RULES = "|".join(re.escape(r) for r in HINTS)
_PATTERN = re.compile(r'("rule":"(?:%s)","message":"(?:[^"\\]|\\.)*","hint":)"(?:%s)"' % (_RULES, _ALTERNATIVES))
_BYTES = re.compile(_PATTERN.pattern.encode())


def before(answer):
    """The answer (text or bytes) as the tool at the earlier pin wrote it: the hints of HINTS are null again."""
    if isinstance(answer, bytes):
        return _BYTES.sub(rb"\1null", answer)
    return _PATTERN.sub(r"\1null", answer)
