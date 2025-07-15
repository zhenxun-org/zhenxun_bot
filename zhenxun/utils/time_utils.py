from datetime import date, datetime
import re

import pytz


class TimeUtils:
    DEFAULT_TIMEZONE = pytz.timezone("Asia/Shanghai")

    @classmethod
    def get_day_start(cls, target_date: date | datetime | None = None) -> datetime:
        """获取某天的0点时间

        返回:
            datetime: 今天某天的0点时间
        """
        if not target_date:
            target_date = datetime.now(cls.DEFAULT_TIMEZONE)

        if isinstance(target_date, datetime) and target_date.tzinfo is None:
            target_date = cls.DEFAULT_TIMEZONE.localize(target_date)

        return (
            target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            if isinstance(target_date, datetime)
            else datetime.combine(
                target_date, datetime.min.time(), tzinfo=cls.DEFAULT_TIMEZONE
            )
        )

    @classmethod
    def is_valid_date(cls, date_text: str, separator: str = "-") -> bool:
        """日期是否合法

        参数:
            date_text: 日期
            separator: 分隔符

        返回:
            bool: 日期是否合法
        """
        try:
            datetime.strptime(date_text, f"%Y{separator}%m{separator}%d")
            return True
        except ValueError:
            return False

    @classmethod
    def parse_time_string(cls, time_str: str) -> int:
        """
        将带有单位的时间字符串 (e.g., "10s", "5m", "1h") 解析为总秒数。
        """
        time_str = time_str.lower().strip()
        match = re.match(r"^(\d+)([smh])$", time_str)
        if not match:
            raise ValueError(
                f"无效的时间格式: '{time_str}'。请使用如 '30s', '10m', '2h' 的格式。"
            )

        value, unit = int(match.group(1)), match.group(2)

        if unit == "s":
            return value
        if unit == "m":
            return value * 60
        if unit == "h":
            return value * 3600
        return 0

    @classmethod
    def format_duration(cls, seconds: float) -> str:
        """
        将秒数格式化为易于阅读的字符串 (例如 "1小时5分钟", "30.5秒")
        """
        seconds = round(seconds, 1)
        if seconds < 0.1:
            return "不到1秒"
        if seconds < 60:
            return f"{seconds}秒"

        minutes, sec_remainder = divmod(int(seconds), 60)

        if minutes < 60:
            if sec_remainder == 0:
                return f"{minutes}分钟"
            return f"{minutes}分钟{sec_remainder}秒"

        hours, rem_minutes = divmod(minutes, 60)
        if rem_minutes == 0:
            return f"{hours}小时"
        return f"{hours}小时{rem_minutes}分钟"
