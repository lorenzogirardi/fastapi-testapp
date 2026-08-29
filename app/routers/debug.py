"""Debug router — network diagnostics, CPU spike, error injection, echo tools."""

import asyncio
import ipaddress
import multiprocessing
import random
import re
import socket
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from loguru import logger

from app.auth import verify_credentials
from app.config import get_settings
from app.models.schemas import CpuSpikeRequest, CpuSpikeResponse, NetworkScanResult

router = APIRouter(prefix="/api/debug", tags=["Debug"])

# Host validation: RFC-compliant hostname or IPv4
_HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")
_MAX_ECHO_BODY = 1024 * 1024  # 1 MB
_MAX_CURL_RESPONSE = 10 * 1024 * 1024  # 10 MB

# Track CPU spike processes for cleanup
_active_cpu_procs: list[multiprocessing.Process] = []


def _validate_host(host: str) -> None:
    """Validate hostname against strict pattern."""
    if not host or len(host) > 253 or not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="Invalid host")


def _host_to_ip(host: str, port: int | None = None) -> list[str]:
    """Resolve a host to its IP address(es)."""
    try:
        infos = socket.getaddrinfo(host, port or 80, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Cannot resolve host: {e}")
    return list({res[4][0] for res in infos})


def _is_blocked_ip(ip_str: str) -> bool:
    """True when an IP is loopback / link-local / private / reserved / multicast.

    Used by the SSRF guard: cloud-metadata (169.254.169.254), container-internal
    ranges, and localhost must not be reachable from network debug tools in prod.
    """
    ip = ipaddress.ip_address(ip_str.split("%")[0])
    return ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_multicast


def _guard_ssrf(host: str, port: int | None = None) -> None:
    """Reject loopback/private/link-local/metadata targets when protection is enabled."""
    settings = get_settings()
    if not settings.ssrf_protection_enabled:
        return
    for ip in _host_to_ip(host, port):
        if _is_blocked_ip(ip):
            raise HTTPException(status_code=400, detail="Target is a restricted/private address")


def _url_host(url: str) -> tuple[str, int | None]:
    """Extract and validate host + optional port from an http(s) URL."""
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Only http(s) URLs are supported")
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port
    if not host:
        raise HTTPException(status_code=400, detail="URL must include a host")
    return host, port


# ========== NETWORK SCAN (netshoot-like) ==========
@router.get(
    "/network/scan",
    response_model=NetworkScanResult,
    summary="Network diagnostic scan (ping, dns, tcp, traceroute)",
    dependencies=[Depends(verify_credentials)],
)
async def network_scan(target: str = Query(..., description="host:port or hostname")):
    host, port_str = (target.rsplit(":", 1) + [None])[:2]
    _validate_host(host)
    _guard_ssrf(host, int(port_str) if port_str and port_str.isdigit() else None)

    result = NetworkScanResult(target=target)

    # Ping
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "3", "-W", "2", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        result.ping = {
            "output": stdout.decode(),
            "returncode": proc.returncode,
        }
    except Exception as e:
        result.ping = {"error": str(e)}

    # DNS
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        )
        addresses = list({res[4][0] for res in infos})
        result.dns = {"addresses": addresses}
    except socket.gaierror as e:
        result.dns = {"error": str(e)}

    # TCP check
    if port_str:
        sock = None
        try:
            port = int(port_str)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            result.tcp_check = {"status": "open", "port": port}
        except Exception as e:
            result.tcp_check = {"status": "closed", "port": port_str, "error": str(e)}
        finally:
            if sock:
                sock.close()

    # Traceroute
    try:
        proc = await asyncio.create_subprocess_exec(
            "traceroute", "-m", "10", "-w", "2", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        result.traceroute = {"output": stdout.decode(), "returncode": proc.returncode}
    except FileNotFoundError:
        result.traceroute = {"error": "traceroute not installed"}
    except Exception as e:
        result.traceroute = {"error": str(e)}

    return result


# ========== CPU SPIKE ==========
def _cpu_burn(duration: int) -> None:
    """Burn CPU for `duration` seconds."""
    end = time.monotonic() + duration
    while time.monotonic() < end:
        _ = sum(i * i for i in range(10000))


async def _cleanup_cpu_procs(procs: list[multiprocessing.Process], duration: int) -> None:
    """Wait for CPU spike processes and clean up."""
    await asyncio.sleep(duration + 5)
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
        p.close()
    # Remove from active list
    for p in procs:
        if p in _active_cpu_procs:
            _active_cpu_procs.remove(p)


@router.post(
    "/cpu/spike",
    response_model=CpuSpikeResponse,
    summary="Simulate CPU load",
    dependencies=[Depends(verify_credentials)],
)
async def cpu_spike(params: CpuSpikeRequest):
    procs = []
    for _ in range(params.cores):
        p = multiprocessing.Process(target=_cpu_burn, args=(params.duration,), daemon=True)
        p.start()
        procs.append(p)
        _active_cpu_procs.append(p)

    # Schedule background cleanup
    asyncio.create_task(_cleanup_cpu_procs(procs, params.duration))

    return CpuSpikeResponse(
        status="started",
        duration=params.duration,
        cores=params.cores,
        message=f"CPU spike started on {params.cores} core(s) for {params.duration}s",
    )


# ========== DIAG ENDPOINTS (from original) ==========
@router.get("/ping", summary="Ping a host", dependencies=[Depends(verify_credentials)])
async def ping_host(host: str = Query(...), count: int = Query(default=3, ge=1, le=20)):
    _validate_host(host)
    _guard_ssrf(host)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", str(count), host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return Response(content=stdout.decode(), media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/dns", summary="DNS resolve", dependencies=[Depends(verify_credentials)])
async def dns_resolve(name: str = Query(...)):
    _validate_host(name)
    _guard_ssrf(name)
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
        )
        addresses = list({res[4][0] for res in results})
        return {"addresses": addresses}
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/curl", summary="HTTP GET a URL", dependencies=[Depends(verify_credentials)])
async def curl(url: str = Query(...)):
    _guard_ssrf(*_url_host(url))
    try:
        async with httpx.AsyncClient(
            timeout=5,
            follow_redirects=False,
            max_redirects=0,
        ) as client:
            resp = await client.get(url)
            body = resp.text[:_MAX_CURL_RESPONSE]
            return Response(
                content=body,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type"),
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tcp-check", summary="TCP connection check", dependencies=[Depends(verify_credentials)])
async def tcp_check(host: str = Query(...), port: int = Query(..., ge=1, le=65535)):
    _validate_host(host)
    _guard_ssrf(host, port)
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        return {"status": "success", "message": f"Successfully connected to {host}:{port}"}
    except (socket.timeout, socket.error) as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if sock:
            sock.close()


@router.api_route(
    "/headers",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="Echo request headers",
    dependencies=[Depends(verify_credentials)],
)
async def echo_headers(request: Request):
    return dict(request.headers)


@router.api_route(
    "/echo",
    methods=["POST", "PUT"],
    summary="Echo request body",
    dependencies=[Depends(verify_credentials)],
)
async def echo_body(request: Request):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_ECHO_BODY:
        raise HTTPException(status_code=413, detail=f"Body too large, max {_MAX_ECHO_BODY} bytes")
    body = await request.body()
    if len(body) > _MAX_ECHO_BODY:
        raise HTTPException(status_code=413, detail=f"Body too large, max {_MAX_ECHO_BODY} bytes")
    return Response(content=body, media_type=request.headers.get("content-type", "application/octet-stream"))


@router.get("/random-error", summary="Random HTTP error", dependencies=[Depends(verify_credentials)])
async def random_error():
    error_codes = [400, 401, 403, 404, 500, 502, 503, 504]
    code = random.choice(error_codes)
    raise HTTPException(status_code=code, detail=f"Randomly generated error: {code}")
