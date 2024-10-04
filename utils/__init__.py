"""
Utils package for common functionality across projects.
Provides database utilities and other shared resources.
"""

# Version information
__version__ = '0.1.0'

# Provide convenient direct imports from the top level
from utils.db_connection import PostgreSQLDatabase

# You could also define package-level configuration
default_config = {
    'db_timeout': 30,
    'max_retries': 3
}

#Initialize logging for the package
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())  # Prevent "No handler found" warnings

# Optional: Define what symbols to export
__all__ = [
    'PostgreSQLDatabase',
    'default_config',
]