from __future__ import annotations

from .config import ConfigError, config_arg_parser, load_config


def main() -> int:
    parser = config_arg_parser("Validate HPR Pi gateway config")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG_INVALID: {exc}")
        return 1
    print(f"CONFIG_OK: {config.trike_id} {config.environment} mqtt={config.mqtt.host}:{config.mqtt.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

