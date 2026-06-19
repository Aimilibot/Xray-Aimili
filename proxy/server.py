#!/usr/bin/env python3
from __future__ import annotations
import select
import socket
import threading
import urllib.parse
import time
from typing import Any

from utils import vpn as vpn_utils

def parse_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Unexpected disconnect.")
        data += chunk
    return data

def parse_host_port(authority: str, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):
        host_part, sep, rest = authority.partition("]")
        host = host_part.lstrip("[")
        port = default_port
        if sep and rest.startswith(":"):
            port = parse_int(rest[1:]) or default_port
        return host, port
    if authority.count(":") == 1:
        host, _, port_text = authority.rpartition(":")
        return host, parse_int(port_text) or default_port
    return authority, default_port

def resolve_dns_over_tun0(host: str, dns_servers: list[str] = ["8.8.8.8", "1.1.1.1", "9.9.9.9", "8.8.4.4"], timeout: float = 2.0) -> str | None:
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return host
    except OSError:
        pass

    import random
    tx_id = random.getrandbits(16).to_bytes(2, "big")
    flags = b"\x01\x00"
    questions = b"\x00\x01"
    rrs = b"\x00\x00\x00\x00\x00\x00"

    qname = b""
    for part in host.split("."):
        if not part:
            continue
        part_bytes = part.encode("idna")
        qname += len(part_bytes).to_bytes(1, "big") + part_bytes
    qname += b"\x00"

    qtype_qclass = b"\x00\x01\x00\x01"
    packet = tx_id + flags + questions + rrs + qname + qtype_qclass

    for dns_server in dns_servers:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
            SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", None)
            if SO_BINDTODEVICE is not None:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, active_dev.encode("utf-8"))
                except OSError as e:
                    if "operation not permitted" in str(e).lower() or e.errno == 1:
                        print(f"[DNS 绑定警告] DNS 解析绑定 {active_dev} 权限不足，降级为默认路由路径进行查询。", flush=True)
                    elif "no such device" in str(e).lower() or e.errno == 19:
                        print(f"[DNS 绑定警告] DNS 解析绑定 {active_dev} 设备不存在，降级为默认路由路径进行查询。", flush=True)
            sock.sendto(packet, (dns_server, 53))
            resp, _ = sock.recvfrom(2048)
            
            if len(resp) < 12:
                continue
            if resp[:2] != tx_id:
                continue
            rcode = resp[3] & 0x0F
            if rcode != 0:
                continue

            offset = 12
            while offset < len(resp):
                length = resp[offset]
                if length == 0:
                    offset += 1
                    break
                elif (length & 0xC0) == 0xC0:
                    offset += 2
                    break
                else:
                    offset += 1 + length

            offset += 4
            answers_count = int.from_bytes(resp[6:8], "big")
            if answers_count == 0:
                continue

            for _ in range(answers_count):
                if offset >= len(resp):
                    break
                while offset < len(resp):
                    length = resp[offset]
                    if length == 0:
                        offset += 1
                        break
                    elif (length & 0xC0) == 0xC0:
                        offset += 2
                        break
                    else:
                        offset += 1 + length
                if offset + 10 > len(resp):
                    break
                atype = int.from_bytes(resp[offset : offset + 2], "big")
                aclass = int.from_bytes(resp[offset + 2 : offset + 4], "big")
                rdlength = int.from_bytes(resp[offset + 8 : offset + 10], "big")
                offset += 10
                if offset + rdlength > len(resp):
                    break
                if atype == 1 and aclass == 1 and rdlength == 4:
                    ip_bytes = resp[offset : offset + 4]
                    sock.close()
                    return socket.inet_ntoa(ip_bytes)
                offset += rdlength
        except Exception:
            pass
        finally:
            sock.close()
    return None

def create_connection(address: tuple[str, int], timeout: float = 20) -> socket.socket:
    host, port = address
    ptype, phost, pport = vpn_utils.get_upstream_proxy()
    SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", None)

    # 1. macOS / Developer Environment SOCKS5 / HTTP dynamic forwarding fallback
    if SO_BINDTODEVICE is None and ptype in ("socks", "http") and phost and pport:
        try:
            print(f"[Proxy Upstream] 非 Linux 环境使用上游 {ptype} 代理 ({phost}:{pport}) 动态转发至 {host}:{port}", flush=True)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((phost, pport))
            if ptype == "socks":
                sock.sendall(b"\x05\x01\x00")
                resp = recv_exact(sock, 2)
                if resp[0] != 5 or resp[1] != 0:
                    raise RuntimeError("SOCKS5 认证失败或不支持该认证方式")
                host_bytes = host.encode("idna")
                req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, "big")
                sock.sendall(req)
                resp_header = recv_exact(sock, 4)
                if resp_header[0] != 5 or resp_header[1] != 0:
                    raise RuntimeError(f"SOCKS5 代理建立连接请求被拒绝: {resp_header[1]}")
                atyp = resp_header[3]
                if atyp == 1:
                    recv_exact(sock, 6)
                elif atyp == 3:
                    addr_len = recv_exact(sock, 1)[0]
                    recv_exact(sock, addr_len + 2)
                elif atyp == 4:
                    recv_exact(sock, 18)
                else:
                    raise RuntimeError(f"未知的 SOCKS5 ATYP: {atyp}")
            else: # http
                req_str = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\nUser-Agent: Mozilla/5.0 vpngate-openvpn-manager/2.0\r\nProxy-Connection: Keep-Alive\r\n\r\n"
                sock.sendall(req_str.encode('ascii'))
                resp = sock.recv(4096)
                if not (b"200" in resp or b"established" in resp.lower() or b"ok" in resp.lower()):
                    raise RuntimeError(f"HTTP CONNECT 隧道连接失败: {resp.decode('utf-8', errors='replace')}")
            return sock
        except Exception as e:
            print(f"[Proxy Warning] 上游代理动态转发失败: {e}，将降级至普通直连网关模式", flush=True)

    # 2. Ordinary Gateway Proxy Mode / direct connection fallback
    resolved_ip = resolve_dns_over_tun0(host)
    if resolved_ip:
        target_host = resolved_ip
    else:
        target_host = host

    err = None
    for res in socket.getaddrinfo(target_host, port, 0, socket.SOCK_STREAM):
        af, socktype, proto, canonname, sa = res
        sock = None
        active_dev = getattr(vpn_utils, 'ACTIVE_TUN_DEVICE', 'tun0')
        try:
            sock = socket.socket(af, socktype, proto)
            sock.settimeout(timeout)
            if SO_BINDTODEVICE is not None:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, active_dev.encode("utf-8"))
                except OSError as e:
                    # setsockopt SO_BINDTODEVICE failed (e.g. no root permission or tun dev missing), fallback gracefully
                    print(f"[Proxy Warning] 绑定虚拟网卡 {active_dev} 失败: {e}，将安全降级到普通网关模式。", flush=True)
            sock.connect(sa)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    else:
        raise OSError("getaddrinfo returns empty list")

def relay(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    while True:
        readable, _, errored = select.select(sockets, [], sockets, 120)
        if errored:
            return
        for source in readable:
            target = right if source is left else left
            data = source.recv(65536)
            if not data:
                return
            target.sendall(data)

def socks5_client(client: socket.socket, first_byte: bytes) -> None:
    upstream = None
    try:
        methods_count = recv_exact(client, 1)[0]
        recv_exact(client, methods_count)
        client.sendall(b"\x05\x00")
        version, command, _, address_type = recv_exact(client, 4)
        if version != 5 or command != 1:
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        if address_type == 1:
            host = socket.inet_ntoa(recv_exact(client, 4))
        elif address_type == 3:
            host = recv_exact(client, recv_exact(client, 1)[0]).decode("idna")
        elif address_type == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        else:
            client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        port = int.from_bytes(recv_exact(client, 2), "big")
        try:
            upstream = create_connection((host, port), timeout=20)
        except Exception as e:
            print(f"[SOCKS5 代理失败] 目标 {host}:{port} 连接失败: {e}", flush=True)
            try:
                client.sendall(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
            raise
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        relay(client, upstream)
    finally:
        client.close()
        if upstream:
            upstream.close()

def read_http_header(client: socket.socket, first_byte: bytes) -> bytes:
    data = first_byte
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = client.recv(4096)
        if not chunk:
            break
        data += chunk
    return data

def read_http_body_remainder(client: socket.socket, headers: list[str], buffered: bytes) -> bytes:
    content_length = 0
    for line in headers:
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "content-length":
            content_length = parse_int(value.strip())
            break
    if content_length <= len(buffered):
        return buffered
    chunks = [buffered]
    remaining = content_length - len(buffered)
    while remaining > 0:
        chunk = client.recv(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

def http_client(client: socket.socket, first_byte: bytes) -> None:
    upstream = None
    try:
        header = read_http_header(client, first_byte)
        if b"\r\n\r\n" not in header:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        head, rest = header.split(b"\r\n\r\n", 1)
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        if not version.startswith("HTTP/"):
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        if method.upper() == "CONNECT":
            host, port = parse_host_port(target, 443)
            upstream = create_connection((host, port), timeout=20)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            relay(client, upstream)
            return

        parsed = urllib.parse.urlsplit(target)
        hostname = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme
        if not hostname:
            for line in lines[1:]:
                if line.lower().startswith("host:"):
                    hostname, parsed_port = parse_host_port(line.split(":", 1)[1].strip(), 0)
                    port = parsed_port or None
                    break
        if not hostname:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        port = port or (443 if scheme == "https" else 80)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = [line for line in lines[1:] if not line.lower().startswith(("proxy-connection:", "connection:"))]
        request = f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\nConnection: close\r\n\r\n"
        body = read_http_body_remainder(client, headers, rest)
        upstream = create_connection((hostname, port), timeout=20)
        upstream.sendall(request.encode("iso-8859-1") + body)
        relay(client, upstream)
    except Exception as e:
        print(f"[HTTP 代理失败] 代理请求目标连接失败: {e}", flush=True)
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
    finally:
        client.close()
        if upstream:
            upstream.close()

def proxy_client(client: socket.socket, address: tuple[str, int]) -> None:
    try:
        client.settimeout(30)
        first = recv_exact(client, 1)
        if first == b"\x05":
            socks5_client(client, first)
        else:
            http_client(client, first)
    except Exception as e:
        err_msg = str(e)
        if "[错误代码" in err_msg:
            print(f"[代理客户端连接失败] 客户端 {address} 遭遇系统性阻碍: {err_msg}", flush=True)
        try:
            client.close()
        except OSError:
            pass

def start_proxy_server(host: str, port: int) -> None:
    is_ipv6 = ":" in host or host == ""
    af = socket.AF_INET6 if is_ipv6 else socket.AF_INET
    try:
        server = socket.socket(af, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if is_ipv6:
            try:
                server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        server.bind((host, port))
        server.listen(256)
        print(f"HTTP/SOCKS5 proxy listening on {host}:{port}", flush=True)
    except Exception as e:
        if is_ipv6 and host == "::":
            print(f"[警告] 绑定 IPv6 {host}:{port} 失败 ({e})，正在尝试回退至 IPv4 0.0.0.0 ...", flush=True)
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("0.0.0.0", port))
                server.listen(256)
                print(f"HTTP/SOCKS5 proxy listening on 0.0.0.0:{port} (仅 IPv4)", flush=True)
            except Exception as ex:
                diag = vpn_utils.diagnose_local_obstructions(port, host="0.0.0.0")
                diag_msg = diag[1] if diag else str(ex)
                print(f"[ERROR] Failed to start HTTP/SOCKS5 proxy on 0.0.0.0:{port}: {diag_msg}", flush=True)
                return
        elif is_ipv6 and host == "::1":
            print(f"[警告] 绑定 IPv6 {host}:{port} 失败 ({e})，正在尝试回退至 IPv4 127.0.0.1 ...", flush=True)
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", port))
                server.listen(256)
                print(f"HTTP/SOCKS5 proxy listening on 127.0.0.1:{port} (仅 IPv4)", flush=True)
            except Exception as ex:
                diag = vpn_utils.diagnose_local_obstructions(port, host="127.0.0.1")
                diag_msg = diag[1] if diag else str(ex)
                print(f"[ERROR] Failed to start HTTP/SOCKS5 proxy on 127.0.0.1:{port}: {diag_msg}", flush=True)
                return
        else:
            diag = vpn_utils.diagnose_local_obstructions(port, host=host)
            diag_msg = diag[1] if diag else str(e)
            print(f"[ERROR] Failed to start HTTP/SOCKS5 proxy on {host}:{port}: {diag_msg}", flush=True)
            return

    while True:
        try:
            client, address = server.accept()
            threading.Thread(target=proxy_client, args=(client, address), daemon=True).start()
        except Exception as e:
            print(f"[ERROR] Proxy accept failed: {e}", flush=True)
            time.sleep(0.5)
