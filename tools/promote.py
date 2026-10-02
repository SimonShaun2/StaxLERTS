#!/usr/bin/env python3
"""Copy the StaxBot DEV twin onto the production Pine file.

The development script and the production script stay identical except for the
DEV title, HUD, alert tags, and the Send DEV Alerts switch. This tool strips
those differences and writes production/staxbot.pine.

    python3 tools/promote.py
    python3 tools/promote.py --write
    python3 tools/promote.py --write --version 2.5.3

Without --write, nothing is changed. Exit 0 means the rendered production text
already matches production/staxbot.pine. A version bump also stores that text
at archive/staxbot_<version>.pine and updates the baseline version inside the
DEV twin.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_PATH = ROOT / "development" / "staxbot_dev.pine"
PROD_PATH = ROOT / "production" / "staxbot.pine"
ARCHIVE = ROOT / "archive"

HEADER_BEGIN = "// BEGIN DEV-HEADER"
HEADER_END = "// END DEV-HEADER"
ONLY_BEGIN = "// BEGIN DEV-ONLY"
ONLY_END = "// END DEV-ONLY"
BASELINE_RE = re.compile(r"Baseline StaxBot (\d+\.\d+\.\d+)")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

FORMING_DEV = (
    "            if sendDevAlertsIn\n"
    "                alert(formMsg, alert.freq_once_per_bar)"
)
FORMING_PROD = "            alert(formMsg, alert.freq_once_per_bar)"

BANNED = (
    "sendDevAlertsIn",
    "staxbot-dev",
    "StaxBot DEV",
    "STAXBOT DEV",
    "BEGIN DEV-",
    "END DEV-",
    '"env":"dev"',
)


def production_header(version: str) -> str:
    return (
        f"// StaxBot {version}\n"
        f"// New saved script. The legend reads StaxBot. The HUD reads STAXBOT {version}.\n"
    )


def _drop_marked_block(text: str, begin: str, end: str) -> tuple[str, str]:
    start = text.find(begin)
    if start == -1:
        raise SystemExit(f"{DEV_PATH.name} is missing {begin}")
    stop = text.find(end, start)
    if stop == -1:
        raise SystemExit(f"{DEV_PATH.name} is missing {end}")
    stop += len(end)
    if text[stop:stop + 1] == "\n":
        stop += 1
    if start > 0 and text[start - 1] != "\n":
        raise SystemExit(f"{begin} must be at the start of a line")
    return text[start:stop], text[:start] + text[stop:]


def baseline_version(text: str) -> str:
    match = BASELINE_RE.search(text)
    if not match:
        raise SystemExit(f"{DEV_PATH.name} is missing a 'Baseline StaxBot X.Y.Z' line")
    return match.group(1)


def render_production(text: str, version: str | None = None) -> str:
    baseline = baseline_version(text)
    target = version or baseline
    if not VERSION_RE.fullmatch(target):
        raise SystemExit(f"Version must look like 2.5.2, got {target!r}")
    if text.count(HEADER_BEGIN) != 1:
        raise SystemExit(f"{DEV_PATH.name} must contain one {HEADER_BEGIN}")
    _, text = _drop_marked_block(text, HEADER_BEGIN, HEADER_END)
    while ONLY_BEGIN in text:
        _, text = _drop_marked_block(text, ONLY_BEGIN, ONLY_END)
    marker = "//@version=6\n"
    if not text.startswith(marker):
        raise SystemExit("DEV file must start with //@version=6")
    text = marker + production_header(target) + text[len(marker):]
    text = text.replace(
        'strategy("StaxBot DEV", shorttitle = "StaxBot DEV"',
        'strategy("StaxBot", shorttitle = "StaxBot"',
        1,
    )
    text = text.replace('"source":"staxbot-dev","env":"dev"', '"source":"staxbot"')
    text = text.replace(f'"version":"{baseline}-dev"', f'"version":"{target}"')
    text = text.replace('"STAXBOT DEV"', f'"STAXBOT {target}"')
    if FORMING_DEV not in text:
        raise SystemExit("DEV file is missing the gated break_forming alert()")
    text = text.replace(FORMING_DEV, FORMING_PROD, 1)
    text = text.replace("sendDevAlertsIn and ", "")
    for banned in BANNED:
        if banned in text:
            raise SystemExit(f"Promotion still contains {banned!r}. Refusing to write a DEV-tagged production file.")
    if f'"version":"{target}"' not in text:
        raise SystemExit(f"Production render has no version {target}")
    if not text.startswith("//@version=6\n"):
        raise SystemExit("Production render lost //@version=6")
    return text


def bump_dev_baseline(text: str, old: str, new: str) -> str:
    updated = text.replace(f"Baseline StaxBot {old}", f"Baseline StaxBot {new}")
    updated = updated.replace(f'"version":"{old}-dev"', f'"version":"{new}-dev"')
    updated = updated.replace(f"version {old}-dev", f"version {new}-dev")
    if f"Baseline StaxBot {new}" not in updated or f'"version":"{new}-dev"' not in updated:
        raise SystemExit("Could not update the DEV baseline version")
    if f"Baseline StaxBot {old}" in updated or f'"version":"{old}-dev"' in updated:
        raise SystemExit("Old DEV baseline version is still present")
    return updated


def archive_name(version: str) -> Path:
    return ARCHIVE / f"staxbot_{version.replace('.', '_')}.pine"


def _diff(current: str, rendered: str) -> str:
    lines = difflib.unified_diff(
        current.splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile="production/staxbot.pine",
        tofile="rendered from development/staxbot_dev.pine",
    )
    shown = []
    for index, line in enumerate(lines):
        if index >= 80:
            shown.append("... diff truncated ...\n")
            break
        shown.append(line)
    return "".join(shown)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="Overwrite production/staxbot.pine")
    parser.add_argument("--version", help="Production version to stamp, such as 2.5.3")
    args = parser.parse_args(argv)

    dev_text = DEV_PATH.read_text()
    baseline = baseline_version(dev_text)
    target = args.version or baseline
    if args.version and not VERSION_RE.fullmatch(args.version):
        print(f"Version must look like 2.5.2, got {args.version!r}", file=sys.stderr)
        return 1
    rendered = render_production(dev_text, target)
    current = PROD_PATH.read_text() if PROD_PATH.exists() else ""
    same = rendered == current

    if not args.write:
        if same:
            print(f"production/staxbot.pine already matches the DEV twin at {target}.")
            return 0
        print(f"DEV twin would change production/staxbot.pine ({target}).")
        print(_diff(current, rendered), end="")
        print("Nothing written. Re-run with --write to publish.")
        return 1

    if args.version and args.version != baseline:
        archived = archive_name(target)
        if archived.exists() and archived.read_text() != rendered:
            print(f"{archived.relative_to(ROOT)} already exists and differs. Not writing.", file=sys.stderr)
            return 1
        DEV_PATH.write_text(bump_dev_baseline(dev_text, baseline, target))
        archived.write_text(rendered)
        print(f"Updated {DEV_PATH.relative_to(ROOT)} baseline to {target}.")
        print(f"Wrote {archived.relative_to(ROOT)}.")
    elif not same:
        snapshot = archive_name(baseline)
        if snapshot.exists() and snapshot.read_text() != rendered:
            print(
                f"Writing {target} over production, which will differ from {snapshot.relative_to(ROOT)}. "
                "Pass --version for the next number when the trading logic changed."
            )

    PROD_PATH.write_text(rendered)
    print(f"Wrote {PROD_PATH.relative_to(ROOT)} as StaxBot {target}.")
    print("Paste that file into the live TradingView script, then recreate its alert.")
    print(f"Changelog line: Promoted StaxBot DEV to production {target}. Live file is production/staxbot.pine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
