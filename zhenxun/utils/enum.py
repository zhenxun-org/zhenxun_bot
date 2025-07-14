import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from strenum import StrEnum


class PriorityLifecycleType(StrEnum):
    STARTUP = "STARTUP"
    """启动"""
    SHUTDOWN = "SHUTDOWN"
    """关闭"""


class BotSentType(StrEnum):
    GROUP = "GROUP"
    PRIVATE = "PRIVATE"


class BankHandleType(StrEnum):
    DEPOSIT = "DEPOSIT"
    """存款"""
    WITHDRAW = "WITHDRAW"
    """取款"""
    LOAN = "LOAN"
    """贷款"""
    REPAYMENT = "REPAYMENT"
    """还款"""
    INTEREST = "INTEREST"
    """利息"""


class EventLogType(StrEnum):
    GROUP_MEMBER_INCREASE = "GROUP_MEMBER_INCREASE"
    """群成员增加"""
    GROUP_MEMBER_DECREASE = "GROUP_MEMBER_DECREASE"
    """群成员减少"""
    KICK_MEMBER = "KICK_MEMBER"
    """踢出群成员"""
    KICK_BOT = "KICK_BOT"
    """踢出Bot"""
    LEAVE_MEMBER = "LEAVE_MEMBER"
    """主动退群"""


class CacheType(StrEnum):
    """
    缓存类型
    """

    PLUGINS = "GLOBAL_ALL_PLUGINS"
    """全局全部插件"""
    GROUPS = "GLOBAL_ALL_GROUPS"
    """全局全部群组"""
    USERS = "GLOBAL_ALL_USERS"
    """全部用户"""
    BAN = "GLOBAL_ALL_BAN"
    """全局ban列表"""
    BOT = "GLOBAL_BOT"
    """全局bot信息"""
    LEVEL = "GLOBAL_USER_LEVEL"
    """用户权限"""
    LIMIT = "GLOBAL_LIMIT"
    """插件限制"""


class DbLockType(StrEnum):
    """
    锁类型
    """

    CREATE = "CREATE"
    """创建"""
    DELETE = "DELETE"
    """删除"""
    UPDATE = "UPDATE"
    """更新"""
    QUERY = "QUERY"
    """查询"""
    UPSERT = "UPSERT"
    """创建或更新"""


class GoldHandle(StrEnum):
    """
    金币处理
    """

    BUY = "BUY"
    """购买"""
    GET = "GET"
    """获取"""
    PLUGIN = "PLUGIN"
    """插件花费"""


class PropHandle(StrEnum):
    """
    道具处理
    """

    BUY = "BUY"
    """购买"""
    USE = "USE"
    """使用"""


class PluginType(StrEnum):
    """
    插件类型
    """

    SUPERUSER = "SUPERUSER"
    """超级用户"""
    ADMIN = "ADMIN"
    """管理员"""
    SUPER_AND_ADMIN = "ADMIN_SUPER"
    """管理员以及超级用户"""
    NORMAL = "NORMAL"
    """普通插件"""
    DEPENDANT = "DEPENDANT"
    """依赖插件，一般为没有主动触发命令的插件，受权限控制"""
    HIDDEN = "HIDDEN"
    """隐藏插件，一般为没有主动触发命令的插件，不受权限控制，如消息统计"""
    PARENT = "PARENT"
    """父插件，仅仅标记"""


class BlockType(StrEnum):
    """
    禁用状态
    """

    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    ALL = "ALL"


class PluginLimitType(StrEnum):
    """
    插件限制类型
    """

    CD = "CD"
    COUNT = "COUNT"
    BLOCK = "BLOCK"


class LimitCheckType(StrEnum):
    """
    插件限制类型
    """

    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    ALL = "ALL"


class LimitWatchType(StrEnum):
    """
    插件限制监听对象
    """

    USER = "USER"
    GROUP = "GROUP"
    ALL = "ALL"


class RequestType(StrEnum):
    """
    请求类型
    """

    FRIEND = "FRIEND"
    """好友"""
    GROUP = "GROUP"
    """群组"""


class RequestHandleType(StrEnum):
    """
    请求处理类型
    """

    APPROVE = "APPROVE"
    """同意"""
    REFUSED = "REFUSED"
    """拒绝"""
    IGNORE = "IGNORE"
    """忽略"""
    EXPIRE = "EXPIRE"
    """过期或失效"""
