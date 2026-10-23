"""`spine-design` CLI: the same code path as the MCP tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from spine_core.resolve import SpinePathError
from spine_mcp.writes import SpineWriteError

from . import imaging, tools


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="spine-design", description="Visual memory for a running UI")
    parser.add_argument("--project", "-p", help="project path (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("brief")
    sub.add_parser("script", help="print extract.js")

    rec = sub.add_parser("record", help="store a capture")
    rec.add_argument("--agent", required=True)
    rec.add_argument("--extraction", required=True, help="path to the extract.js JSON")
    rec.add_argument("--image", help="screenshot file")
    rec.add_argument("--route", default="/")
    rec.add_argument("--viewport")
    rec.add_argument("--theme")
    rec.add_argument("--state", default="default")

    get = sub.add_parser("get")
    get.add_argument("--view")
    get.add_argument("--which", default="latest")
    get.add_argument("--save-image", help="write the preview webp here")

    dif = sub.add_parser("diff")
    dif.add_argument("--view")
    dif.add_argument("-a", default="previous")
    dif.add_argument("-b", default="latest")
    dif.add_argument("--threshold", type=float, default=0.02)

    tok = sub.add_parser("tokens")
    tok.add_argument("--view")
    com = sub.add_parser("components")
    com.add_argument("--name")
    com.add_argument("--view")

    ens = sub.add_parser("ensure", help="capture through BrowserOS neo if the view is stale")
    ens.add_argument("--agent", required=True)
    ens.add_argument("--route", default="/")
    ens.add_argument("--viewport", default="1280x800")
    ens.add_argument("--theme")
    ens.add_argument("--state", default="default")
    ens.add_argument("--force", action="store_true")
    ens.add_argument("--url")

    chk = sub.add_parser("check", help="compare the running UI with DESIGN.md")
    chk.add_argument("--view")

    fgt = sub.add_parser("forget")
    fgt.add_argument("--view")
    fgt.add_argument("--before")
    fgt.add_argument("--everything", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "brief":
            print(tools.design_brief(args.project)["brief"])
        elif args.command == "script":
            print(tools.extraction_script())
        elif args.command == "record":
            view = {"route": args.route, "viewport": args.viewport, "theme": args.theme, "state": args.state}
            _json(tools.record_capture(args.project, args.agent, view, args.extraction, image_path=args.image))
        elif args.command == "get":
            result, image = tools.get_capture(args.project, args.view, args.which, bool(args.save_image))
            if image and args.save_image:
                Path(args.save_image).write_bytes(image)
                result["preview_saved"] = args.save_image
            _json(result)
        elif args.command == "diff":
            _json(tools.diff_captures(args.project, args.view, args.a, args.b, args.threshold))
        elif args.command == "tokens":
            _json(tools.tokens(args.project, args.view))
        elif args.command == "components":
            _json(tools.components(args.project, args.name, args.view))
        elif args.command == "ensure":
            _json(tools.ensure_capture(args.project, args.agent, args.route, args.viewport, args.theme,
                                       args.state, args.force, args.url))
        elif args.command == "check":
            _json(tools.check_design(args.project, args.view))
        elif args.command == "forget":
            _json(tools.forget(args.project, args.view, args.before, args.everything))
    except (SpinePathError, SpineWriteError, imaging.ImageError, OSError) as exc:
        print(f"spine-design: {exc}", file=sys.stderr)
        return 1
    return 0


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
