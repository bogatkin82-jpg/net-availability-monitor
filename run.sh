#!/usr/bin/env bash
# Запуск дашборда мониторинга доступности сети.
# Все аргументы пробрасываются в server.py, например:
#   ./run.sh --port 8100 --interval 30
#   ./run.sh --config /path/to/config.json
#   NETMON_CONFIG=/path/to/config.json ./run.sh
#
# Настройки читаются из config.json рядом с этим скриптом; флаги --port/--interval
# и переменная NETMON_CONFIG их переопределяют (см. docs/windows.md и docs/api-contract.md).
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -B server.py "$@"
