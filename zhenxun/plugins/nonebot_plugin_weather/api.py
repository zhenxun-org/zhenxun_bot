import httpx
import json
import re
import xml.etree.ElementTree as ET
from typing import Optional
from pydantic import BaseModel


# Deleted:def _parse_warning_level(text: str) -> str:
# Deleted:    """从文本中提取预警等级"""
# Deleted:    if "红色" in text or "红" in text:
# Deleted:        return "red"
# Deleted:    if "橙色" in text or "橙" in text:
# Deleted:        return "orange"
# Deleted:    if "黄色" in text or "黄" in text:
# Deleted:        return "yellow"
# Deleted:    if "蓝色" in text or "蓝" in text:
# Deleted:        return "blue"
# Deleted:    return "unknown"

class WeatherData(BaseModel):
    city: str
    date: str
    weather: str
    temperature: str
    humidity: str
    wind_direction: str
    wind_scale: str
    visibility: str = "--"

class DailyForecast(BaseModel):
    date: str
    day_weather: str
    night_weather: str
    temp_max: str
    temp_min: str
    wind_dir_day: str
    wind_scale_day: str

# Deleted:class WarningInfo(BaseModel):
# Deleted:    title: str
# Deleted:    type: str
# Deleted:    level: str
# Deleted:    text: str
# Deleted:    pub_time: str

class AirQuality(BaseModel):
    aqi: str
    level: str
    pm25: str
    pm10: str
    o3: str
    co: str
    no2: str
    so2: str


async def get_weather(city: str, api_key: str, api_host: str) -> Optional[WeatherData]:
    if not api_key or not api_host:
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        city_id = await _get_city_id(client, city, api_key, api_host)
        if not city_id:
            return None
        try:
            resp = await client.get(
                f"https://{api_host}/v7/weather/now",
                params={"location": city_id, "key": api_key, "lang": "zh"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        if data.get("code") != "200":
            return None
        now = data["now"]
        return WeatherData(
            city=city,
            date="今天",
            weather=now.get("text", "未知"),
            temperature=now.get("temp", "--"),
            humidity=now.get("humidity", "--"),
            wind_direction=now.get("windDir", "--"),
            wind_scale=now.get("windScale", "--"),
            visibility=now.get("vis", "--"),
        )


async def get_forecast(city: str, days: int, api_key: str, api_host: str) -> Optional[list[DailyForecast]]:
    if not api_key or not api_host:
        return None
    days = min(max(days, 1), 7)
    async with httpx.AsyncClient(timeout=30) as client:
        city_id = await _get_city_id(client, city, api_key, api_host)
        if not city_id:
            return None
        try:
            resp = await client.get(
                f"https://{api_host}/v7/weather/{days}d",
                params={"location": city_id, "key": api_key, "lang": "zh"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        if data.get("code") != "200":
            return None
        result = []
        for day in data.get("daily", [])[:days]:
            result.append(DailyForecast(
                date=day.get("fxDate", ""),
                day_weather=day.get("textDay", "未知"),
                night_weather=day.get("textNight", "未知"),
                temp_max=day.get("tempMax", "--"),
                temp_min=day.get("tempMin", "--"),
                wind_dir_day=day.get("windDirDay", "--"),
                wind_scale_day=day.get("windScaleDay", "--"),
            ))
        return result


# Deleted:# ==================== 预警查询（双重机制）====================
# Deleted:async def get_warnings(city: str, api_key: str, api_host: str) -> Optional[list[WarningInfo]]:
# Deleted:    """
# Deleted:    获取气象预警
# Deleted:    1. 和风天气 API（需要商业版 Key，免费 Key 会 403 自动跳过）
# Deleted:    2. 中华万年历 wthrcdn（免费，XML 格式，最稳定）
# Deleted:    """
# Deleted:    # 1. 尝试和风天气（商业版）
# Deleted:    if api_key and api_host:
# Deleted:        result = await _get_warnings_qweather(city, api_key, api_host)
# Deleted:        if result is not None:
# Deleted:            return result
# Deleted:
# Deleted:    # 2. 降级到中华万年历 API（免费）
# Deleted:    result = await _get_warnings_wthrcdn(city)
# Deleted:    return result
# Deleted:
# Deleted:
# Deleted:async def _get_warnings_qweather(city: str, api_key: str, api_host: str) -> Optional[list[WarningInfo]]:
# Deleted:    """和风天气预警（需要商业版 Key）"""
# Deleted:    async with httpx.AsyncClient(timeout=30) as client:
# Deleted:        city_id = await _get_city_id(client, city, api_key, api_host)
# Deleted:        if not city_id:
# Deleted:            return None
# Deleted:        try:
# Deleted:            resp = await client.get(
# Deleted:                f"https://{api_host}/v7/warning/now",
# Deleted:                params={"location": city_id, "key": api_key, "lang": "zh"}
# Deleted:            )
# Deleted:            resp.raise_for_status()
# Deleted:            data = resp.json()
# Deleted:            from nonebot import logger
# Deleted:            logger.info(f"[Weather] 和风预警 API 响应: {data.get('code')} | 城市: {city}")
# Deleted:        except Exception as e:
# Deleted:            from nonebot import logger
# Deleted:            logger.warning(f"[Weather] 和风预警 API 请求失败: {e}")
# Deleted:            return None
# Deleted:
# Deleted:        if data.get("code") != "200":
# Deleted:            return None
# Deleted:
# Deleted:        result = []
# Deleted:        for w in data.get("warning", []):
# Deleted:            level_map = {
# Deleted:                "蓝色": "blue", "黄色": "yellow", "橙色": "orange", "红色": "red",
# Deleted:                "蓝": "blue", "黄": "yellow", "橙": "orange", "红": "red",
# Deleted:            }
# Deleted:            level = level_map.get(w.get("level", "").lower(), w.get("level", "unknown"))
# Deleted:            result.append(WarningInfo(
# Deleted:                title=w.get("title", ""),
# Deleted:                type=w.get("typeName", ""),
# Deleted:                level=level,
# Deleted:                text=w.get("text", ""),
# Deleted:                pub_time=w.get("pubTime", ""),
# Deleted:            ))
# Deleted:        return result if result else None
# Deleted:
# Deleted:
# Deleted:async def _get_warnings_wthrcdn(city: str) -> Optional[list[WarningInfo]]:
# Deleted:    """
# Deleted:    从中华万年历 API 获取预警（免费，XML 格式）
# Deleted:    接口：http://wthrcdn.etouch.cn/WeatherApi?city=城市名
# Deleted:    返回 XML 中包含 <alarm> 节点（无预警时该节点不存在）
# Deleted:    """
# Deleted:    from nonebot import logger
# Deleted:    try:
# Deleted:        async with httpx.AsyncClient(timeout=10) as client:
# Deleted:            resp = await client.get(
# Deleted:                "http://wthrcdn.etouch.cn/WeatherApi",
# Deleted:                params={"city": city},
# Deleted:                headers={
# Deleted:                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
# Deleted:                }
# Deleted:            )
# Deleted:            resp.raise_for_status()
# Deleted:            xml_text = resp.text
# Deleted:
# Deleted:            if not xml_text or "<resp>" not in xml_text:
# Deleted:                logger.debug(f"[Weather] wthrcdn 响应异常: {xml_text[:100] if xml_text else '空响应'}")
# Deleted:                return None
# Deleted:
# Deleted:            root = ET.fromstring(xml_text)
# Deleted:            alarms = root.findall("alarm")
# Deleted:            if not alarms:
# Deleted:                return None  # 无预警
# Deleted:
# Deleted:            result = []
# Deleted:            for alarm in alarms:
# Deleted:                alarm_type = alarm.findtext("type", "")
# Deleted:                alarm_level = alarm.findtext("level", "")
# Deleted:                alarm_txt = alarm.findtext("txt", "")
# Deleted:                alarm_time = alarm.findtext("time", "")
# Deleted:                alarm_city = alarm.findtext("city", city)
# Deleted:
# Deleted:                # 拼接标题
# Deleted:                title_parts = [p for p in [alarm_type, alarm_level] if p]
# Deleted:                title_suffix = "预警" if title_parts else "气象预警"
# Deleted:                title = "".join(title_parts) + title_suffix
# Deleted:
# Deleted:                level = _parse_warning_level(alarm_level + title)
# Deleted:
# Deleted:                result.append(WarningInfo(
# Deleted:                    title=f"{alarm_city}{title}",
# Deleted:                    type=alarm_type,
# Deleted:                    level=level,
# Deleted:                    text=alarm_txt or title,
# Deleted:                    pub_time=alarm_time or "",
# Deleted:                ))
# Deleted:
# Deleted:            return result if result else None
# Deleted:
# Deleted:    except Exception as e:
# Deleted:        error_type = type(e).__name__
# Deleted:        logger.debug(f"[Weather] wthrcdn 预警请求失败: {error_type}: {e}")
# Deleted:        return None


async def get_air_quality(city: str, api_key: str, api_host: str) -> Optional[AirQuality]:
    if not api_key or not api_host:
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        city_id = await _get_city_id(client, city, api_key, api_host)
        if not city_id:
            return None
        try:
            resp = await client.get(
                f"https://{api_host}/v7/air/now",
                params={"location": city_id, "key": api_key, "lang": "zh"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        if data.get("code") != "200":
            return None
        now = data.get("now", {})
        return AirQuality(
            aqi=now.get("aqi", "--"),
            level=now.get("category", "未知"),
            pm25=now.get("pm2p5", "--"),
            pm10=now.get("pm10", "--"),
            o3=now.get("o3", "--"),
            co=now.get("co", "--"),
            no2=now.get("no2", "--"),
            so2=now.get("so2", "--"),
        )


async def _get_city_id(client: httpx.AsyncClient, city: str, api_key: str, api_host: str) -> Optional[str]:
    try:
        resp = await client.get(
            f"https://{api_host}/geo/v2/city/lookup",
            params={"location": city, "key": api_key, "lang": "zh"}
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if data.get("code") != "200" or not data.get("location"):
        return None
    return data["location"][0]["id"]
    try:
        resp = await client.get(
            f"https://{api_host}/geo/v2/city/lookup",
            params={"location": city, "key": api_key, "lang": "zh"}
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if data.get("code") != "200" or not data.get("location"):
        return None
    return data["location"][0]["id"]