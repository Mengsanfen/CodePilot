from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from urllib3.util import connection as urllib3_connection

MAX_REDIRECTS = 5

_pin_state = threading.local()
_install_lock = threading.Lock()
_install_count = 0
_original_create_connection = None


def _get_pin_stack() -> list[dict[str, list]]:
    """获取当前线程的 DNS pin 栈。

    这个设计借鉴 open-swe：URL 安全检查不能只在请求前解析一次 DNS，
    否则恶意域名可以在校验时返回公网 IP，在真正连接时改成内网 IP。
    """

    stack = getattr(_pin_state, "stack", None)
    if stack is None:
        stack = []
        _pin_state.stack = stack
    return stack


def _pinned_create_connection(
    address,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address=None,
    socket_options=None,
):
    """让 urllib3 连接时使用已经校验过的 DNS 结果。"""

    host, port = address
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    stack = _get_pin_stack()
    pins = stack[-1] if stack else None
    pinned = pins.get(host) if pins else None

    if pinned is None:
        return _original_create_connection(
            address,
            timeout,
            source_address=source_address,
            socket_options=socket_options,
        )

    last_error = None
    for family, socktype, proto, _canonname, sockaddr in pinned:
        if family == socket.AF_INET:
            target = (sockaddr[0], port)
        elif family == socket.AF_INET6:
            target = (sockaddr[0], port, *sockaddr[2:])
        else:
            continue

        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            for opt in socket_options or ():
                sock.setsockopt(*opt)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()

    if last_error is not None:
        raise last_error
    raise OSError("DNS pin 没有可用地址")


@contextlib.contextmanager
def _pin_dns(hostname: str, addr_infos: list) -> Iterator[None]:
    """在当前线程内临时固定 hostname 的 DNS 解析结果。"""

    global _install_count, _original_create_connection

    with _install_lock:
        if _install_count == 0:
            _original_create_connection = urllib3_connection.create_connection
            urllib3_connection.create_connection = _pinned_create_connection
        _install_count += 1

    stack = _get_pin_stack()
    pins = dict(stack[-1]) if stack else {}
    pins[hostname] = addr_infos
    stack.append(pins)
    try:
        yield
    finally:
        stack.pop()
        with _install_lock:
            _install_count -= 1
            if _install_count == 0 and _original_create_connection is not None:
                urllib3_connection.create_connection = _original_create_connection
                _original_create_connection = None


def _resolve_and_validate(url: str) -> tuple[bool, str, str | None, list | None]:
    """解析 URL 并确认目标不是内网、本机或保留地址。"""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, f"不支持的 URL 协议：{parsed.scheme or '<empty>'}", None, None
    if not parsed.hostname:
        return False, "URL 中没有可解析的 hostname", None, None

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False, f"无法解析 hostname：{parsed.hostname}", parsed.hostname, None
    if not addr_infos:
        return False, f"无法解析 hostname：{parsed.hostname}", parsed.hostname, None

    for addr_info in addr_infos:
        ip_text = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False, f"无法识别解析地址：{ip_text}", parsed.hostname, None
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, f"URL 解析到被禁止访问的地址：{ip_text}", parsed.hostname, None

    return True, "", parsed.hostname, addr_infos


def blocked_response(url: str, reason: str) -> dict[str, Any]:
    """生成统一的阻断响应，方便工具和测试复用。"""

    return {
        "ok": False,
        "status_code": 0,
        "headers": {},
        "content": "",
        "url": url,
        "error": f"请求被安全策略拦截：{reason}",
    }


def request_with_safe_redirects(
    method: str,
    url: str,
    *,
    timeout: int,
    **kwargs: Any,
) -> tuple[requests.Response | None, dict[str, Any] | None]:
    """发起安全 HTTP 请求，并在每次重定向前重新校验目标地址。"""

    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)

    for redirect_count in range(MAX_REDIRECTS + 1):
        is_safe, reason, hostname, addr_infos = _resolve_and_validate(current_url)
        if not is_safe or hostname is None or addr_infos is None:
            return None, blocked_response(current_url, reason)

        with _pin_dns(hostname, addr_infos):
            response = requests.request(
                current_method,
                current_url,
                timeout=timeout,
                allow_redirects=False,
                **request_kwargs,
            )

        if not response.is_redirect and not response.is_permanent_redirect:
            return response, None

        location = response.headers.get("Location")
        if not location:
            return response, None
        if redirect_count == MAX_REDIRECTS:
            return None, blocked_response(current_url, "重定向次数过多")

        current_url = urljoin(str(response.url), location)
        if response.status_code == requests.codes.see_other or (
            response.status_code in {requests.codes.moved, requests.codes.found}
            and current_method not in {"GET", "HEAD"}
        ):
            current_method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)

    return None, blocked_response(current_url, "重定向次数过多")
