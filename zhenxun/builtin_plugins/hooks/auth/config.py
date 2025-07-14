import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from strenum import StrEnum

LOGGER_COMMAND = "AuthChecker"


class SwitchEnum(StrEnum):
    ENABLE = "醒来"
    DISABLE = "休息吧"


WARNING_THRESHOLD = 0.5  # 警告阈值（秒）
