from __future__ import annotations

from .models import ShopInfo

__all__ = ["ShopInfo", "ShopsRepository"]


def __getattr__(name: str):
    if name == "ShopsRepository":
        from .repository import ShopsRepository as _Repo

        return _Repo
    raise AttributeError(name)
