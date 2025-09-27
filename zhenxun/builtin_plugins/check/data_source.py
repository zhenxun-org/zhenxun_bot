import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import subprocess

import cpuinfo
import nonebot
from nonebot.utils import run_sync
import psutil
from pydantic import BaseModel

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

BAIDU_URL = "https://www.baidu.com/"
GOOGLE_URL = "https://www.google.com/"

VERSION_FILE = Path() / "__version__"


def get_arm_cpu_freq_safe():
    """获取ARM设备CPU频率"""
    # 方法1: 优先从系统频率文件读取
    freq_files = [
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq",
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq",
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq",
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",
    ]

    for freq_file in freq_files:
        try:
            with open(freq_file) as f:
                frequency = int(f.read().strip())
                return round(frequency / 1000000, 2)  # 转换为GHz
        except (OSError, ValueError):
            continue

    # 方法2: 解析/proc/cpuinfo
    with contextlib.suppress(OSError, FileNotFoundError, ValueError, PermissionError):
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "CPU MHz" in line:
                    freq = float(line.split(":")[1].strip())
                    return round(freq / 1000, 2)  # 转换为GHz
    # 方法3: 使用lscpu命令
    with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, env=env, timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "CPU max MHz" in line or "CPU MHz" in line:
                    freq = float(line.split(":")[1].strip())
                    return round(freq / 1000, 2)  # 转换为GHz
    return 0  # 如果所有方法都失败，返回0


@dataclass
class CPUInfo:
    core: int | None
    """CPU 物理核心数"""
    usage: float
    """CPU 占用百分比，取值范围(0,100]"""
    freq: float
    """CPU 的时钟速度（单位：GHz）"""

    @classmethod
    def get_cpu_info(cls):
        cpu_core = psutil.cpu_count(logical=False)
        cpu_usage = psutil.cpu_percent(interval=0.1)
        if _cpu_freq := psutil.cpu_freq():
            cpu_freq = round(_cpu_freq.current / 1000, 2)
        else:
            cpu_freq = get_arm_cpu_freq_safe()
        return CPUInfo(core=cpu_core, usage=cpu_usage, freq=cpu_freq)


@dataclass
class RAMInfo:
    """RAM 信息（单位：GB）"""

    total: float
    """RAM 总量"""
    usage: float
    """当前 RAM 占用量/GB"""

    @classmethod
    def get_ram_info(cls):
        ram_total = round(psutil.virtual_memory().total / (1024**3), 2)
        ram_usage = round(psutil.virtual_memory().used / (1024**3), 2)

        return RAMInfo(total=ram_total, usage=ram_usage)


@dataclass
class SwapMemory:
    """Swap 信息（单位：GB）"""

    total: float
    """Swap 总量"""
    usage: float
    """当前 Swap 占用量/GB"""

    @classmethod
    def get_swap_info(cls):
        swap_total = round(psutil.swap_memory().total / (1024**3), 2)
        swap_usage = round(psutil.swap_memory().used / (1024**3), 2)

        return SwapMemory(total=swap_total, usage=swap_usage)


@dataclass
class DiskInfo:
    """硬盘信息"""

    total: float
    """硬盘总量"""
    usage: float
    """当前硬盘占用量/GB"""

    @classmethod
    def get_disk_info(cls):
        disk_total = round(psutil.disk_usage("/").total / (1024**3), 2)
        disk_usage = round(psutil.disk_usage("/").used / (1024**3), 2)

        return DiskInfo(total=disk_total, usage=disk_usage)


class SystemInfo(BaseModel):
    """系统信息"""

    cpu: CPUInfo
    """CPU信息"""
    ram: RAMInfo
    """RAM信息"""
    swap: SwapMemory
    """SWAP信息"""
    disk: DiskInfo
    """DISK信息"""

    def get_system_info(self):
        return {
            "cpu_info": f"{self.cpu.usage}% - {self.cpu.freq}Ghz "
            f"[{self.cpu.core} core]",
            "cpu_process": self.cpu.usage,
            "ram_info": f"{self.ram.usage} / {self.ram.total} GB",
            "ram_process": (
                0 if self.ram.total == 0 else (self.ram.usage / self.ram.total * 100)
            ),
            "swap_info": f"{self.swap.usage} / {self.swap.total} GB",
            "swap_process": (
                0 if self.swap.total == 0 else (self.swap.usage / self.swap.total * 100)
            ),
            "disk_info": f"{self.disk.usage} / {self.disk.total} GB",
            "disk_process": (
                0 if self.disk.total == 0 else (self.disk.usage / self.disk.total * 100)
            ),
        }


@run_sync
def __build_status() -> SystemInfo:
    """获取 `CPU` `RAM` `SWAP` `DISK` 信息"""
    cpu = CPUInfo.get_cpu_info()
    ram = RAMInfo.get_ram_info()
    swap = SwapMemory.get_swap_info()
    disk = DiskInfo.get_disk_info()

    return SystemInfo(cpu=cpu, ram=ram, swap=swap, disk=disk)


async def __get_network_info():
    """网络请求"""
    baidu, google = True, True
    try:
        await AsyncHttpx.get(BAIDU_URL, timeout=5)
    except Exception as e:
        logger.warning("自检：百度无法访问...", e=e)
        baidu = False
    try:
        await AsyncHttpx.get(GOOGLE_URL, timeout=5)
    except Exception as e:
        logger.warning("自检：谷歌无法访问...", e=e)
        google = False
    return baidu, google


def __get_version() -> str | None:
    """获取版本信息"""
    if VERSION_FILE.exists():
        with open(VERSION_FILE, encoding="utf-8") as f:
            if text := f.read():
                return text.split(":")[-1]
    return None


async def get_status_info() -> dict:
    """获取信息"""
    data = await __build_status()

    system = platform.uname()
    data = data.get_system_info()
    data["brand_raw"] = cpuinfo.get_cpu_info().get("brand_raw", "Unknown")
    baidu, google = await __get_network_info()
    data["baidu"] = "#8CC265" if baidu else "red"
    data["google"] = "#8CC265" if google else "red"

    data["system"] = f"{system.system} {system.release}"
    data["version"] = __get_version()
    data["plugin_count"] = len(nonebot.get_loaded_plugins())
    data["nickname"] = BotConfig.self_nickname
    return data
