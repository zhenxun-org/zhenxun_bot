"""
渲染器服务的共享配置和常量
"""

RESERVED_TEMPLATE_KEYS: set[str] = {
    "data",
    "theme",
    "theme_css",
    "extra_css",
    "required_scripts",
    "required_styles",
    "frameless",
}
