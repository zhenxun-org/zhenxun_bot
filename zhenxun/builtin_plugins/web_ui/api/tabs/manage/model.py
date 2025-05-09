from nonebot.compat import model_dump
from pydantic import BaseModel, Field

from zhenxun.utils.enum import RequestType


class Group(BaseModel):
    """
    群组信息
    """

    group_id: str
    """群组id"""
    group_name: str
    """群组名称"""
    member_count: int
    """成员人数"""
    max_member_count: int
    """群组最大人数"""


class Task(BaseModel):
    """
    被动技能
    """

    name: str
    """被动名称"""
    zh_name: str
    """被动中文名称"""
    status: bool
    """状态"""
    is_super_block: bool
    """是否超级用户禁用"""


class Plugin(BaseModel):
    """
    插件
    """

    module: str
    """模块名"""
    plugin_name: str
    """中文名"""
    is_super_block: bool
    """是否超级用户禁用"""


class GroupResult(BaseModel):
    """
    群组返回数据
    """

    group_id: str
    """群组id"""
    group_name: str
    """群组名称"""
    ava_url: str
    """群组头像"""


class Friend(BaseModel):
    """
    好友数据
    """

    user_id: str
    """用户id"""
    nickname: str = ""
    """昵称"""
    remark: str = ""
    """备注"""
    ava_url: str = ""
    """头像url"""


class UpdateGroup(BaseModel):
    """
    更新群组信息
    """

    group_id: str
    """群号"""
    status: bool
    """状态"""
    level: int
    """群权限"""
    task: list[str]
    """被动状态"""
    close_plugins: list[str]
    """关闭插件"""


class FriendRequestResult(BaseModel):
    """
    好友/群组请求管理
    """

    bot_id: str
    """bot_id"""
    oid: int
    """排序"""
    id: str
    """id"""
    flag: str
    """flag"""
    nickname: str | None
    """昵称"""
    comment: str | None
    """备注信息"""
    ava_url: str
    """头像"""
    type: str
    """类型 private group"""


class GroupRequestResult(FriendRequestResult):
    """
    群聊邀请请求
    """

    invite_group: str
    """邀请群聊"""
    group_name: str | None
    """群聊名称"""


class ClearRequest(BaseModel):
    """
    清空请求
    """

    request_type: RequestType


class HandleRequest(BaseModel):
    """
    操作请求接收数据
    """

    bot_id: str | None = None
    """bot_id"""
    id: int
    """数据id"""


class LeaveGroup(BaseModel):
    """
    退出群聊
    """

    bot_id: str
    """bot_id"""
    group_id: str
    """群聊id"""


class DeleteFriend(BaseModel):
    """
    删除好友
    """

    bot_id: str
    """bot_id"""
    user_id: str
    """用户id"""


class ReqResult(BaseModel):
    """
    好友/群组请求列表
    """

    friend: list[FriendRequestResult] = Field(default_factory=list)
    """好友请求列表"""
    group: list[GroupRequestResult] = Field(default_factory=list)
    """群组请求列表"""


class UserDetail(BaseModel):
    """
    用户详情
    """

    user_id: str
    """用户id"""
    ava_url: str
    """头像url"""
    nickname: str
    """昵称"""
    remark: str
    """备注"""
    is_ban: bool
    """是否被ban"""
    chat_count: int
    """发言次数"""
    call_count: int
    """功能调用次数"""
    like_plugin: dict[str, int]
    """最喜爱的功能"""


class GroupDetail(BaseModel):
    """
    用户详情
    """

    group_id: str
    """群组id"""
    ava_url: str
    """头像url"""
    name: str
    """名称"""
    member_count: int
    """成员数"""
    max_member_count: int
    """最大成员数"""
    chat_count: int
    """发言次数"""
    call_count: int
    """功能调用次数"""
    like_plugin: dict[str, int]
    """最喜爱的功能"""
    level: int
    """群权限"""
    status: bool
    """状态（睡眠）"""
    close_plugins: list[Plugin]
    """关闭的插件"""
    task: list[Task]
    """被动列表"""


class MessageItem(BaseModel):
    type: str
    """消息类型"""
    msg: str
    """内容"""
    time: str
    """发送日期"""


class Message(BaseModel):
    """
    消息
    """

    object_id: str
    """主体id user_id 或 group_id"""
    user_id: str
    """用户id"""
    group_id: str | None = None
    """群组id"""
    message: list[MessageItem]
    """消息"""
    name: str
    """用户名称"""
    ava_url: str
    """用户头像"""

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)


class SendMessageParam(BaseModel):
    """
    发送消息
    """

    bot_id: str
    """bot id"""
    user_id: str | None = None
    """用户id"""
    group_id: str | None = None
    """群组id"""
    message: str
    """消息"""
