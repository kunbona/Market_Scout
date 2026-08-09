"""
API模块

提供各种数据API的封装和调用功能。
"""

from .index import fetch_all_data, list_available_apis

__all__ = ['fetch_all_data', 'list_available_apis']

