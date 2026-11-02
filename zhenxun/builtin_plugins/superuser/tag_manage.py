from nonebot.adapters import Bot
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaMatch,
    AlconnaQuery,
    Args,
    Match,
    MultiVar,
    Option,
    Query,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_waiter import prompt_until
from tortoise.exceptions import IntegrityError

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.tags import tag_manager
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="群组标签管理",
    description="用于管理和操作群组标签",
    usage="""### 🏷️ 群组标签管理
用于创建和管理群组标签，以实现对群组的批量操作和筛选。

---

#### **✨ 核心命令**

- **`tag list`** (别名: `ls`)
    - 查看所有标签及其基本信息。

- **`tag info <标签名>`**
    - 查看指定标签的详细信息，包括关联群组或动态规则的匹配结果。

- **`tag create <标签名> [选项...]`**
    - 创建一个新标签。
    - **选项**:
        - `--type <static|dynamic>`: 标签类型，默认为 `static`。
            - `static`: 静态标签，需手动关联群组。
            - `dynamic`: 动态标签，根据规则自动匹配。
        - `-g <群号...>`: **(静态)** 初始关联的群组ID。
        - `--rule "<规则>"`: **(动态)** 定义动态规则，**规则必须用引号包裹**。
        - `--desc "<描述>"`: 为标签添加描述。
        - `--blacklist`: **(静态)** 将标签设为黑名单（排除）模式。

- **`tag edit <标签名> [操作...]`**
    - 编辑一个已存在的标签。
    - **通用操作**:
        - `--rename <新名>`: 重命名标签。
        - `--desc "<描述>"`: 更新描述。
        - `--mode <white|black>`: 切换为白名单/黑名单模式。
    - **静态标签操作**:
        - `--add <群号...>`: 添加群组。
        - `--remove <群号...>`: 移除群组。
        - `--set <群号...>`: **[覆盖]** 重新设置所有关联群组。
    - **动态标签操作**:
        - `--rule "<新规则>"`: 更新动态规则。

- **`tag delete <名1> [名2] ...`**
     - 删除一个或多个标签。

- **`tag clear`**
     - **[⚠️ 危险]** 删除所有标签，操作前会请求确认。

---

#### **🔧 动态规则速查**
规则支持 `and` 和 `or` 组合（`and` 优先）。
**包含空格或特殊字符的规则值建议用英文引号包裹**。

- `member_count > 100`
  按 **群成员数** 筛选 (`>`, `>=`, `<`, `<=`, `=`)。

- `level >= 5`
  按 **群权限等级** 筛选。

- `status = true`
  按 **群是否休眠** 筛选 (`true` / `false`)。

- `is_super = false`
  按 **群是否为白名单** 筛选 (`true` / `false`)。

- `group_name contains "模式"`
  按 **群名模糊/正则匹配**。
  例: `contains "测试.*群$"` 匹配以“测试”开头、“群”结尾的群名。

- `group_name in "群1,群2"`
  按 **群名多值精确匹配** (英文逗号分隔)。

---

#### **💡 使用示例**

##### 静态标签示例
```bash
# 创建一个名为“核心群”的静态标签，并关联两个群组
tag create 核心群 -g 12345 67890 --desc "核心业务群"

# 向“核心群”中添加一个新群组
tag edit 核心群 --add 98765

# 创建一个用于排除的黑名单标签
tag create 排除群 --blacklist -g 11111
```

##### 动态标签示例
```bash
# 创建一个动态标签，匹配所有成员数大于200的群
tag create 大群 --type dynamic --rule "member_count > 200"

# 创建一个匹配高权限且未休眠的群的标签
tag create 活跃管理群 --type dynamic --rule "level > 5 and status = true"

# 创建一个匹配群名包含“核心”或“测试”的标签
tag create 业务群 --type dynamic --rule "group_name contains 核心 or group_name contains 测试"
```
    """.strip(),  # noqa: E501
    extra=PluginExtraData(
        author="HibiKier",
        version="1.0.0",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)
tag_cmd = on_alconna(
    Alconna(
        "tag",
        Subcommand("list", alias=["ls"], help_text="查看所有标签"),
        Subcommand("info", Args["name", str], help_text="查看标签详情"),
        Subcommand(
            "create",
            Args["name", str],
            Option(
                "--rule",
                Args["rule", str],
                help_text="动态标签规则 (例如: min_members=100)",
            ),
            Option(
                "--type",
                Args["tag_type", ["static", "dynamic"]],
                help_text="标签类型 (默认: static)",
            ),
            Option(
                "--blacklist", action=store_true, help_text="设为黑名单模式(仅静态标签)"
            ),
            Option("--desc", Args["description", str], help_text="标签描述"),
            Option(
                "-g", Args["group_ids", MultiVar(str)], help_text="创建时要关联的群组ID"
            ),
        ),
        Subcommand(
            "edit",
            Args["name", str],
            Option(
                "--rule",
                Args["rule", str],
                help_text="更新动态标签规则",
            ),
            Option("--add", Args["add_groups", MultiVar(str)]),
            Option("--remove", Args["remove_groups", MultiVar(str)]),
            Option("--set", Args["set_groups", MultiVar(str)]),
            Option("--rename", Args["new_name", str]),
            Option("--desc", Args["description", str]),
            Option("--mode", Args["mode", ["black", "white"]]),
            help_text="编辑标签",
        ),
        Subcommand(
            "delete",
            Args["names", MultiVar(str)],
            alias=["del", "rm"],
            help_text="删除标签",
        ),
        Subcommand("clear", help_text="清空所有标签"),
        Subcommand("prune", alias=["check", "清理"], help_text="清理无效的群组关联"),
        Subcommand(
            "clone",
            Args["source_name", str]["new_name", str],
            Option("--add", Args["add_groups", MultiVar(str)]),
            Option("--remove", Args["remove_groups", MultiVar(str)]),
            Option("--as-dynamic", action=store_true),
            Option("--desc", Args["description", str]),
            Option("--mode", Args["mode", ["black", "white"]]),
            help_text="克隆标签",
        ),
    ),
    permission=SUPERUSER,
    priority=5,
    block=True,
)

tag_cmd.shortcut(
    "清理标签",
    command="tag",
    arguments=["prune"],
    prefix=True,
)


@tag_cmd.assign("list")
async def handle_list():
    tags = await tag_manager.list_tags_with_counts()
    if not tags:
        await MessageUtils.build_message("当前没有已创建的标签。").finish()

    msg = "已创建的群组标签:\n"
    for tag in tags:
        mode = "黑名单(排除)" if tag["is_blacklist"] else "白名单(包含)"
        tag_type = "动态" if tag["tag_type"] == "DYNAMIC" else "静态"
        count_desc = (
            f"含 {tag['group_count']} 个群组" if tag_type == "静态" else "动态计算"
        )
        msg += f"- {tag['name']} (类型: {tag_type}, 模式: {mode}): {count_desc}\n"
    await MessageUtils.build_message(msg).finish()


@tag_cmd.assign("info")
async def handle_info(name: Match[str], bot: Bot):
    details = await tag_manager.get_tag_details(name.result, bot=bot)
    if not details:
        await MessageUtils.build_message(f"标签 '{name.result}' 不存在。").finish()

    mode = "黑名单(排除)" if details["is_blacklist"] else "白名单(包含)"
    tag_type_str = "动态" if details["tag_type"] == "DYNAMIC" else "静态"
    msg = f"标签详情: {details['name']}\n"
    msg += f"类型: {tag_type_str}\n"
    msg += f"模式: {mode}\n"
    msg += f"描述: {details['description'] or '无'}\n"

    if details["tag_type"] == "STATIC" and details["is_blacklist"]:
        msg += f"排除群组 ({len(details['groups'])}个):\n"
        if details["groups"]:
            msg += "\n".join(f"- {gid}" for gid in details["groups"])
        else:
            msg += "无"
        msg += "\n\n"

    if details["tag_type"] == "DYNAMIC" and details.get("dynamic_rule"):
        msg += f"动态规则: {details['dynamic_rule']}\n"

    title = (
        "当前生效群组"
        if details["tag_type"] == "DYNAMIC" or details["is_blacklist"]
        else "关联群组"
    )

    if details["resolved_groups"] is not None:
        msg += f"{title} ({len(details['resolved_groups'])}个):\n"
        if details["resolved_groups"]:
            msg += "\n".join(
                f"- {g_name} ({g_id})" for g_id, g_name in details["resolved_groups"]
            )
        else:
            msg += "无"
    else:
        msg += f"关联群组 ({len(details['groups'])}个):\n"
        if details["groups"]:
            msg += "\n".join(f"- {gid}" for gid in details["groups"])
        else:
            msg += "无"

    await MessageUtils.build_message(msg).finish()


@tag_cmd.assign("create")
async def handle_create(
    name: Match[str],
    description: Match[str],
    group_ids: Match[list[str]],
    rule: Match[str] = AlconnaMatch("rule"),
    tag_type: Match[str] = AlconnaMatch("tag_type"),
    blacklist: Query[bool] = AlconnaQuery("create.blacklist.value", False),
):
    ttype = (
        tag_type.result.upper()
        if tag_type.available
        else ("DYNAMIC" if rule.available else "STATIC")
    )

    if ttype == "DYNAMIC" and not rule.available:
        await MessageUtils.build_message(
            "创建失败: 动态标签必须提供至少一个规则。"
        ).finish()

    try:
        gids_to_create = None
        unique_gids_count = 0
        if group_ids.available:
            unique_gids = list(dict.fromkeys(group_ids.result))
            gids_to_create = unique_gids
            unique_gids_count = len(unique_gids)

        tag = await tag_manager.create_tag(
            name=name.result,
            is_blacklist=blacklist.result,
            description=description.result if description.available else None,
            group_ids=gids_to_create,
            tag_type=ttype,
            dynamic_rule=rule.result if rule.available else None,
        )
        msg = f"标签 '{tag.name}' 创建成功！"
        if group_ids.available:
            msg += f"\n已同时关联 {unique_gids_count} 个群组。"
        await MessageUtils.build_message(msg).finish()
    except IntegrityError:
        await MessageUtils.build_message(
            f"创建失败: 标签 '{name.result}' 已存在。"
        ).finish()
    except ValueError as e:
        await MessageUtils.build_message(f"创建失败: {e}").finish()


@tag_cmd.assign("edit")
async def handle_edit(
    name: Match[str],
    add_groups: Match[list[str]],
    remove_groups: Match[list[str]],
    set_groups: Match[list[str]],
    new_name: Match[str],
    description: Match[str],
    mode: Match[str],
    rule: Match[str] = AlconnaMatch("rule"),
):
    tag_name = name.result
    tag_details = await tag_manager.get_tag_details(tag_name)
    if not tag_details:
        await MessageUtils.build_message(f"标签 '{tag_name}' 不存在。").finish()

    group_actions = [
        add_groups.available,
        remove_groups.available,
        set_groups.available,
    ]
    if sum(group_actions) > 1:
        await MessageUtils.build_message(
            "`--add`, `--remove`, `--set` 选项不能同时使用。"
        ).finish()

    is_dynamic = tag_details.get("tag_type") == "DYNAMIC"

    if is_dynamic and any(group_actions):
        await MessageUtils.build_message(
            "编辑失败: 不能对动态标签执行 --add, --remove, 或 --set 操作。"
        ).finish()

    if not is_dynamic and rule.available:
        await MessageUtils.build_message(
            "编辑失败: 不能为静态标签设置动态规则。"
        ).finish()

    results = []
    try:
        rule_str = rule.result if rule.available else None

        if add_groups.available:
            count = await tag_manager.add_groups_to_tag(tag_name, add_groups.result)
            results.append(f"添加了 {count} 个群组。")
        if remove_groups.available:
            count = await tag_manager.remove_groups_from_tag(
                tag_name, remove_groups.result
            )
            results.append(f"移除了 {count} 个群组。")
        if set_groups.available:
            count = await tag_manager.set_groups_for_tag(tag_name, set_groups.result)
            results.append(f"关联群组已覆盖为 {count} 个。")

        if description.available or mode.available or rule_str is not None:
            is_blacklist = None
            if mode.available:
                is_blacklist = mode.result == "black"
            await tag_manager.update_tag_attributes(
                tag_name,
                description.result if description.available else None,
                is_blacklist,
                rule_str,
            )
            if rule_str is not None:
                results.append(f"动态规则已更新为 '{rule_str}'。")
            if description.available:
                results.append("描述已更新。")
            if mode.available:
                results.append(
                    f"模式已更新为 {'黑名单' if is_blacklist else '白名单'}。"
                )

        if new_name.available:
            await tag_manager.rename_tag(tag_name, new_name.result)
            results.append(f"已重命名为 '{new_name.result}'。")
            tag_name = new_name.result

    except (ValueError, IntegrityError) as e:
        await MessageUtils.build_message(f"操作失败: {e}").finish()

    if not results:
        await MessageUtils.build_message(
            "未执行任何操作，请提供至少一个编辑选项。"
        ).finish()

    final_msg = f"对标签 '{tag_name}' 的操作已完成：\n" + "\n".join(
        f"- {r}" for r in results
    )
    await MessageUtils.build_message(final_msg).finish()


@tag_cmd.assign("delete")
async def handle_delete(names: Match[list[str]]):
    success, failed = [], []
    for name in names.result:
        if await tag_manager.delete_tag(name):
            success.append(name)
        else:
            failed.append(name)
    msg = ""
    if success:
        msg += f"成功删除标签: {', '.join(success)}\n"
    if failed:
        msg += f"标签不存在，删除失败: {', '.join(failed)}"
    await MessageUtils.build_message(msg.strip()).finish()


@tag_cmd.assign("clear")
async def handle_clear():
    confirm = await prompt_until(
        "【警告】此操作将删除所有群组标签，是否继续？\n请输入 `是` 或 `确定` 确认操作",
        lambda msg: (
            msg.extract_plain_text().lower() in ["是", "确定", "yes", "confirm"]
        ),
        timeout=30,
        retry=1,
    )
    if confirm:
        count = await tag_manager.clear_all_tags()
        await MessageUtils.build_message(f"操作完成，已清空 {count} 个标签。").finish()
    else:
        await MessageUtils.build_message("操作已取消。").finish()


@tag_cmd.assign("clone")
async def handle_clone(
    bot: Bot,
    source_name: Match[str],
    new_name: Match[str],
    add_groups: Query[list[str] | None] = AlconnaQuery("clone.add.add_groups", None),
    remove_groups: Query[list[str] | None] = AlconnaQuery(
        "clone.remove.remove_groups", None
    ),
    as_dynamic: Query[bool] = AlconnaQuery("clone.as-dynamic.value", False),
    description: Query[str | None] = AlconnaQuery("clone.desc.description", None),
    mode: Query[str | None] = AlconnaQuery("clone.mode.mode", None),
):
    try:
        new_tag = await tag_manager.clone_tag(
            source_name=source_name.result,
            new_name=new_name.result,
            bot=bot,
            add_groups=add_groups.result,
            remove_groups=remove_groups.result,
            as_dynamic=as_dynamic.result,
            description=description.result,
            mode=mode.result,
        )

        tag_type_str = "动态" if new_tag.tag_type == "DYNAMIC" else "静态"
        group_count = 0
        if new_tag.tag_type == "STATIC":
            group_count = await new_tag.groups.all().count()

        msg = f"✅ 成功克隆标签!\n- 新标签: {new_tag.name}\n- 类型: {tag_type_str}"
        if new_tag.tag_type == "STATIC":
            msg += f" (含 {group_count} 个群组)"
        await MessageUtils.build_message(msg).finish()
    except (ValueError, IntegrityError) as e:
        await MessageUtils.build_message(f"克隆失败: {e}").finish()


@tag_cmd.assign("prune")
async def handle_prune():
    deleted_count = await tag_manager.prune_stale_group_links()
    msg = f"清理完成！共移除了 {deleted_count} 个无效的群组关联。"
    await MessageUtils.build_message(msg).finish()
