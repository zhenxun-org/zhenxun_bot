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
