"""Public listener and local evaluation commands; no delivery entry point."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['monitor', 'evaluate', 'verify'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--worker')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.command == 'monitor':
        from .monitor import run
        result = run(args.root, args.worker)
    elif args.command == 'verify':
        from .common import verify
        result = {'verified_files': verify(args.root)}
    else:
        from .evaluate import run
        result = run(args.root, args.worker, args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
