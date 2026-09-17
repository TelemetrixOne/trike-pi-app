from __future__ import annotations

from .config import ConfigError, config_arg_parser, load_config


def main() -> int:
    parser = config_arg_parser("Print a value from HPR Pi gateway config")
    parser.add_argument("key", help="dotted config key")
    parser.add_argument("--default", default="", help="default value if the key is missing")
    parser.add_argument("--expand", action="store_true", help="expand {trike_id}, {topic_root}, and related tokens")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        value = config.get(args.key, args.default)
        if value is None:
            value = args.default
        if isinstance(value, bool):
            print("true" if value else "false")
        elif isinstance(value, list):
            print(",".join(str(item) for item in value))
        elif args.expand and isinstance(value, str):
            print(config.expand(value))
        else:
            print(value)
    except ConfigError as exc:
        print(f"CONFIG_INVALID: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

