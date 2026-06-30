import logging
import os


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    return logging.getLogger(name)
