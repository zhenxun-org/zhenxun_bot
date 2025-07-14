
---

# 🚀 Zhenxun LLM 服务模块

本模块是一个功能强大、高度可扩展的统一大语言模型（LLM）服务框架。它旨在将各种不同的 LLM 提供商（如 OpenAI、Gemini、智谱AI等）的 API 封装在一个统一、易于使用的接口之后，让开发者可以无缝切换和使用不同的模型，同时支持多模态输入、工具调用、智能重试和缓存等高级功能。

## 目录

- [🚀 Zhenxun LLM 服务模块](#-zhenxun-llm-服务模块)
  - [目录](#目录)
  - [✨ 核心特性](#-核心特性)
  - [🧠 核心概念](#-核心概念)
  - [🛠️ 安装与配置](#️-安装与配置)
    - [服务提供商配置 (`config.yaml`)](#服务提供商配置-configyaml)
    - [MCP 工具配置 (`mcp_tools.json`)](#mcp-工具配置-mcp_toolsjson)
  - [📘 使用指南](#-使用指南)
    - [**等级1: 便捷函数** - 最快速的调用方式](#等级1-便捷函数---最快速的调用方式)
    - [**等级2: `AI` 会话类** - 管理有状态的对话](#等级2-ai-会话类---管理有状态的对话)
    - [**等级3: 直接模型控制** - `get_model_instance`](#等级3-直接模型控制---get_model_instance)
  - [🌟 功能深度剖析](#-功能深度剖析)
    - [精细化控制模型生成 (`LLMGenerationConfig` 与 `CommonOverrides`)](#精细化控制模型生成-llmgenerationconfig-与-commonoverrides)
    - [赋予模型能力：工具使用 (Function Calling)](#赋予模型能力工具使用-function-calling)
      - [1. 注册工具](#1-注册工具)
        - [函数工具注册](#函数工具注册)
        - [MCP工具注册](#mcp工具注册)
      - [2. 调用带工具的模型](#2-调用带工具的模型)
    - [处理多模态输入](#处理多模态输入)
  - [🔧 高级主题与扩展](#-高级主题与扩展)
    - [模型与密钥管理](#模型与密钥管理)
    - [缓存管理](#缓存管理)
    - [错误处理 (`LLMException`)](#错误处理-llmexception)
    - [自定义适配器 (Adapter)](#自定义适配器-adapter)
  - [📚 API 快速参考](#-api-快速参考)

---

## ✨ 核心特性

-   **多提供商支持**: 内置对 OpenAI、Gemini、智谱AI 等多种 API 的适配器，并可通过通用 OpenAI 兼容适配器轻松接入更多服务。
-   **统一的 API**: 提供从简单到高级的三层 API，满足不同场景的需求，无论是快速聊天还是复杂的分析任务。
-   **强大的工具调用 (Function Calling)**: 支持标准的函数调用和实验性的 MCP (Model Context Protocol) 工具，让 LLM 能够与外部世界交互。
-   **多模态能力**: 无缝集成 `UniMessage`，轻松处理文本、图片、音频、视频等混合输入，支持多模态搜索和分析。
-   **文本嵌入向量化**: 提供统一的嵌入接口，支持语义搜索、相似度计算和文本聚类等应用。
-   **智能重试与 Key 轮询**: 内置健壮的请求重试逻辑，当 API Key 失效或达到速率限制时，能自动轮询使用备用 Key。
-   **灵活的配置系统**: 通过配置文件和代码中的 `LLMGenerationConfig`，可以精细控制模型的生成行为（如温度、最大Token等）。
-   **高性能缓存机制**: 内置模型实例缓存，减少重复初始化开销，提供缓存管理和监控功能。
-   **丰富的配置预设**: 提供 `CommonOverrides` 类，包含创意模式、精确模式、JSON输出等多种常用配置预设。
-   **可扩展的适配器架构**: 开发者可以轻松编写自己的适配器来支持新的 LLM 服务。

## 🧠 核心概念

-   **适配器 (Adapter)**: 这是连接我们统一接口和特定 LLM 提供商 API 的“翻译官”。例如，`GeminiAdapter` 知道如何将我们的标准请求格式转换为 Google Gemini API 需要的格式，并解析其响应。
-   **模型实例 (`LLMModel`)**: 这是框架中的核心操作对象，代表一个**具体配置好**的模型。例如，一个 `LLMModel` 实例可能代表使用特定 API Key、特定代理的 `Gemini/gemini-1.5-pro`。所有与模型交互的操作都通过这个类的实例进行。
-   **生成配置 (`LLMGenerationConfig`)**: 这是一个数据类，用于控制模型在生成内容时的行为，例如 `temperature` (温度)、`max_tokens` (最大输出长度)、`response_format` (响应格式) 等。
-   **工具 (Tool)**: 代表一个可以让 LLM 调用的函数。它可以是一个简单的 Python 函数，也可以是一个更复杂的、有状态的 MCP 服务。
-   **多模态内容 (`LLMContentPart`)**: 这是处理多模态输入的基础单元，一个 `LLMMessage` 可以包含多个 `LLMContentPart`，如一个文本部分和多个图片部分。

## 🛠️ 安装与配置

该模块作为 `zhenxun` 项目的一部分被集成，无需额外安装。核心配置主要涉及两个文件。

### 服务提供商配置 (`config.yaml`)

核心配置位于项目 `/data/config.yaml` 文件中的 `AI` 部分。

```yaml
# /data/configs/config.yaml
AI:
  # (可选) 全局默认模型，格式: "ProviderName/ModelName"
  default_model_name: Gemini/gemini-2.5-flash
  # (可选) 全局代理设置
  proxy: http://127.0.0.1:7890
  # (可选) 全局超时设置 (秒)
  timeout: 180
  # (可选) Gemini 的安全过滤阈值
  gemini_safety_threshold: BLOCK_MEDIUM_AND_ABOVE

  # 配置你的AI服务提供商
  PROVIDERS:
    # 示例1: Gemini
    - name: Gemini
      api_key:
        - "AIzaSy_KEY_1" # 支持多个Key，会自动轮询
        - "AIzaSy_KEY_2"
      api_base: https://generativelanguage.googleapis.com
      api_type: gemini
      models:
        - model_name: gemini-2.5-pro
        - model_name: gemini-2.5-flash
        - model_name: gemini-2.0-flash
        - model_name: embedding-001
          is_embedding_model: true  # 标记为嵌入模型
          max_input_tokens: 2048    # 嵌入模型特有配置

    # 示例2: 智谱AI
    - name: GLM
      api_key: "YOUR_ZHIPU_API_KEY"
      api_type: zhipu # 适配器类型
      models:
        - model_name: glm-4-flash
        - model_name: glm-4-plus
          temperature: 0.8 # 可以为特定模型设置默认温度

    # 示例3: 一个兼容OpenAI的自定义服务
    - name: MyOpenAIService
      api_key: "sk-my-custom-key"
      api_base: "http://localhost:8080/v1"
      api_type: general_openai_compat # 使用通用OpenAI兼容适配器
      models:
        - model_name: Llama3-8B-Instruct
          max_tokens: 2048 # 可以为特定模型设置默认最大Token
```

### MCP 工具配置 (`mcp_tools.json`)

此文件位于 `/data/llm/mcp_tools.json`，用于配置通过 MCP 协议启动的外部工具服务。如果文件不存在，系统会自动创建一个包含示例的默认文件。

```json
{
  "mcpServers": {
    "baidu-map": {
      "command": "npx",
      "args": ["-y", "@baidumap/mcp-server-baidu-map"],
      "env": {
        "BAIDU_MAP_API_KEY": "<YOUR_BAIDU_MAP_API_KEY>"
      },
      "description": "百度地图工具，提供地理编码、路线规划等功能。"
    },
    "sequential-thinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
      "description": "顺序思维工具，用于帮助模型进行多步骤推理。"
    }
  }
}
```

## 📘 使用指南

我们提供了三层 API，以满足从简单到复杂的各种需求。

### **等级1: 便捷函数** - 最快速的调用方式

这些函数位于 `zhenxun.services.llm` 包的顶层，为你处理了所有的底层细节。

```python
from zhenxun.services.llm import chat, search, code, pipeline_chat, embed, analyze_multimodal, search_multimodal
from zhenxun.services.llm.utils import create_multimodal_message

# 1. 纯文本聊天
response_text = await chat("你好，请用苏轼的风格写一首关于月亮的诗。")
print(response_text)

# 2. 带网络搜索的问答
search_result = await search("马斯克的Neuralink公司最近有什么新进展？")
print(search_result['text'])
# print(search_result['sources']) # 查看信息来源

# 3. 执行代码
code_result = await code("用Python画一个心形图案。")
print(code_result['text']) # 包含代码和解释的回复

# 4. 链式调用
image_msg = create_multimodal_message(images="path/to/cat.jpg")
final_poem = await pipeline_chat(
    message=image_msg,
    model_chain=["Gemini/gemini-1.5-pro", "GLM/glm-4-flash"],
    initial_instruction="详细描述这只猫的外观和姿态。",
    final_instruction="将上述描述凝练成一首可爱的短诗。"
)
print(final_poem.text)

# 5. 文本嵌入向量生成
texts_to_embed = ["今天天气真好", "我喜欢打篮球", "这部电影很感人"]
vectors = await embed(texts_to_embed, model="Gemini/embedding-001")
print(f"生成了 {len(vectors)} 个向量，每个向量维度: {len(vectors[0])}")

# 6. 多模态分析便捷函数
response = await analyze_multimodal(
    text="请分析这张图片中的内容",
    images="path/to/image.jpg",
    model="Gemini/gemini-1.5-pro"
)
print(response)

# 7. 多模态搜索便捷函数
search_result = await search_multimodal(
    text="搜索与这张图片相关的信息",
    images="path/to/image.jpg",
    model="Gemini/gemini-1.5-pro"
)
print(search_result['text'])
```

### **等级2: `AI` 会话类** - 管理有状态的对话

当你需要进行有上下文的、连续的对话时，`AI` 类是你的最佳选择。

```python
from zhenxun.services.llm import AI, AIConfig

# 初始化一个AI会话，可以传入自定义配置
ai_config = AIConfig(model="GLM/glm-4-flash", temperature=0.7)
ai_session = AI(config=ai_config)

# 更完整的AIConfig配置示例
advanced_config = AIConfig(
    model="GLM/glm-4-flash",
    default_embedding_model="Gemini/embedding-001",  # 默认嵌入模型
    temperature=0.7,
    max_tokens=2000,
    enable_cache=True,              # 启用模型缓存
    enable_code=True,               # 启用代码执行功能
    enable_search=True,             # 启用搜索功能
    timeout=180,                    # 请求超时时间（秒）
    # Gemini特定配置选项
    enable_gemini_json_mode=True,   # 启用Gemini JSON模式
    enable_gemini_thinking=True,    # 启用Gemini 思考模式
    enable_gemini_safe_mode=True,   # 启用Gemini 安全模式
    enable_gemini_multimodal=True,  # 启用Gemini 多模态优化
    enable_gemini_grounding=True,   # 启用Gemini 信息来源关联
)
advanced_session = AI(config=advanced_config)

# 进行连续对话
await ai_session.chat("我最喜欢的城市是成都。")
response = await ai_session.chat("它有什么好吃的？") # AI会知道“它”指的是成都
print(response)

# 在同一个会话中，临时切换模型进行一次调用
response_gemini = await ai_session.chat(
    "从AI的角度分析一下成都的科技发展潜力。",
    model="Gemini/gemini-1.5-pro"
)
print(response_gemini)

# 清空历史，开始新一轮对话
ai_session.clear_history()
```

### **等级3: 直接模型控制** - `get_model_instance`

这是最底层的 API，为你提供对模型实例的完全控制。推荐使用 `async with` 语句来优雅地管理模型实例的生命周期。

```python
from zhenxun.services.llm import get_model_instance, LLMMessage
from zhenxun.services.llm.config import LLMGenerationConfig

# 1. 获取模型实例
# get_model_instance 返回一个异步上下文管理器
async with await get_model_instance("Gemini/gemini-1.5-pro") as model:
    # 2. 准备消息列表
    messages = [
        LLMMessage.system("你是一个专业的营养师。"),
        LLMMessage.user("我今天吃了汉堡和可乐，请给我一些健康建议。")
    ]
    
    # 3. (可选) 定义本次调用的生成配置
    gen_config = LLMGenerationConfig(
        temperature=0.2, # 更严谨的回复
        max_tokens=300
    )
    
    # 4. 生成响应
    response = await model.generate_response(messages, config=gen_config)
    
    # 5. 处理响应
    print(response.text)
    if response.usage_info:
        print(f"Token 消耗: {response.usage_info['total_tokens']}")
```

## 🌟 功能深度剖析

### 精细化控制模型生成 (`LLMGenerationConfig` 与 `CommonOverrides`)

-   **`LLMGenerationConfig`**: 一个 Pydantic 模型，用于覆盖模型的默认生成参数。
-   **`CommonOverrides`**: 一个包含多种常用配置预设的类，如 `creative()`, `precise()`, `gemini_json()` 等，能极大地简化配置过程。

```python
from zhenxun.services.llm.config import LLMGenerationConfig, CommonOverrides

# LLMGenerationConfig 完整参数示例
comprehensive_config = LLMGenerationConfig(
    temperature=0.7,              # 生成温度 (0.0-2.0)
    max_tokens=1000,              # 最大输出token数
    top_p=0.9,                    # 核采样参数 (0.0-1.0)
    top_k=40,                     # Top-K采样参数
    frequency_penalty=0.0,        # 频率惩罚 (-2.0-2.0)
    presence_penalty=0.0,         # 存在惩罚 (-2.0-2.0)
    repetition_penalty=1.0,       # 重复惩罚 (0.0-2.0)
    stop=["END", "\n\n"],         # 停止序列
    response_format={"type": "json_object"},  # 响应格式
    response_mime_type="application/json",    # Gemini专用MIME类型
    response_schema={...},        # JSON响应模式
    thinking_budget=0.8,          # Gemini思考预算 (0.0-1.0)
    enable_code_execution=True,   # 启用代码执行
    safety_settings={...},        # 安全设置
    response_modalities=["TEXT"], # 响应模态类型
)

# 创建一个配置，要求模型输出JSON格式
json_config = LLMGenerationConfig(
    temperature=0.1,
    response_mime_type="application/json" # Gemini特有
)
# 对于OpenAI兼容API，可以这样做
json_config_openai = LLMGenerationConfig(
    temperature=0.1,
    response_format={"type": "json_object"}
)

# 使用框架提供的预设 - 基础预设
safe_config = CommonOverrides.gemini_safe()
creative_config = CommonOverrides.creative()
precise_config = CommonOverrides.precise()
balanced_config = CommonOverrides.balanced()

# 更多实用预设
concise_config = CommonOverrides.concise(max_tokens=50)      # 简洁模式
detailed_config = CommonOverrides.detailed(max_tokens=3000)  # 详细模式
json_config = CommonOverrides.gemini_json()                 # JSON输出模式
thinking_config = CommonOverrides.gemini_thinking(budget=0.8) # 思考模式

# Gemini特定高级预设
code_config = CommonOverrides.gemini_code_execution()        # 代码执行模式
grounding_config = CommonOverrides.gemini_grounding()        # 信息来源关联模式
multimodal_config = CommonOverrides.gemini_multimodal()     # 多模态优化模式

# 在调用时传入config对象
# await model.generate_response(messages, config=json_config)
```

### 赋予模型能力：工具使用 (Function Calling)

工具调用让 LLM 能够与外部函数、API 或服务进行交互。

#### 1. 注册工具

##### 函数工具注册

使用 `@tool_registry.function_tool` 装饰器注册一个简单的函数工具。

```python
from zhenxun.services.llm import tool_registry

@tool_registry.function_tool(
    name="query_stock_price",
    description="查询指定股票代码的当前价格。",
    parameters={
        "stock_symbol": {"type": "string", "description": "股票代码, 例如 'AAPL' 或 'GOOG'"}
    },
    required=["stock_symbol"]
)
async def query_stock_price(stock_symbol: str) -> dict:
    """一个查询股票价格的伪函数"""
    print(f"--- 正在查询 {stock_symbol} 的价格 ---")
    if stock_symbol == "AAPL":
        return {"symbol": "AAPL", "price": 175.50, "currency": "USD"}
    return {"error": "未知的股票代码"}
```

##### MCP工具注册

对于更复杂的、有状态的工具，可以使用 `@tool_registry.mcp_tool` 装饰器注册MCP工具。

```python
from contextlib import asynccontextmanager
from pydantic import BaseModel
from zhenxun.services.llm import tool_registry

# 定义工具的配置模型
class MyToolConfig(BaseModel):
    api_key: str
    endpoint: str
    timeout: int = 30

# 注册MCP工具
@tool_registry.mcp_tool(name="my-custom-tool", config_model=MyToolConfig)
@asynccontextmanager
async def my_tool_factory(config: MyToolConfig):
    """MCP工具工厂函数"""
    # 初始化工具会话
    session = MyToolSession(config)
    try:
        await session.initialize()
        yield session
    finally:
        await session.cleanup()
```

#### 2. 调用带工具的模型

在 `analyze` 或 `generate_response` 中使用 `use_tools` 参数。框架会自动处理整个调用流程。

```python
from zhenxun.services.llm import analyze
from nonebot_plugin_alconna.uniseg import UniMessage

response = await analyze(
    UniMessage("帮我查一下苹果公司的股价"),
    use_tools=["query_stock_price"]
)
print(response.text) # 输出应为 "苹果公司(AAPL)的当前股价为175.5美元。" 或类似内容
```

### 处理多模态输入

本模块通过 `UniMessage` 和 `LLMContentPart` 完美支持多模态。

-   **`create_multimodal_message`**: 推荐的、用于从代码中便捷地创建多模态消息的函数。
-   **`unimsg_to_llm_parts`**: 框架内部使用的核心转换函数，将 `UniMessage` 的各个段（文本、图片等）转换为 `LLMContentPart` 列表。

```python
from zhenxun.services.llm import analyze
from zhenxun.services.llm.utils import create_multimodal_message
from pathlib import Path

# 从本地文件创建消息
message = create_multimodal_message(
    text="请分析这张图片和这个视频。图片里是什么？视频里发生了什么？",
    images=[Path("path/to/your/image.jpg")],
    videos=[Path("path/to/your/video.mp4")]
)
response = await analyze(message, model="Gemini/gemini-1.5-pro")
print(response.text)
```

## 🔧 高级主题与扩展

### 模型与密钥管理

模块提供了一些工具函数来管理你的模型配置。

```python
from zhenxun.services.llm.manager import (
    list_available_models,
    list_embedding_models,
    set_global_default_model_name,
    get_global_default_model_name,
    get_key_usage_stats,
    reset_key_status
)

# 列出所有在config.yaml中配置的可用模型
models = list_available_models()
print([m['full_name'] for m in models])

# 列出所有可用的嵌入模型
embedding_models = list_embedding_models()
print([m['full_name'] for m in embedding_models])

# 动态设置全局默认模型
success = set_global_default_model_name("GLM/glm-4-plus")

# 获取所有Key的使用统计
stats = await get_key_usage_stats()
print(stats)

# 重置'Gemini'提供商的所有Key
await reset_key_status("Gemini")
```

### 缓存管理

模块提供了模型实例缓存功能，可以提高性能并减少重复初始化的开销。

```python
from zhenxun.services.llm import clear_model_cache, get_cache_stats

# 获取缓存统计信息
stats = get_cache_stats()
print(f"缓存大小: {stats['cache_size']}/{stats['max_cache_size']}")
print(f"缓存TTL: {stats['cache_ttl']}秒")
print(f"已缓存模型: {stats['cached_models']}")

# 清空模型缓存（在内存不足或需要强制重新初始化时使用）
clear_model_cache()
print("模型缓存已清空")
```

### 错误处理 (`LLMException`)

所有模块内的预期错误都会被包装成 `LLMException`，方便统一处理。

```python
from zhenxun.services.llm import chat, LLMException, LLMErrorCode

try:
    await chat("test", model="InvalidProvider/invalid_model")
except LLMException as e:
    print(f"捕获到LLM异常: {e}")
    print(f"错误码: {e.code}") # 例如 LLMErrorCode.MODEL_NOT_FOUND
    print(f"用户友好提示: {e.user_friendly_message}")
```

### 自定义适配器 (Adapter)

如果你想支持一个新的、非 OpenAI 兼容的 LLM 服务，可以通过实现自己的适配器来完成。

1.  **创建适配器类**: 继承 `BaseAdapter` 并实现其抽象方法。

    ```python
    # my_adapters/custom_adapter.py
    from zhenxun.services.llm.adapters import BaseAdapter, RequestData, ResponseData

    class MyCustomAdapter(BaseAdapter):
        @property
        def api_type(self) -> str: return "my_custom_api"
    
        @property
        def supported_api_types(self) -> list[str]: return ["my_custom_api"]
        # ... 实现 prepare_advanced_request, parse_response 等方法
    ```

2.  **注册适配器**: 在你的插件初始化代码中注册你的适配器。

    ```python
    from zhenxun.services.llm.adapters import register_adapter
    from .my_adapters.custom_adapter import MyCustomAdapter
    
    register_adapter(MyCustomAdapter())
    ```

3.  **在 `config.yaml` 中使用**:

    ```yaml
    AI:
      PROVIDERS:
        - name: MyAwesomeLLM
          api_key: "my-secret-key"
          api_type: "my_custom_api" # 关键！使用你注册的 api_type
          # ...
    ```

## 📚 API 快速参考

| 类/函数                               | 主要用途                                                               | 推荐场景                                                     |
| ------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------ |
| `llm.chat()`                          | 进行简单的、无状态的文本对话。                                         | 快速实现单轮问答。                                           |
| `llm.search()`                        | 执行带网络搜索的问答。                                                 | 需要最新信息或回答事实性问题时。                             |
| `llm.code()`                          | 请求模型执行代码。                                                     | 计算、数据处理、代码生成等。                                 |
| `llm.pipeline_chat()`                 | 将多个模型串联，处理复杂任务流。                                       | 需要多模型协作完成的任务，如“图生文再润色”。                 |
| `llm.analyze()`                       | 处理复杂的多模态输入 (`UniMessage`) 和工具调用。                       | 插件中处理用户命令，需要解析图片、at、回复等复杂消息时。   |
| `llm.AI` (类)                         | 管理一个有状态的、连续的对话会话。                                     | 需要实现上下文关联的连续对话机器人。                         |
| `llm.get_model_instance()`            | 获取一个底层的、可直接控制的 `LLMModel` 实例。                         | 需要对模型进行最精细控制的复杂或自定义场景。               |
| `llm.config.LLMGenerationConfig` (类) | 定义模型生成的具体参数，如温度、最大Token等。                         | 当需要微调模型输出风格或格式时。                             |
| `llm.tools.tool_registry` (实例)      | 注册和管理可供LLM调用的函数工具。                                      | 当你想让LLM拥有与外部世界交互的能力时。                      |
| `llm.embed()`                         | 生成文本的嵌入向量表示。                                               | 语义搜索、相似度计算、文本聚类等。                           |
| `llm.search_multimodal()`             | 执行带网络搜索的多模态问答。                                           | 需要基于图片、视频等多模态内容进行搜索时。                   |
| `llm.analyze_multimodal()`            | 便捷的多模态分析函数。                                                 | 直接分析文本、图片、视频、音频等多模态内容。                 |
| `llm.AIConfig` (类)                   | AI会话的配置类，包含模型、温度等参数。                                 | 配置AI会话的行为和特性。                                     |
| `llm.clear_model_cache()`             | 清空模型实例缓存。                                                     | 内存管理或强制重新初始化模型时。                             |
| `llm.get_cache_stats()`               | 获取模型缓存的统计信息。                                               | 监控缓存使用情况和性能优化。                                 |
| `llm.list_embedding_models()`         | 列出所有可用的嵌入模型。                                               | 选择合适的嵌入模型进行向量化任务。                           |
| `llm.config.CommonOverrides` (类)     | 提供常用的配置预设，如创意模式、精确模式等。                           | 快速应用常见的模型配置组合。                                 |
| `llm.utils.create_multimodal_message` | 便捷地从文本、图片、音视频等数据创建 `UniMessage`。                    | 在代码中以编程方式构建多模态输入时。                         |