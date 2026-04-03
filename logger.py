import logging
import sys
from colorama import Fore, Style

class BotLogger:
    def __init__(self, log_file="bot_log.txt", log_to_file=True):
        self._logger = logging.getLogger("KalshiBot")
        self._logger.setLevel(logging.DEBUG)
        if not self._logger.handlers:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(ch)
            if log_to_file:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
                self._logger.addHandler(fh)
    def info(self, msg): self._logger.info(msg)
    def warning(self, msg): self._logger.warning(Fore.YELLOW + "WARNING: " + str(msg) + Style.RESET_ALL)
    def error(self, msg): self._logger.error(Fore.RED + "ERROR: " + str(msg) + Style.RESET_ALL)
