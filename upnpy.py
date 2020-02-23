import argparse
import asyncio
import base64
import logging
import os
from pprint import pprint, pformat
import socket
import struct
import sys
import tempfile
import urllib.parse
import uuid

from ssdp import SimpleServiceDiscoveryProtocol, SSDPDevice
from ssdp import MULTICAST_ADDRESS, MULTICAST_PORT

from scpd import MetadataServer, MetadataClient
from scpd import ROOT_DESC_PATH

logger = logging.getLogger('upnpy')


class UPnPy():

    def __init__(self, loop):
        self.loop = loop
        self.remote_devices = {}
        self.desc_cache = {}
        self.icon_cache = {}
        self.listeners = []

        self.wait = 6
        self.filter = None

    async def run_unix_socket(self, path):
        logger.info("Creating unix socket at %s", path)
        server = await asyncio.start_unix_server(
            self.on_listener_connected, path)
        try:
            async with server:
                await server.serve_forever()
        finally:
            server.close()
            os.remove(path)

    async def on_listener_connected(self, reader, writer):
        logger.info("Listener connected")
        self.listeners.append(writer)

        # TODO check if devices still up
        for device in self.remote_devices.values():
            await self.notify_listener(writer, device)

        self.loop.create_task(self.discover())

    def add_remote_device(self, device):
        unique = False
        parts = device.usn.split('::', 1)
        if parts[0] not in self.remote_devices:
            self.remote_devices[parts[0]] = SSDPDevice(parts[0], device.location)
            unique = True
        if len(parts) == 2 and not any(sub.usn == device.usn for sub in self.remote_devices[parts[0]].subdevices):
            self.remote_devices[parts[0]].subdevices.append(device)
        return unique


    async def notify_listener(self, listener, device, sub=False):
        try:
            if not sub:
                listener.write(f'DEVICE {device.usn}\n'.encode('utf-8'))
            else:
                listener.write(f'SUBDEVICE {device.usn}\n'.encode('utf-8'))
            
            if device.location:
                (desc, icon) = await self.get_desc_and_icon(device.location)

            if desc is None:
                return

            listener.write(f'META {device.usn}\n'.encode('utf-8'))
            listener.writelines(
                f'{k}:{v}\n'.encode('utf-8')
                for k, v in desc.items()
                if isinstance(v, str)
            )

            if icon:
                listener.write(f'ICON {device.usn}\n'.encode('utf-8'))
                # b64 so we can terminate line with \n
                listener.write(base64.b64encode(icon) + b'\n')

            for subdevice in device.subdevices:
                await self.notify_listener(listener, subdevice, sub=True)

            await listener.drain()

        except ConnectionResetError:
            logger.info("Listener disconnected")
            try:
                self.listeners.remove(listener)
            except ValueError:
                pass

    def on_new_device(self, device):
        if not device.usn:
            return

        if not self.add_remote_device(device):
            logger.info("Found duplicate device %s", device.usn)
            return

        logger.info("Found new device %s", device.usn)
        logger.debug(pformat(device.__dict__))

        async def coro():
            if device.location:
                (desc, icon) = await self.get_desc_and_icon(device.location)

            if desc is not None:
                logger.info("Found metadata for %s", device.usn)
                logger.debug(pformat(desc))
            if icon is not None:
                logger.info("Found icon for %s", device.usn)

            for listener in self.listeners[:]:
                await self.notify_listener(listener, device)

        self.loop.create_task(coro())

    async def get_desc_and_icon(self, location):
        if location in self.desc_cache:
            try:
                await self.desc_cache[location].wait()
            except AttributeError:
                pass
            return (self.desc_cache.get(location), self.icon_cache.get(location))
        else:
            done_event = asyncio.Event()
            self.desc_cache[location] = done_event
            await self.fetch_metadata(location)
            done_event.set()
            return (self.desc_cache.get(location), self.icon_cache.get(location))

    async def fetch_metadata(self, location):
        try:
            client = await MetadataClient(location).connect()
            metadata = await client.fetch_metadata()
        except ValueError:
            metadata = None

        if metadata is None:
            self.desc_cache[location] = None  # fetch_metadata must set cache
            return None

        self.desc_cache[location] = metadata

        try:
            client = await MetadataClient(metadata['icon']['url']).connect()
            icon = await client.fetch_icon()
        except (KeyError, ValueError):
            icon = None

        if icon is not None:
            self.icon_cache[location] = icon

        return metadata

    async def discover(self):
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # sock.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, '0.0.0.0')
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

        def ssdp_factory(): return SimpleServiceDiscoveryProtocol(
            device_callback=self.on_new_device, filter=self.filter)

        transport, protocol = await self.loop.create_datagram_endpoint(
            ssdp_factory, sock=sock)

        protocol.search_devices()

        try:
            await asyncio.sleep(self.wait)
        finally:
            transport.close()

    async def run_ssdp_deamon(self, discover=False, announce_devices=[]):
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # sock.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, '0.0.0.0')
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        mreq = struct.pack("4sl", socket.inet_aton(
            MULTICAST_ADDRESS), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.bind(('0.0.0.0', 1900))  # INADDR_ANY

        device_callback = self.on_new_device if discover else None
        def ssdp_factory(): return SimpleServiceDiscoveryProtocol(
            device_callback=device_callback, filter=self.filter)

        on_con_lost = self.loop.create_future()
        transport, protocol = await self.loop.create_datagram_endpoint(
            ssdp_factory, sock=sock)

        for device in announce_devices:
            protocol.announce_device(device)

        try:
            await on_con_lost
        finally:
            for device in announce_devices:
                protocol.remove_device(device)
            transport.close()

    async def serve_metadata(self, device):
        server = await MetadataServer(device).start()

        async with server:
            await server.serve_forever()


class UPnPDevice():

    def __init__(self, host, port, uuid, type, name):
        self.host = host
        self.port = port
        self.uuid = uuid
        self.type = type
        self.name = name
        self.icon = None

    def to_ssdp(self):
        location = f'http://{self.host}:{self.port}{ROOT_DESC_PATH}'
        usns = [
            f'uuid:{self.uuid}::upnp:rootdevice',
            f'uuid:{self.uuid}',
            f'uuid:{self.uuid}::{self.type}',
        ]

        return [SSDPDevice(usn, location) for usn in usns]


async def main():
    loop = asyncio.get_running_loop()
    upnpy = UPnPy(loop)

    async def discover(args):
        if args.filter and ':' not in args.filter:
            if args.filter == 'root':
                args.filter = 'upnp:rootdevice'
            else:
                args.filter = f"urn:schemas-upnp-org:device:{args.filter}:1"
        
        upnpy.filter = args.filter
        upnpy.wait = args.wait

        coros = []
        if args.sock:
            coros.append(upnpy.run_unix_socket(args.sock))
        else:
            coros.append(upnpy.discover())
        if not args.no_deamon:
            coros.append(upnpy.run_ssdp_deamon(discover=True))
        await asyncio.gather(*coros)

    async def announce(args):
        # TODO might return 171.0.0.1
        host = socket.gethostbyname(socket.gethostname())
        device = UPnPDevice(
            host, args.port,
            uuid.uuid4(),
            f"urn:schemas-upnp-org:device:{args.type}:1",
            args.name,
        )

        if args.icon:
            device.icon = args.icon.read()
            args.icon.close()

        upnpy.filter = not args.ignore_filter

        await asyncio.gather(
            upnpy.run_ssdp_deamon(announce_devices=device.to_ssdp()),
            upnpy.serve_metadata(device),
        )

    parser = argparse.ArgumentParser(description='UPnPy')
    parser.add_argument('-v', '--verbose', action='store_true')
    subparsers = parser.add_subparsers()

    parser_discover = subparsers.add_parser(
        'discover', help='Control point mode.')
    parser_discover.add_argument('--filter', default=None,
                                 help='If not specified, "ssdp:all" will be used as search target.')
    parser_discover.add_argument('--wait', type=int, default=6,
                                 help='Seconds to wait for responses after search.')
    parser_discover.add_argument('--sock', nargs='?', const=tempfile.gettempdir() + '/upnpy.sock',
                                 help='If specified, creates a unix socket at the given path, to which listeners can connect.')
    parser_discover.add_argument('--no-deamon', action='store_true',
                                 help='Disables listening for NOTIFY messages. Thus only a foreground search will be performed.')
    parser_discover.set_defaults(func=discover)

    parser_announce = subparsers.add_parser('announce', help='Device mode.')
    parser_announce.add_argument('--name', default='Basic Device',
                                 help='Friendly name of the device.')
    parser_announce.add_argument('--type', default='Basic',
                                 help='Device type')
    parser_announce.add_argument('--icon', type=argparse.FileType('rb'),
                                 help='Path to a PNG image to use as icon.')
    parser_announce.add_argument('--port', type=int, default=1999,
                                 help='Port on which the metadata server listens.')
    parser_announce.add_argument('--ignore-filter', action='store_true',
                                 help='Reply to all searches (ignore search target).')
    parser_announce.set_defaults(func=announce)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    await args.func(args)


asyncio.run(main(), debug=True)
