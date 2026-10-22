"""Post-login redirect targets."""
from urllib.parse import urlencode

DEFAULT_TARGET = "/home"


def is_local_target(target: str) -> bool:
    return target.startswith("/") and "://" not in target


def after_login(target: str) -> str:
    if target and is_local_target(target):
        return target
    return DEFAULT_TARGET


def login_page(target: str) -> str:
    return "/login?" + urlencode({"next": target}) if target else "/login"


def service_callback(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")
