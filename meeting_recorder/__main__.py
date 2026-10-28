import sys

from .cli import parse_args, run


def main():
    args = parse_args()
    if args.cli or args.list_devices or len(sys.argv) > 1:
        run(args)
    else:
        from .app import main as gui
        gui()


if __name__ == "__main__":
    main()
