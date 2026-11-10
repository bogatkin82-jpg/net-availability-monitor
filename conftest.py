"""Конфигурация pytest для репозитория.

Legacy-скрипты macOS-QA (test_api.py, test_ui.py, test_incidents.py,
test_persistence.py) запускаются напрямую как standalone-скрипты: им нужен
живой сервер и/или браузер, поэтому в обычный pytest-прогон они не входят.
"""

collect_ignore = [
    "tests/test_api.py",
    "tests/test_ui.py",
    "tests/test_incidents.py",
    "tests/test_persistence.py",
]
