from copy import deepcopy

from ruamel.yaml import YAML

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.configs.utils import BaseBlock, PluginCdBlock, PluginCountBlock
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.plugin_limit import PluginLimit
from zhenxun.services.log import logger
from zhenxun.utils.enum import BlockType, LimitCheckType, PluginLimitType

_yaml = YAML(pure=True)
_yaml.indent = 2
_yaml.allow_unicode = True


CD_TEST = """需要cd的功能
自定义的功能需要cd也可以在此配置
key：模块名称
cd：cd 时长（秒）
status：此限制的开关状态
check_type：'PRIVATE'/'GROUP'/'ALL'，限制私聊/群聊/全部
watch_type：监听对象，以user_id或group_id作为键来限制，'USER'：用户id，'GROUP'：群id
                                 示例：'USER':用户N秒内触发1次，'GROUP':群N秒内触发1次
result：回复的话,可以添加[at],[uname],[nickname]来对应艾特，用户群名称，昵称系统昵称
result 为 "" 或 None 时则不回复
result示例："[uname]你冲的太快了，[nickname]先生，请稍后再冲[at]"
result回复："老色批你冲的太快了，欧尼酱先生，请稍后再冲@老色批"
     用户昵称↑     昵称系统的昵称↑          艾特用户↑"""


BLOCK_TEST = """用户调用阻塞
即 当用户调用此功能还未结束时
用发送消息阻止用户重复调用此命令直到该命令结束
key：模块名称
status：此限制的开关状态
check_type：'PRIVATE'/'GROUP'/'ALL'，限制私聊/群聊/全部
watch_type：监听对象，以user_id或group_id作为键来限制，'USER'：用户id，'GROUP'：群id
                                    示例：'USER'：阻塞用户，'group'：阻塞群聊
result：回复的话，可以添加[at]，[uname]，[nickname]来对应艾特，用户群名称，昵称系统昵称
result 为 "" 或 None 时则不回复
result示例："[uname]你冲的太快了，[nickname]先生，请稍后再冲[at]"
result回复："老色批你冲的太快了，欧尼酱先生，请稍后再冲@老色批"
     用户昵称↑     昵称系统的昵称↑          艾特用户↑"""

COUNT_TEST = """命令每日次数限制
即 用户/群聊 每日可调用命令的次数 [数据内存存储，重启将会重置]
每日调用直到 00:00 刷新
key：模块名称
max_count: 每日调用上限
status：此限制的开关状态
watch_type：监听对象，以user_id或group_id作为键来限制，'USER'：用户id，'GROUP'：群id
                                     示例：'USER'：用户上限，'group'：群聊上限
result：回复的话，可以添加[at]，[uname]，[nickname]来对应艾特，用户群名称，昵称系统昵称
result 为 "" 或 None 时则不回复
result示例："[uname]你冲的太快了，[nickname]先生，请稍后再冲[at]"
result回复："老色批你冲的太快了，欧尼酱先生，请稍后再冲@老色批"
     用户昵称↑     昵称系统的昵称↑          艾特用户↑"""


class Manager:
    """
    插件命令 cd 管理器
    """

    def __init__(self):
        self.cd_file = DATA_PATH / "configs" / "plugins2cd.yaml"
        self.block_file = DATA_PATH / "configs" / "plugins2block.yaml"
        self.count_file = DATA_PATH / "configs" / "plugins2count.yaml"
        self.cd_data = {}
        self.block_data = {}
        self.count_data = {}

    def add(
        self,
        module: str,
        data: BaseBlock | PluginCdBlock | PluginCountBlock | PluginLimit,
    ):
        """添加限制"""
        if isinstance(data, PluginLimit):
            check_type = BlockType.ALL
            if LimitCheckType.GROUP == data.check_type:
                check_type = BlockType.GROUP
            elif LimitCheckType.PRIVATE == data.check_type:
                check_type = BlockType.PRIVATE
            if data.limit_type == PluginLimitType.CD:
                data = PluginCdBlock(
                    status=data.status,
                    check_type=check_type,
                    watch_type=data.watch_type,
                    result=data.result,
                    cd=data.cd,
                )
            elif data.limit_type == PluginLimitType.BLOCK:
                data = BaseBlock(
                    status=data.status,
                    check_type=check_type,
                    watch_type=data.watch_type,
                    result=data.result,
                )
            elif data.limit_type == PluginLimitType.COUNT:
                data = PluginCountBlock(
                    status=data.status,
                    watch_type=data.watch_type,
                    result=data.result,
                    max_count=data.max_count,
                )
        if isinstance(data, PluginCdBlock):
            self.cd_data[module] = data
        elif isinstance(data, PluginCountBlock):
            self.count_data[module] = data
        elif isinstance(data, BaseBlock):
            self.block_data[module] = data

    def exists(self, module: str, type: PluginLimitType):
        """是否存在"""
        if type == PluginLimitType.CD:
            return module in self.cd_data
        elif type == PluginLimitType.BLOCK:
            return module in self.block_data
        elif type == PluginLimitType.COUNT:
            return module in self.count_data

    def init(self):
        if not self.cd_file.exists():
            self.save_cd_file()
        if not self.block_file.exists():
            self.save_block_file()
        if not self.count_file.exists():
            self.save_count_file()
        self.__load_file()

    def __load_file(self):
        self.__load_block_file()
        self.__load_cd_file()
        self.__load_count_file()

    def save_file(self):
        """保存文件"""
        self.save_cd_file()
        self.save_block_file()
        self.save_count_file()

    def save_cd_file(self):
        """保存文件"""
        self._extracted_from_save_file_3("PluginCdLimit", CD_TEST, self.cd_data)

    def save_block_file(self):
        """保存文件"""
        self._extracted_from_save_file_3(
            "PluginBlockLimit", BLOCK_TEST, self.block_data
        )

    def save_count_file(self):
        """保存文件"""
        self._extracted_from_save_file_3(
            "PluginCountLimit", COUNT_TEST, self.count_data
        )

    def _extracted_from_save_file_3(self, type_: str, after: str, data: dict):
        """保存文件

        参数:
            type_: 类型参数
            after: 备注
        """
        temp_data = deepcopy(data)
        if not temp_data:
            temp_data = {
                "test": {
                    "status": False,
                    "check_type": "ALL",
                    "limit_type": "USER",
                    "result": "你冲的太快了，请稍后再冲",
                }
            }
            if type_ == "PluginCdLimit":
                temp_data["test"]["cd"] = 5
            elif type_ == "PluginCountLimit":
                temp_data["test"]["max_count"] = 5
                del temp_data["test"]["check_type"]
        else:
            for v in temp_data:
                temp_data[v] = temp_data[v].to_dict()
                if check_type := temp_data[v].get("check_type"):
                    temp_data[v]["check_type"] = str(check_type)
                if watch_type := temp_data[v].get("watch_type"):
                    temp_data[v]["watch_type"] = str(watch_type)
                if type_ == "PluginCountLimit":
                    del temp_data[v]["check_type"]
        file = self.block_file
        if type_ == "PluginCdLimit":
            file = self.cd_file
        elif type_ == "PluginCountLimit":
            file = self.count_file
        with open(file, "w", encoding="utf8") as f:
            _yaml.dump({type_: temp_data}, f)
        with open(file, encoding="utf8") as rf:
            _data = _yaml.load(rf)
        _data.yaml_set_comment_before_after_key(after=after, key=type_)
        with open(file, "w", encoding="utf8") as wf:
            _yaml.dump(_data, wf)

    def __load_cd_file(self):
        self.cd_data: dict[str, PluginCdBlock] = {}
        if self.cd_file.exists():
            with open(self.cd_file, encoding="utf8") as f:
                temp = _yaml.load(f) or {}
                if "PluginCdLimit" in temp.keys():
                    for k, v in temp["PluginCdLimit"].items():
                        if "." in k:
                            k = k.split(".")[-1]
                        self.cd_data[k] = PluginCdBlock.parse_obj(v)

    def __load_block_file(self):
        self.block_data: dict[str, BaseBlock] = {}
        if self.block_file.exists():
            with open(self.block_file, encoding="utf8") as f:
                temp = _yaml.load(f) or {}
                if "PluginBlockLimit" in temp.keys():
                    for k, v in temp["PluginBlockLimit"].items():
                        if "." in k:
                            k = k.split(".")[-1]
                        self.block_data[k] = BaseBlock.parse_obj(v)

    def __load_count_file(self):
        self.count_data: dict[str, PluginCountBlock] = {}
        if self.count_file.exists():
            with open(self.count_file, encoding="utf8") as f:
                temp = _yaml.load(f) or {}
                if "PluginCountLimit" in temp.keys():
                    for k, v in temp["PluginCountLimit"].items():
                        if "." in k:
                            k = k.split(".")[-1]
                        self.count_data[k] = PluginCountBlock.parse_obj(v)

    def __replace_data(
        self,
        db_data: PluginLimit | None,
        limit: PluginCdBlock | BaseBlock | PluginCountBlock,
    ) -> PluginLimit:
        """替换数据"""
        if not db_data:
            db_data = PluginLimit()
        db_data.status = limit.status
        check_type = LimitCheckType.ALL
        if BlockType.GROUP == limit.check_type:
            check_type = LimitCheckType.GROUP
        elif BlockType.PRIVATE == limit.check_type:
            check_type = LimitCheckType.PRIVATE
        db_data.check_type = check_type
        db_data.watch_type = limit.watch_type
        db_data.result = limit.result or ""
        return db_data

    def __set_data(
        self,
        k: str,
        db_data: PluginLimit | None,
        limit: PluginCdBlock | BaseBlock | PluginCountBlock,
        limit_type: PluginLimitType,
        module2plugin: dict[str, PluginInfo],
    ) -> tuple[PluginLimit, bool]:
        """设置数据

        参数:
            k: 模块名
            db_data: 数据库数据
            limit: 文件数据
            limit_type: 限制类型
            module2plugin: 模块:插件信息

        返回:
            tuple[PluginLimit, bool]: PluginLimit，是否创建
        """
        if not db_data:
            return (
                PluginLimit(
                    module=k,
                    module_path=module2plugin[k].module_path,
                    limit_type=limit_type,
                    plugin=module2plugin[k],
                    cd=getattr(limit, "cd", None),
                    max_count=getattr(limit, "max_count", None),
                    status=limit.status,
                    check_type=limit.check_type,
                    watch_type=limit.watch_type,
                    result=limit.result,
                ),
                True,
            )
        db_data = self.__replace_data(db_data, limit)
        if limit_type == PluginLimitType.CD:
            db_data.cd = limit.cd  # type: ignore
        if limit_type == PluginLimitType.COUNT:
            db_data.max_count = limit.max_count  # type: ignore
        return db_data, False

    def __get_file_data(self, limit_type: PluginLimitType) -> dict:
        """获取文件数据

        参数:
            limit_type: 限制类型

        返回:
            dict: 文件数据
        """
        if limit_type == PluginLimitType.CD:
            return self.cd_data
        elif limit_type == PluginLimitType.COUNT:
            return self.count_data
        else:
            return self.block_data

    def __set_db_limits(
        self,
        db_limits: list[PluginLimit],
        module2plugin: dict[str, PluginInfo],
        limit_type: PluginLimitType,
    ) -> tuple[list[PluginLimit], list[PluginLimit], list[int]]:
        """更新cd限制数据

        参数:
            db_limits: 数据库limits
            module2plugin: 模块:插件信息

        返回:
            tuple[list[PluginLimit], list[PluginLimit]]: 创建列表，更新列表
        """
        update_list = []
        create_list = []
        delete_list = []
        db_type_limits = [
            limit for limit in db_limits if limit.limit_type == limit_type
        ]
        if data := self.__get_file_data(limit_type):
            db_type_limit_modules = [
                (limit.module, limit.id) for limit in db_type_limits
            ]
            delete_list.extend(
                id for module, id in db_type_limit_modules if module not in data.keys()
            )
            for k, v in data.items():
                if not module2plugin.get(k):
                    if k != "test":
                        logger.warning(
                            f"插件模块 {k} 未加载，已过滤当前 {v._type} 限制..."
                        )
                    continue
                db_data = [limit for limit in db_type_limits if limit.module == k]
                db_data, is_create = self.__set_data(
                    k, db_data[0] if db_data else None, v, limit_type, module2plugin
                )
                if is_create:
                    create_list.append(db_data)
                else:
                    update_list.append(db_data)
        else:
            delete_list = [limit.id for limit in db_type_limits]
        return create_list, update_list, delete_list

    async def __set_all_limit(
        self,
    ) -> tuple[list[PluginLimit], list[PluginLimit], list[int]]:
        """获取所有插件限制数据

        返回:
            tuple[list[PluginLimit], list[PluginLimit]]: 创建列表，更新列表
        """
        db_limits = await PluginLimit.all()
        modules = set(
            list(self.cd_data.keys())
            + list(self.block_data.keys())
            + list(self.count_data.keys())
        )
        plugins = await PluginInfo.get_plugins(module__in=modules)
        module2plugin = {p.module: p for p in plugins}
        create_list, update_list, delete_list = self.__set_db_limits(
            db_limits, module2plugin, PluginLimitType.CD
        )
        create_list1, update_list1, delete_list1 = self.__set_db_limits(
            db_limits, module2plugin, PluginLimitType.COUNT
        )
        create_list2, update_list2, delete_list2 = self.__set_db_limits(
            db_limits, module2plugin, PluginLimitType.BLOCK
        )
        all_create = create_list + create_list1 + create_list2
        all_update = update_list + update_list1 + update_list2
        all_delete = delete_list + delete_list1 + delete_list2
        return all_create, all_update, all_delete

    async def load_to_db(self):
        """读取配置文件"""

        create_list, update_list, delete_list = await self.__set_all_limit()
        if create_list:
            await PluginLimit.bulk_create(create_list)
        if update_list:
            for limit in update_list:
                await limit.save(
                    update_fields=[
                        "status",
                        "check_type",
                        "watch_type",
                        "result",
                        "cd",
                        "max_count",
                    ]
                )
            # TODO: tortoise.exceptions.OperationalError:syntax error at or near "GROUP"
            # await PluginLimit.bulk_update(
            #     update_list,
            #     ["status", "check_type", "watch_type", "result", "cd", "max_count"],
            # )
        if delete_list:
            await PluginLimit.filter(id__in=delete_list).delete()
        cnt = await PluginLimit.filter(status=True).count()
        logger.info(f"已经加载 {cnt} 个插件限制.")


manager = Manager()
