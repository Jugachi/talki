"""xdg-desktop-portal GlobalShortcuts client (KDE Plasma 6, GNOME 48+).

KDE emits Deactivated on key release, which is what makes hold-to-talk possible.
"""

import asyncio
import logging
import secrets
import threading

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from ..paths import APP_ID
from . import ACTIONS

log = logging.getLogger(__name__)

PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
GS = "org.freedesktop.portal.GlobalShortcuts"


class PortalHotkeys:
    def __init__(self, callback, on_status=None):
        self.callback = callback
        self.on_status = on_status or (lambda s: None)
        self.loop = asyncio.new_event_loop()
        self.bus: MessageBus | None = None
        self.session: str | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.thread = threading.Thread(target=self._run, daemon=True, name="portal-hotkeys")

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
            self.loop.run_forever()
        except Exception as e:
            log.exception("portal hotkeys failed")
            self.on_status(f"Global shortcuts unavailable: {e}")

    async def _call(self, member: str, signature: str, body: list, interface: str = GS):
        reply = await self.bus.call(
            Message(destination=PORTAL, path=PATH, interface=interface, member=member,
                    signature=signature, body=body)
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name} {reply.body}")
        return reply.body

    async def _request(self, member: str, signature: str, body_fn) -> dict:
        """Portal request/response: the answer arrives as a Response signal on a Request object."""
        token = "talki_" + secrets.token_hex(6)
        sender = self.bus.unique_name.lstrip(":").replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        fut = self.loop.create_future()
        self._pending[path] = fut
        await self._call(member, signature, body_fn(token))
        code, results = await fut
        if code != 0:
            raise RuntimeError(f"{member} was denied or cancelled (code {code})")
        return results

    def _on_message(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False
        if msg.interface == "org.freedesktop.portal.Request" and msg.member == "Response":
            fut = self._pending.pop(msg.path, None)
            if fut and not fut.done():
                fut.set_result((msg.body[0], msg.body[1]))
            return False
        if msg.interface == GS and msg.body and msg.body[0] == self.session:
            if msg.member == "Activated":
                self.callback(msg.body[1], True)
            elif msg.member == "Deactivated":
                self.callback(msg.body[1], False)
        return False

    async def _main(self) -> None:
        self.bus = await MessageBus().connect()
        try:
            # Host (non-Flatpak) apps must identify themselves via a matching .desktop file.
            await self._call("Register", "sa{sv}", [APP_ID, {}], interface="org.freedesktop.host.portal.Registry")
        except Exception as e:
            log.info("portal Registry.Register unavailable: %s", e)
        self.bus.add_message_handler(self._on_message)
        for rule in (
            "type='signal',interface='org.freedesktop.portal.Request',member='Response'",
            f"type='signal',interface='{GS}'",
        ):
            await self.bus.call(
                Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule])
            )
        res = await self._request(
            "CreateSession", "a{sv}",
            lambda tok: [{"handle_token": Variant("s", tok),
                          "session_handle_token": Variant("s", "talki_session")}],
        )
        self.session = res["session_handle"].value
        shortcuts = [
            [aid, {"description": Variant("s", desc), "preferred_trigger": Variant("s", trig)}]
            for aid, (desc, trig) in ACTIONS.items()
        ]
        res = await self._request(
            "BindShortcuts", "oa(sa{sv})sa{sv}",
            lambda tok: [self.session, shortcuts, "", {"handle_token": Variant("s", tok)}],
        )
        bound = res.get("shortcuts")
        triggers = {}
        if bound:
            for sid, props in bound.value:
                t = props.get("trigger_description")
                triggers[sid] = t.value if t else ""
        self.triggers = triggers
        log.info("global shortcuts bound: %s", triggers)
        self.on_status("ready")

    def configure(self) -> None:
        """Opens the desktop's shortcut editor (portal v2)."""
        async def go():
            if self.session:
                await self._call("ConfigureShortcuts", "osa{sv}", [self.session, "", {}])

        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(go(), self.loop)
