import sys
from pathlib import Path
from loguru import logger

def setup_logger(log_level: str = "INFO", log_file: Path = Path("logs/pipeline.log")):
    """Configures Loguru logger with custom formats and file rotation."""
    logger.remove()  # Remove default handler

    # Stdout logger
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    # File logger
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_file),
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} - {message}",
        level=log_level,
    )

    return logger

# Global logger instance
setup_logger()
