"""Offerly HTTP server with browser Agent and phone collaboration."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from offerly.services import ApplicationServices
from frontend.backend import make_app
from frontend.phone import PhoneHub

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9189)
    parser.add_argument(
        "--public-origin", help="Public HTTPS site connected by native helpers"
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--phone-ice-config", type=Path)
    parser.add_argument(
        "--phone-host", help="Physical LAN IPv4 address, never a public interface"
    )
    parser.add_argument("--phone-port", type=int, default=9190)
    parser.add_argument("--phone-cert", help="TLS certificate trusted by the phone")
    parser.add_argument(
        "--phone-key", help="Matching TLS private key; never share this file"
    )
    parser.add_argument(
        "--phone-tunnel",
        help="cloudflared executable for a temporary trusted HTTPS phone entry",
    )
    args = parser.parse_args()
    if args.public_origin:
        if not args.data_dir or args.phone_host or args.phone_tunnel:
            parser.error("正式网站需要独立 --data-dir，由 HTTPS 网站提供手机入口。")
        from frontend.hosted import HostedSite

        os.umask(0o077)
        ice = (
            json.loads(args.phone_ice_config.read_text(encoding="utf-8"))
            if args.phone_ice_config
            else {}
        )
        hosted = HostedSite(args.data_dir, args.public_origin, ice)
        web.run_app(
            hosted.app(), host="127.0.0.1", port=args.port, access_log=None, print=None
        )
        raise SystemExit(0)
    manager = ApplicationServices(args.data_dir)
    app = make_app(manager)
    if args.phone_tunnel and not args.phone_host:
        parser.error("公网手机入口仍需指定电脑局域网地址供 WebRTC 使用。")
    if args.phone_host:
        import ipaddress

        address = ipaddress.ip_address(args.phone_host)
        if (
            address.version != 4
            or not address.is_private
            or address.is_unspecified
            or address.is_loopback
        ):
            parser.error("手机地址必须为实际局域网 IPv4 地址。")
        import ssl

        tls = None
        if bool(args.phone_cert) != bool(args.phone_key):
            parser.error("HTTPS 需要同时提供证书和私钥。")
        if args.phone_tunnel and args.phone_cert:
            parser.error("公网入口与本地测试证书只能选一种。")
        if args.phone_cert:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(args.phone_cert, args.phone_key)
        manager.phone = PhoneHub(
            manager, args.phone_host, args.phone_port, "https" if tls else "http"
        )

        async def phone_lifecycle(app):
            runner = web.AppRunner(manager.phone.app(), access_log=None)
            tunnel = None
            connecting = None
            await runner.setup()
            try:
                bind = "127.0.0.1" if args.phone_tunnel else args.phone_host
                await web.TCPSite(
                    runner, bind, args.phone_port, ssl_context=tls
                ).start()
                if args.phone_tunnel:
                    from frontend.tunnel import PhoneTunnel

                    tunnel = PhoneTunnel(
                        manager.phone,
                        args.phone_tunnel,
                        manager.root / "tmp/phone-tunnel",
                    )
                    manager.phone.connection_error = "手机 HTTPS 入口正在连接，请稍候。"

                    async def connect_phone():
                        try:
                            await tunnel.start(f"http://127.0.0.1:{args.phone_port}")
                            print(
                                "手机 HTTPS 入口已就绪；请在电脑手机连接页生成配对链接。",
                                flush=True,
                            )
                        except (OSError, RuntimeError, TimeoutError):
                            manager.phone.connection_error = "手机 HTTPS 通道连接失败；电脑端可继续使用，请检查网络后重新启动服务。"
                            print(
                                "手机 HTTPS 通道连接失败；电脑端服务继续运行。",
                                flush=True,
                            )

                    connecting = asyncio.create_task(connect_phone())
                yield
            finally:
                # Finish the task while its notification channel is still alive.
                await manager.close()
                if connecting:
                    connecting.cancel()
                    await asyncio.gather(connecting, return_exceptions=True)
                if tunnel:
                    await tunnel.close()
                await runner.cleanup()

        app.cleanup_ctx.append(phone_lifecycle)
    print(f"Offerly: http://127.0.0.1:{args.port}", flush=True)
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None, print=None)
