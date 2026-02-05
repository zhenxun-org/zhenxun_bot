from datetime import datetime
from pathlib import Path
import uuid

import nonebot
from nonebot.adapters import Bot
from nonebot.drivers import Driver
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError, OperationalError
import ujson as json

from zhenxun.models.bot_connect_log import BotConnectLog
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.goods_info import GoodsInfo
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.sign_user import SignUser
from zhenxun.models.user_console import UserConsole
from zhenxun.services.log import logger
from zhenxun.utils.decorator.shop import shop_register
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.manager.zhenxun_repo_manager import ZhenxunRepoManager
from zhenxun.utils.platform import PlatformUtils

driver: Driver = nonebot.get_driver()


@driver.on_bot_connect
async def _(bot: Bot):
    logger.debug(f"Bot: {bot.self_id} 建立连接...")
    await BotConnectLog.create(
        bot_id=bot.self_id, platform=bot.adapter, connect_time=datetime.now(), type=1
    )
    if not await BotConsole.exists(bot_id=bot.self_id):
        try:
            await BotConsole.create(
                bot_id=bot.self_id, platform=PlatformUtils.get_platform(bot)
            )
        except IntegrityError as e:
            logger.warning(f"记录bot: {bot.self_id} 数据已存在...", e=e)


@driver.on_bot_disconnect
async def _(bot: Bot):
    logger.debug(f"Bot: {bot.self_id} 断开连接...")
    try:
        await BotConnectLog.create(
            bot_id=bot.self_id,
            platform=bot.adapter,
            connect_time=datetime.now(),
            type=0,
        )
    except Exception as e:
        logger.warning(f"记录bot: {bot.self_id} 断开连接失败", e=e)


SIGN_SQL = """
SELECT user_id, checkin_count, add_probability, specify_probability, impression
FROM (
    SELECT
        t1.user_id,
        t1.checkin_count,
        t1.add_probability,
        t1.specify_probability,
        t1.impression,
        ROW_NUMBER() OVER(PARTITION BY t1.user_id ORDER BY t1.impression DESC) AS rn
    FROM sign_group_users t1
    INNER JOIN (
        SELECT user_id, MAX(impression) AS max_impression
        FROM sign_group_users
        GROUP BY user_id
    ) t2 ON t2.user_id = t1.user_id AND t2.max_impression = t1.impression
) t
WHERE rn = 1
"""

BAG_SQL = """
select t1.user_id, t1.gold, t1.property
from bag_users t1
  join (
    select user_id, max(t2.gold) as max_gold
    from bag_users t2
    group by user_id
  ) t on t.user_id = t1.user_id and t.max_gold = t1.gold
"""


@PriorityLifecycle.on_startup(priority=5)
async def _():
    try:
        should_update = False
        resource_path = ZhenxunRepoManager.config.RESOURCE_PATH
        default_theme_path = resource_path / "themes" / "default"
        version_file = resource_path / "__version__"

        if (
            not ZhenxunRepoManager.check_resources_exists()
            or not default_theme_path.exists()
            or not version_file.exists()
        ):
            should_update = True
            logger.info(
                "检测到资源文件(字体/主题/版本信息)缺失，准备进行初始化下载...",
                "资源检查",
            )
        else:
            spec_file = Path("resources.spec")
            req_ver_str = ">=0.0.0"
            if spec_file.exists():
                try:
                    for line in spec_file.read_text("utf-8").splitlines():
                        if line.strip().startswith("require_resources_version:"):
                            req_ver_str = line.split(":", 1)[1].strip().strip("'\"")
                            break
                except Exception:
                    pass

            local_ver_str = "0.0.0"
            try:
                content = version_file.read_text("utf-8").strip()
                local_ver_str = (
                    content.split(":", 1)[1].strip() if ":" in content else content
                )
            except Exception:
                pass

            if not SpecifierSet(req_ver_str).contains(Version(local_ver_str)):
                should_update = True
                logger.info(
                    f"资源版本({local_ver_str})不满足要求({req_ver_str})，准备强制更新...",
                    "资源检查",
                )

        if should_update:
            await ZhenxunRepoManager.resources_update()
    except Exception as e:
        logger.error(f"资源检查或更新失败: {e}", "资源检查")
    """签到与用户的数据迁移"""
    if goods_list := await GoodsInfo.filter(uuid__isnull=True).all():
        for goods in goods_list:
            goods.uuid = uuid.uuid1()  # type: ignore
        await GoodsInfo.bulk_update(goods_list, ["uuid"], 10)
    await shop_register.load_register()
    if (
        not await UserConsole.annotate().count()
        and not await SignUser.annotate().count()
    ):
        try:
            group_user = []
            try:
                group_user = await GroupInfoUser.filter(uid__isnull=False).all()
            except Exception as e:
                logger.warning("获取GroupInfoUser数据uid失败...", e=e)
            user2uid = {u.user_id: u.uid for u in group_user}
            db = Tortoise.get_connection("default")
            old_sign_list = await db.execute_query_dict(SIGN_SQL)
            old_bag_list = await db.execute_query_dict(BAG_SQL)
            goods = {
                g["goods_name"]: g["uuid"]
                for g in await GoodsInfo.annotate().values("goods_name", "uuid")
            }
            create_list = []
            sign_id_list = []
            max_uid = max(user2uid.values()) + 1 if user2uid else 0
            for old_sign in old_sign_list:
                sign_id_list.append(old_sign["user_id"])
                if old_bag := [
                    b for b in old_bag_list if b["user_id"] == old_sign["user_id"]
                ]:
                    old_bag = old_bag[0]
                    property = json.loads(old_bag["property"])
                    props = {}
                    if property:
                        for name, num in property.items():
                            if name in goods:
                                props[goods[name]] = num
                    create_list.append(
                        UserConsole(
                            user_id=old_sign["user_id"],
                            platform="qq",
                            uid=user2uid.get(old_sign["user_id"]) or max_uid,
                            props=props,
                            gold=old_bag["gold"],
                        )
                    )
                    if not user2uid.get(old_sign["user_id"]):
                        max_uid += 1
                else:
                    create_list.append(
                        UserConsole(
                            user_id=old_sign["user_id"], platform="qq", uid=max_uid
                        )
                    )
                    max_uid += 1
            if create_list:
                logger.info("开始迁移用户数据...")
                await UserConsole.bulk_create(create_list, 10)
                logger.info("迁移用户数据完成!")
            create_list.clear()
            uc_dict = {u.user_id: u for u in await UserConsole.all()}
            for old_sign in old_sign_list:
                user_console = uc_dict.get(
                    old_sign["user_id"]
                ) or await UserConsole.get_user(old_sign["user_id"], "qq")
                create_list.append(
                    SignUser(
                        user_id=old_sign["user_id"],
                        user_console=user_console,
                        platform="qq",
                        sign_count=old_sign["checkin_count"],
                        impression=old_sign["impression"],
                        add_probability=old_sign["add_probability"],
                        specify_probability=old_sign["specify_probability"],
                    )
                )
            if create_list:
                logger.info("开始迁移签到数据...")
                await SignUser.bulk_create(create_list, 10)
                logger.info("迁移签到数据完成!")
        except OperationalError as e:
            logger.warning("数据迁移", e=e)
