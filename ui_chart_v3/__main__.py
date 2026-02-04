"""Запуск UI v3 через python -m ui_chart_v3."""

from . import server


def main() -> int:
    return server.main()


if __name__ == "__main__":
    raise SystemExit(main())
