import logging
import sys
import os

class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        msg = str(record.getMessage())
        sensitive_keywords = ["AUTH_TOKEN", "RAW_ASYM_BOOTSTRAP", "DECRYPTED_BOOTSTRAP_PAYLOAD", "PRIVATE_KEY_MATERIAL"]
        for keyword in sensitive_keywords:
            if keyword in msg:
                return False
        return True

def setup_logger():
    logger = logging.getLogger("SystemLog")
    if logger.hasHandlers():
        return logger
        
    logger.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter('[%(asctime)s] | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    ch.addFilter(SensitiveDataFilter())
    
    fh = logging.FileHandler("session.log", mode='a')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    fh.addFilter(SensitiveDataFilter())
    
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger
