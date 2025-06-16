from tortoise import Tortoise


async def test_db_connection(db_url: str) -> bool | str:
    try:
        # 初始化 Tortoise ORM
        await Tortoise.init(
            db_url=db_url,
            modules={"models": ["__main__"]},  # 这里不需要实际模型
        )
        # 测试连接
        await Tortoise.get_connection("default").execute_query("SELECT 1")
        return True
    except Exception as e:
        return str(e)
    finally:
        # 关闭连接
        await Tortoise.close_connections()
