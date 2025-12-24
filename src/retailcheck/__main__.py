import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from loguru import logger
from redis.asyncio import Redis

from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.repository import AuditRepository
from retailcheck.bot.handlers import manager as manager_handlers
from retailcheck.bot.handlers import start as start_handlers
from retailcheck.bot.handlers import status as status_handlers
from retailcheck.bot.handlers import steps as steps_handlers
from retailcheck.bot.middlewares.run_service import RunServiceMiddleware
from retailcheck.bot.middlewares.shops_repo import ShopsRepositoryMiddleware
from retailcheck.bot.middlewares.template_repo import TemplateRepositoryMiddleware
from retailcheck.bot.middlewares.users_repo import UsersRepositoryMiddleware
from retailcheck.config import load_app_config
from retailcheck.export.repository import ExportRepository
from retailcheck.runs.repository import RunsRepository
from retailcheck.runs.service import RunService
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository


async def main() -> None:
    config = load_app_config()
    bot = Bot(token=config.bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    await _setup_bot_commands(bot)
    redis = Redis.from_url(config.redis.url)
    sheets_client = SheetsClient(
        spreadsheet_id=config.google.sheets_id,
        service_account_file=config.google.service_account_json,
    )
    runs_repo = RunsRepository(sheets_client)
    runsteps_repo = RunStepsRepository(sheets_client)
    template_repo = TemplateRepository(sheets_client)
    shops_repo = ShopsRepository(sheets_client)
    users_repo = UsersRepository(sheets_client)
    attachments_repo = AttachmentRepository(sheets_client)
    audit_repo = AuditRepository(sheets_client)
    export_repo = ExportRepository(sheets_client)
    run_service = RunService(
        repository=runs_repo,
        redis=redis,
        template_defaults=config.run.template_defaults,
        lock_ttl=config.run.lock_ttl_sec,
        audit_repository=audit_repo,
        run_scope=config.run.scope,
        runsteps_repository=runsteps_repo,
    )

    storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)
    dispatcher["manager_notify_chat_ids"] = config.notifications.manager_chat_ids
    run_service_mw = RunServiceMiddleware(run_service)
    shops_repo_mw = ShopsRepositoryMiddleware(shops_repo)
    users_repo_mw = UsersRepositoryMiddleware(users_repo)
    template_repo_mw = TemplateRepositoryMiddleware(
        template_repo,
        runs_repo,
        runsteps_repo,
        attachments_repo,
        audit_repo,
        export_repo,
    )

    routers = (
        start_handlers.router,
        steps_handlers.router,
        status_handlers.router,
        manager_handlers.router,
    )
    for router in routers:
        for observer in (router.message, router.callback_query):
            observer.middleware(run_service_mw)
            observer.middleware(shops_repo_mw)
            observer.middleware(template_repo_mw)
            observer.middleware(users_repo_mw)

    dispatcher.include_router(start_handlers.router)
    dispatcher.include_router(steps_handlers.router)
    dispatcher.include_router(status_handlers.router)
    dispatcher.include_router(manager_handlers.router)

    logger.info("Starting RetailCheck bot...")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await redis.close()
        await bot.session.close()


async def _setup_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="🚀 Запустить смену / получить ссылки"),
        BotCommand(command="status", description="📊 Статус смены (/status shop_id)"),
        BotCommand(command="summary", description="✅ Итог и отправка менеджеру"),
    ]
    await bot.set_my_commands(commands)


if __name__ == "__main__":
    asyncio.run(main())
