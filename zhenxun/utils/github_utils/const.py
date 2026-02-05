import re

GITHUB_REPO_URL_PATTERN = re.compile(
    r"^https://github.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(/tree/(?P<branch>[^/]+))?$"
)
"""github仓库地址正则"""

JSD_PACKAGE_API_FORMAT = (
    "https://data.jsdelivr.com/v1/packages/gh/{owner}/{repo}@{branch}"
)
"""jsdelivr包地址格式"""

GIT_API_TREES_FORMAT = (
    "https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
)
"""git api trees地址格式"""

CACHED_API_TTL = 300
"""缓存api ttl"""

RAW_CONTENT_FORMAT = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
"""raw content格式"""

GITEE_RAW_CONTENT_FORMAT = "https://gitee.com/{owner}/{repo}/raw/main/{path}"
"""gitee raw content格式"""

ARCHIVE_URL_FORMAT = "https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
"""archive url格式"""

RELEASE_ASSETS_FORMAT = (
    "https://github.com/{owner}/{repo}/releases/download/{version}/{filename}"
)
"""release assets格式"""

RELEASE_SOURCE_FORMAT = (
    "https://codeload.github.com/{owner}/{repo}/legacy.{compress}/refs/tags/{version}"
)
"""release 源码格式"""

GIT_API_COMMIT_FORMAT = "https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
"""git api commit地址格式"""

GIT_API_PROXY_COMMIT_FORMAT = (
    "https://git-api.zhenxun.org/repos/{owner}/{repo}/commits/{branch}"
)
"""git api commit地址格式 (代理)"""

ALIYUN_ORG_ID = "67a361cf556e6cdab537117a"
"""阿里云 organization id"""

ALIYUN_ENDPOINT = "devops.cn-hangzhou.aliyuncs.com"
"""阿里云 endpoint"""

ALIYUN_REGION = "cn-hangzhou"
"""阿里云区域"""

Aliyun_AccessKey_ID = "LTAI5tNmf7KaTAuhcvRobAQs"
"""阿里云AccessKey ID"""

Aliyun_Secret_AccessKey_encrypted = "NmJ3d2VNRU1MREY0T1RtRnBqMlFqdlBxN3pMUk1j"
"""阿里云 Secret Access Key """

RDC_access_token_encrypted = (
    "cHQtYXp0allnQWpub0FYZWpqZm1RWGtneHk0XzBlMmYzZTZmLWQwOWItNDE4Mi1iZWUx"
    "LTQ1ZTFkYjI0NGRlMg=="
)
"""RDC Access Token """

ALIYUN_REPO_MAPPING = {
    "zhenxun-bot-resources": "4957431",
    "zhenxun_bot_plugins_index": "4957418",
    "zhenxun_bot_plugins": "4957429",
    "zhenxun_docs": "4957426",
    "zhenxun_bot": "4957428",
}
"""阿里云仓库ID映射"""

ALIYUN_EXTERNAL_PLUGIN_GROUPS: list[str] = ["zhenxun_plugins"]
"""阿里云外部插件分组列表，用于动态查询第三方插件仓库"""
