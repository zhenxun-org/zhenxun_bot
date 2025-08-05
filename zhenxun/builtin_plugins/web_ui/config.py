import sys

from fastapi.middleware.cors import CORSMiddleware
import nonebot

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from strenum import StrEnum


app = nonebot.get_app()

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


AVA_URL = "http://q1.qlogo.cn/g?b=qq&nk={}&s=160"

GROUP_AVA_URL = "http://p.qlogo.cn/gh/{}/{}/640/"


class QueryDateType(StrEnum):
    """
    查询日期类型
    """

    DAY = "day"
    """日"""
    WEEK = "week"
    """周"""
    MONTH = "month"
    """月"""
    YEAR = "year"
    """年"""
