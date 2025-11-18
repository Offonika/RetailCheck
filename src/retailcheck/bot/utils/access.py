from __future__ import annotations

from aiogram.types import User as TelegramUser
from loguru import logger

from retailcheck.shops.repository import ShopsRepository
from retailcheck.users.models import UserRecord
from retailcheck.users.repository import UsersRepository


async def find_shop(shops_repository: ShopsRepository, shop_id: str):
    shops = await shops_repository.list_active()
    for shop in shops:
        if shop.shop_id == shop_id:
            return shop
    return None


async def resolve_user_record(
    user: TelegramUser, users_repository: UsersRepository
) -> UserRecord | None:
    record: UserRecord | None = None
    if user.username:
        record = await users_repository.get_by_username(user.username)
    if not record:
        record = await users_repository.get_by_tg_id(user.id)
    return record


async def ensure_user_allowed(
    user: TelegramUser,
    shop_id: str,
    shops_repository: ShopsRepository | None,
    users_repository: UsersRepository | None,
) -> None:
    if not shops_repository or not users_repository:
        return
    if getattr(user, "is_bot", False):
        return
    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        logger.warning("Shop {} not found while checking access for user {}", shop_id, user.id)
        raise ValueError(f"Shop {shop_id} not found")
    record = await resolve_user_record(user, users_repository)
    if not record or not record.is_active:
        logger.warning(
            "User {} (@{}) not found or inactive in Users table; tg_id={}",
            user.id,
            user.username,
            user.id,
        )
        raise PermissionError("user not allowed")
    if shop.allow_anyone or record.can_work_in_shop(shop.shop_id):
        logger.debug(
            "Access granted: user {} (@{}) → shop {} (allow_anyone={}, shops={})",
            user.id,
            user.username,
            shop_id,
            shop.allow_anyone,
            record.shops,
        )
        return
    logger.warning(
        "Access denied: user {} (@{}) not in shop {}. User shops={}",
        user.id,
        user.username,
        shop_id,
        record.shops,
    )
    raise PermissionError("user not allowed")


async def user_can_access_shop(
    user: TelegramUser,
    shop_id: str,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> bool:
    try:
        await ensure_user_allowed(user, shop_id, shops_repository, users_repository)
        return True
    except Exception:  # noqa: BLE001
        return False
