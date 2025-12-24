from __future__ import annotations

from aiogram import Bot
from loguru import logger

from retailcheck.bot.utils.access import find_shop
from retailcheck.shops.repository import ShopsRepository
from retailcheck.users.repository import UsersRepository


async def collect_shop_chat_ids(
    shop_id: str,
    shops_repository: ShopsRepository | None,
    users_repository: UsersRepository | None,
) -> list[int]:
    if not shops_repository or not users_repository:
        return []
    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        return []
    usernames = {
        username.lower().lstrip("@")
        for username in (shop.employee_usernames + shop.manager_usernames)
        if username
    }
    chat_ids: list[int] = []
    for username in usernames:
        if not username:
            continue
        record = await users_repository.get_by_username(username)
        if record and record.tg_id:
            chat_ids.append(record.tg_id)
    return chat_ids


async def broadcast_to_targets(
    bot: Bot,
    text: str,
    manager_ids: list[int],
    extra_ids: list[int] | None = None,
    *,
    disable_preview: bool = False,
) -> None:
    targets = list(dict.fromkeys((extra_ids or []) + (manager_ids or [])))
    if not targets:
        logger.info("No recipients for message:\n%s", text)
        return
    for chat_id in targets:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=disable_preview,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Notify failed: chat_id=%s error=%s (%s)",
                chat_id,
                exc.__class__.__name__,
                exc,
            )
