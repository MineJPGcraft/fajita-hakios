#!/usr/bin/env python3
"""Copy the current fajita bootargs and enable only the read-only RMI probe."""
import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("identity", "layout"), default="identity")
    args = parser.parse_args()

    tokens = args.source.read_text().split()
    values = {}
    for token in tokens:
        if "=" not in token:
            raise SystemExit(f"invalid boot argument: {token!r}")
        key, value = token.split("=", 1)
        if key in values and key != "fajita_touch_probe":
            raise SystemExit(f"duplicate boot argument: {key}")
        values[key] = value
    for required in ("fajita_touch", "fajita_touch_power", "fajita_touch_panel"):
        if values.get(required) != "1":
            raise SystemExit(f"probe bootargs require {required}=1")

    tokens = [token for token in tokens if not token.startswith("fajita_touch_probe=")]
    tokens.append("fajita_touch_probe=" + ("2" if args.mode == "layout" else "1"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(" ".join(tokens) + "\n")
    print(f"probe bootargs written: {args.output}")


if __name__ == "__main__":
    main()
