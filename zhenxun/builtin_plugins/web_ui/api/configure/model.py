from pydantic import BaseModel


class Setting(BaseModel):
    superusers: list[str]
    """超级用户列表"""
    db_url: str
    """数据库地址"""
    host: str
    """主机地址"""
    port: int
    """端口"""
    username: str
    """前端用户名"""
    password: str
    """前端密码"""
