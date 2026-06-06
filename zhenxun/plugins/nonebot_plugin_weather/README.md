# 🌤️ nonebot-plugin-weather

> 基于 [Zhenxun Bot](https://github.com/HibiKier/zhenxun_bot) 的天气查询插件，**默认输出精美天气图片卡片**，支持实时天气、预报查询、定时订阅与管理员管理。

[![NoneBot2](https://img.shields.io/badge/NoneBot2-v2.0.0+-green.svg)](https://github.com/nonebot/nonebot2)
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## ✨ 功能特性

- 🖼️ **精美图片卡片** —— 实时天气与预报均输出高清 HTML 渲染图片，信息一目了然
- ☀️ **实时天气查询** —— 温度、天气状况、湿度、风向、风力、能见度 + 空气质量六宫格
- 📅 **未来预报** —— 支持 1~7 天逐日预报（白天/夜间天气 + 最高/最低温度）
- ⏰ **定时订阅推送** —— 用户可订阅每日指定时间自动接收天气卡片（私聊/群聊均支持）
- 🔧 **管理员后台** —— SUPERUSER 可查看、删除、修改任意用户的订阅
- 🌡️ **空气质量监测** —— AQI、PM2.5、PM10、O₃、CO、NO₂、SO₂ 全指标展示
- 🎨 **和风天气图标** —— 自动匹配官方 SVG 天气图标，视觉统一
- 🔌 **和风天气 API** —— 稳定可靠的数据来源

## 📦 安装

### 方式一：通过 nb-cli 安装（推荐）

```bash
nb plugin install nonebot-plugin-weather
```

### 方式二：手动安装

```bash
git clone https://github.com/aikun-China/nonebot-plugin-weather.git
```

将插件文件夹放入 Zhenxun Bot 的 `extensive_plugin` 或 `my_plugins` 目录下，重启 Bot 即可。

## ⚙️ 配置

在 Zhenxun Bot 的 `data/config.yaml` 中添加以下配置（首次加载插件后会自动生成）：

```yaml
nonebot_plugin_weather:
  API_KEY: ""                      # 和风天气 API Key（必填）
  API_HOST: ""                     # 和风天气专属 API Host（必填），例如 devapi.qweather.com
  DEFAULT_CITY: "北京"              # 默认查询城市
```

### 🔑 API Key 获取

1. 前往 [和风天气开发者平台](https://dev.qweather.com/)
2. 注册账号并创建新项目
3. 获取 Web API Key 与专属 API Host
4. 将 Key 和 Host 填入上述配置项

> 💡 **提示**：配置修改后需重启 Bot 生效。

## 🚀 使用

### 普通用户指令

| 指令 | 权限 | 说明 | 示例 |
|------|------|------|------|
| `天气 [城市名]` | 所有人 | 查询指定城市实时天气（图片卡片） | `天气 上海` |
| `天气` | 所有人 | 查询默认城市天气（图片卡片） | `天气` |
| `天气 [城市名] [天数]` | 所有人 | 查询未来 1~7 天预报（图片卡片） | `天气 北京 5` |
| `天气 订阅 [城市] [HH:MM]` | 所有人 | 订阅每日定时推送 | `天气 订阅 北京 07:30` |
| `天气 取消订阅` / `退订` | 所有人 | 取消当前订阅 | `天气 取消订阅` |
| `天气 我的订阅` / `订阅状态` | 所有人 | 查看当前订阅信息 | `天气 我的订阅` |

> 📌 **指令说明**：
> - 发送 `天气 北京` → 查询北京**实时天气**
> - 发送 `天气 北京 3` → 查询北京**未来 3 天预报**
> - 发送 `天气 3` → 查询城市名为 **"3"** 的实时天气（不会误判为预报）
> - 天数范围 **1~7**，超出范围会自动限制为 1 或 7

### 管理员指令（SUPERUSER）

| 指令 | 权限 | 说明 | 示例 |
|------|------|------|------|
| `天气管理` | SUPERUSER | 查看所有用户订阅列表 | `天气管理` |
| `天气管理 删除 [QQ号]` | SUPERUSER | 删除指定用户的订阅 | `天气管理 删除 123456789` |
| `天气管理 修改 [QQ号] [城市] [HH:MM]` | SUPERUSER | 修改指定用户的订阅 | `天气管理 修改 123456789 上海 08:00` |

### 输出效果

**实时天气卡片**包含：
- 🏙️ 城市名 + 📅 日期标签
- 🌤️ 和风天气官方 SVG 图标 + 🌡️ 实时温度
- ☁️ 天气状况（晴 / 多云 / 雨等）
- 📊 四宫格信息：💧 湿度、🍃 风向、💨 风力、👁️ 能见度
- 🌫️ 空气质量卡片：AQI 等级 + PM2.5 / PM10 / O₃ / CO / NO₂ / SO₂

**预报卡片**包含：
- 📅 逐日预报列表（星期、日期、白天/夜间天气图标、最高/最低温度）

## 📁 项目结构

```
nonebot-plugin-weather/
├── __init__.py          # 插件入口、指令处理、订阅管理、定时推送、管理员后台
├── api.py               # 和风天气 API 封装（实时 / 预报 / 空气质量 / 城市 ID 查询）
├── config.py            # 插件配置定义（Zhenxun 注册配置）
├── requirements.txt     # 依赖列表
├── .gitignore           # Git 忽略规则（已排除 __pycache__ 等）
└── LICENSE              # MIT 协议
```

> ⚠️ **注意**：请勿将 `__pycache__/` 等缓存目录提交到仓库。本项目已配置 `.gitignore` 进行排除。

## 🔧 技术细节

- **框架**：NoneBot2 + Zhenxun Bot
- **适配器**：OneBot V11（QQ）
- **HTTP 客户端**：httpx
- **数据验证**：Pydantic
- **图片渲染**：nonebot-plugin-htmlrender（HTML → 图片）
- **定时任务**：nonebot-plugin-apscheduler（每分钟检查订阅）
- **API 来源**：和风天气 (QWeather)
- **订阅持久化**：JSON 文件（`subscriptions.json`）

## 📝 依赖

```
nonebot2
nonebot-plugin-htmlrender
httpx
tortoise-orm
```

- Python >= 3.9
- Zhenxun Bot 环境

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📜 协议

本项目采用 [MIT License](LICENSE) 开源。

---

> Made with ❤️ by [aikun-China](https://github.com/aikun-China)
