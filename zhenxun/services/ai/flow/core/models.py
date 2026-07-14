from enum import Enum

from pydantic import BaseModel, Field


class ConcurrencyPolicy(str, Enum):
    """并发执行策略枚举"""

    ALLOW = "allow"
    """允许并发：不做任何限制（适用于无状态或独立任务）"""
    REJECT = "reject"
    """拒绝新请求：当前有任务在执行时，直接丢弃新任务并提醒"""
    QUEUE = "queue"
    """排队等待：当前有任务在执行时，新任务排队等待（先进先出）"""
    INTERRUPT = "interrupt"
    """中断旧任务：新任务到达时，立即强制取消并覆盖正在执行的旧任务"""


class ConcurrencyScope(str, Enum):
    """并发作用域枚举（决定锁的粒度，解耦于会话隔离）"""

    GLOBAL = "global"
    """全局互斥：整个系统同一时间只能执行一个该任务"""
    GROUP = "group"
    """群组互斥：同一群组内串行排队（私聊退化为用户级），防止抢话刷屏"""
    USER = "user"
    """用户互斥：同一用户发起的任务串行排队（允许同群不同人并行）"""
    SESSION = "session"
    """会话互斥：跟随记忆 SessionID 进行物理锁隔离"""


class InterventionPolicy(str, Enum):
    """运行时消息干预策略枚举"""

    IGNORE = "ignore"
    """忽略干预：丢弃在任务执行期间收到的额外消息（默认）"""
    STEER = "steer"
    """动态转向：将额外消息立即注入到下一轮大模型推理历史中，影响其思考方向"""
    FOLLOW_UP = "follow_up"
    """追加执行：将额外消息放入队列，在当前大模型意图（所有工具等）执行完毕后追加推理"""


class BaseRuntimeConfig(BaseModel):
    """所有可执行实体（Agent/Team/Workflow）的通用基础运行时配置"""

    stateless: bool = Field(default=True)
    """是否使用临时会话，不持久化历史记录"""
    concurrency_policy: ConcurrencyPolicy | None = Field(default=None)
    """并发执行策略。如果未显式指定，无状态(stateless=True)默认为ALLOW，有状态(stateless=False)默认为QUEUE。"""
    concurrency_scope: ConcurrencyScope | None = Field(default=None)
    """并发作用域，决定锁的粒度。如果未显式指定，默认为 GROUP 级排队。"""
    intervention_policy: InterventionPolicy | None = Field(default=None)
    """运行时干预策略，决定在大模型执行期间接收到新消息时该如何处理数据流合并。"""
