"""Command-line interface for libvisio-ng."""

import argparse
import sys
from pathlib import Path

from libvisio_ng import __version__, convert, get_page_info, extract_text


def main():
    parser = argparse.ArgumentParser(
        prog="visio2svg",
        description="Convert Microsoft Visio files to SVG",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    # convert
    p_conv = sub.add_parser("convert", help="Convert Visio file to SVG")
    p_conv.add_argument("input", help="Input Visio file (.vsdx, .vsd, etc.)")
    p_conv.add_argument("-o", "--output-dir", help="Output directory (default: current dir)")
    p_conv.add_argument("-p", "--page", type=int, help="Convert only this page (0-indexed)")

    # info
    p_info = sub.add_parser("info", help="Show page information")
    p_info.add_argument("input", help="Input Visio file")

    # text
    p_text = sub.add_parser("text", help="Extract all text from a Visio file")
    p_text.add_argument("input", help="Input Visio file")

    args = parser.parse_args()

    if args.command == "convert":
        output_dir = args.output_dir or "."
        if args.page is not None:
            from libvisio_ng import convert_page
            result = convert_page(args.input, args.page, output_dir)
            if result:
                print(result)
            else:
                print(f"Error: could not convert page {args.page}", file=sys.stderr)
                sys.exit(1)
        else:
            results = convert(args.input, output_dir)
            for f in results:
                print(f)
    elif args.command == "info":
        pages = get_page_info(args.input)
        for i, p in enumerate(pages):
            print(f"Page {i}: {p.get('name', 'Unnamed')} ({p.get('width', '?')}x{p.get('height', '?')})")
    elif args.command == "text":
        print(extract_text(args.input))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
