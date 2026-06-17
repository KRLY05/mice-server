from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from .config import logger, TELEGRAM_BOT_TOKEN, http_client
from .handlers import (
    start_cmd, stop_cmd, help_cmd,
    button_callback, handle_photo, handle_message,
    error_handler,
)


async def post_shutdown(application):
    """Clean up shared resources on bot shutdown."""
    await http_client.aclose()


def main():
    """Start the bot."""
    logger.info("Initializing Telegram Bot...")

    request = HTTPXRequest(connect_timeout=15.0, read_timeout=15.0)
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram Bot polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
