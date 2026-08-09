"""
HTTP客户端工具

提供GET和POST请求功能，支持数据获取和保存。
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import RAW_DATA_DIR


class HttpClient:
    """
    HTTP客户端工具类
    
    提供GET和POST请求功能，自动保存响应数据到本地文件。
    """
    
    def __init__(self, base_url: Optional[str] = None, timeout: int = 30, cookies: Optional[str] = None):
        """
        初始化HTTP客户端
        
        Args:
            base_url: 基础URL，如果提供则会在请求URL时自动拼接
            timeout: 请求超时时间（秒），默认30秒
            cookies: Cookie字符串，格式如 "key1=value1; key2=value2"，如果提供则会在所有请求中使用
        """
        self.base_url = base_url
        self.timeout = timeout
        
        # 创建带重试机制的session
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # 设置默认请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # 设置默认cookie（如果提供）
        if cookies:
            self.set_cookies(cookies)
    
    def set_cookies(self, cookies: str):
        """
        设置Cookie
        
        Args:
            cookies: Cookie字符串，格式如 "key1=value1; key2=value2"
        """
        # 解析cookie字符串为字典
        cookie_dict = {}
        for item in cookies.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                cookie_dict[key.strip()] = value.strip()
        
        # 更新session的cookies
        self.session.cookies.update(cookie_dict)
    
    def _get_full_url(self, url: str) -> str:
        """
        获取完整URL
        
        Args:
            url: 相对或绝对URL
            
        Returns:
            完整URL
        """
        if self.base_url and not url.startswith(('http://', 'https://')):
            return f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"
        return url
    
    def _save_response_data(self, data: Any, filename: str) -> Path:
        """
        保存响应数据到文件
        
        Args:
            data: 要保存的数据（可以是dict、list或字符串）
            filename: 文件名（不需要路径，只需要文件名）
            
        Returns:
            保存的文件路径
        """
        # 确保raw_data目录存在
        raw_data_dir = Path(RAW_DATA_DIR)
        raw_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建完整文件路径
        file_path = raw_data_dir / filename
        
        # 如果数据是dict或list，保存为JSON格式
        if isinstance(data, (dict, list)):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            # 其他类型保存为文本
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(str(data))
        
        return file_path
    
    def get(self, 
            url: str, 
            params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            cookies: Optional[str] = None,
            save_to_file: bool = True,
            filename: Optional[str] = None) -> Dict[str, Any]:
        """
        发送GET请求
        
        Args:
            url: 请求URL
            params: URL查询参数
            headers: 额外的请求头
            cookies: Cookie字符串，格式如 "key1=value1; key2=value2"，如果提供则覆盖默认cookie
            save_to_file: 是否保存响应数据到文件，默认True
            filename: 保存的文件名，如果为None则自动生成
            
        Returns:
            包含响应信息的字典:
            {
                'status_code': 状态码,
                'data': 响应数据（JSON解析后的数据或文本）,
                'headers': 响应头,
                'file_path': 保存的文件路径（如果保存了）
            }
        """
        full_url = self._get_full_url(url)
        
        # 合并请求头
        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)
        
        # 处理cookie
        request_cookies = None
        if cookies:
            # 解析cookie字符串为字典
            cookie_dict = {}
            for item in cookies.split(';'):
                item = item.strip()
                if '=' in item:
                    key, value = item.split('=', 1)
                    cookie_dict[key.strip()] = value.strip()
            request_cookies = cookie_dict
        
        try:
            response = self.session.get(
                full_url,
                params=params,
                headers=request_headers,
                cookies=request_cookies,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            # 尝试解析JSON，失败则返回文本
            try:
                data = response.json()
            except json.JSONDecodeError:
                data = response.text
            
            # 保存数据
            file_path = None
            if save_to_file:
                if filename is None:
                    # 自动生成文件名：基于URL和当前时间戳
                    url_part = url.split('/')[-1].split('?')[0] or 'response'
                    timestamp = int(time.time())
                    filename = f"{url_part}_{timestamp}.json" if isinstance(data, (dict, list)) else f"{url_part}_{timestamp}.txt"
                
                file_path = self._save_response_data(data, filename)
            
            return {
                'status_code': response.status_code,
                'data': data,
                'headers': dict(response.headers),
                'file_path': str(file_path) if file_path else None
            }
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"GET请求失败: {str(e)}")
    
    def post(self,
             url: str,
             data: Optional[Dict[str, Any]] = None,
             json_data: Optional[Dict[str, Any]] = None,
             headers: Optional[Dict[str, str]] = None,
             cookies: Optional[str] = None,
             save_to_file: bool = True,
             filename: Optional[str] = None) -> Dict[str, Any]:
        """
        发送POST请求
        
        Args:
            url: 请求URL
            data: 表单数据（会作为application/x-www-form-urlencoded发送）
            json_data: JSON数据（会作为application/json发送），优先级高于data
            headers: 额外的请求头
            cookies: Cookie字符串，格式如 "key1=value1; key2=value2"，如果提供则覆盖默认cookie
            save_to_file: 是否保存响应数据到文件，默认True
            filename: 保存的文件名，如果为None则自动生成
            
        Returns:
            包含响应信息的字典:
            {
                'status_code': 状态码,
                'data': 响应数据（JSON解析后的数据或文本）,
                'headers': 响应头,
                'file_path': 保存的文件路径（如果保存了）
            }
        """
        full_url = self._get_full_url(url)
        
        # 合并请求头
        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)
        
        # 处理cookie
        request_cookies = None
        if cookies:
            # 解析cookie字符串为字典
            cookie_dict = {}
            for item in cookies.split(';'):
                item = item.strip()
                if '=' in item:
                    key, value = item.split('=', 1)
                    cookie_dict[key.strip()] = value.strip()
            request_cookies = cookie_dict
        
        try:
            # 优先使用json_data
            if json_data is not None:
                response = self.session.post(
                    full_url,
                    json=json_data,
                    headers=request_headers,
                    cookies=request_cookies,
                    timeout=self.timeout
                )
            elif data is not None:
                response = self.session.post(
                    full_url,
                    data=data,
                    headers=request_headers,
                    cookies=request_cookies,
                    timeout=self.timeout
                )
            else:
                response = self.session.post(
                    full_url,
                    headers=request_headers,
                    cookies=request_cookies,
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            
            # 尝试解析JSON，失败则返回文本
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = response.text
            
            # 保存数据
            file_path = None
            if save_to_file:
                if filename is None:
                    # 自动生成文件名：基于URL和当前时间戳
                    url_part = url.split('/')[-1].split('?')[0] or 'response'
                    timestamp = int(time.time())
                    filename = f"{url_part}_{timestamp}.json" if isinstance(response_data, (dict, list)) else f"{url_part}_{timestamp}.txt"
                
                file_path = self._save_response_data(response_data, filename)
            
            return {
                'status_code': response.status_code,
                'data': response_data,
                'headers': dict(response.headers),
                'file_path': str(file_path) if file_path else None
            }
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"POST请求失败: {str(e)}")

