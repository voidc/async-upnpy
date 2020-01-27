# -*- coding: utf-8 -*-

import socket
import struct
import asyncio
import logging
import errno

logger = logging.getLogger('ssdp')

MULTICAST_ADDRESS = '239.255.255.250'
MULTICAST_PORT = 1900


class SSDPDevice():

    def __init__(self, usn, location):
        self.usn = usn
        self.location = location

        self.extra = None
        self.subdevices = []

    def uuid(self):
        try:
            return self.usn.split(':')[1]
        except IndexError:
            return self.usn

    def target(self):
        try:
            return self.usn.split('::')[1]
        except IndexError:
            return self.usn

    def matches_target(self, search_target):
        return search_target == 'ssdp:all' or search_target == self.target()


class SimpleServiceDiscoveryProtocol(asyncio.DatagramProtocol):

    def __init__(self, device_callback=None, filter=None):
        self.device_callback = device_callback or (lambda _: None)
        self.local_devices = []
        self.filter = filter  # filtering off

        self.handlers = {
            'NOTIFY * HTTP/1.1': self.handle_notify,
            'M-SEARCH * HTTP/1.1': self.handle_search,
            'HTTP/1.1 200 OK': self.handle_search_response,
        }

    def announce_device(self, device):
        self.local_devices.append(device)
        self.send_notify(device)

    def search_devices(self):
        if self.filter is None:
            self.send_search()
        else:
            self.send_search(search_target=self.filter)

    def connection_made(self, transport):
        self.transport = transport

    def send(self, data, addr):
        logger.debug("%s:%s < \"%s\"", *(addr + (data,)))
        self.transport.sendto(data.encode('utf-8'), addr)

    def send_notify(self, device):
        data = (
            "NOTIFY * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "CACHE-CONTROL: max-age=3600\r\n"
            "LOCATION: {loc}\r\n"
            "NT: {nt}\r\n"
            "NTS: ssdp:alive\r\n"  # TODO send ssdp:byebye
            "SERVER: 'Linux UPnP/1.0 upnpy/0.1'\r\n"
            "USN: {usn}\r\n"
            "\r\n"
        ).format(loc=device.location, nt=device.target(), usn=device.usn)
        # data += "".join(f"{k}: {v}\r\n" for k, v in device.extra.items())
        # data += "\r\n"

        addr = (MULTICAST_ADDRESS, MULTICAST_PORT)
        self.send(data, addr)

    def send_search(self, search_target='ssdp:all', max_delay=2):
        data = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "ST: {st}\r\n"
            "MX: {mx}\r\n"
            "\r\n"
        ).format(st=search_target, mx=max_delay)

        addr = (MULTICAST_ADDRESS, MULTICAST_PORT)
        self.send(data, addr)

    def send_search_response(self, device, addr, search_target='ssdp:all'):
        data = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=3600\r\n"
            "LOCATION: {loc}\r\n"
            "SERVER: 'Linux UPnP/1.0 upnpy/0.1'\r\n"
            "ST: {st}\r\n"
            "USN: {usn}\r\n"
            "\r\n"
        ).format(loc=device.location, st=search_target, usn=device.usn)
        # data += "".join(f"{k}: {v}\r\n" for k, v in device.extra.items())
        # data += "\r\n"

        self.send(data, addr)

    def datagram_received(self, data, addr):
        data = data.decode()
        logger.debug("%s:%s > \"%s\"", *(addr + (data,)))
        data = data.splitlines()

        if len(data) < 1 or data[0] not in self.handlers.keys():
            return

        headers = {
            p[0].strip().lower(): p[1].strip()
            for p in (
                line.split(':', 1) for line in data[1:]
                if line and ':' in line
            )
        }

        method = data[0]
        self.handlers[method](headers, addr)

    def handle_notify(self, data, addr):
        logger.debug("NOTIFY")
        usn = data.get('usn')
        root_desc = data.get('location')
        # nts = data.get('nts')  # TODO handle ssdp:byebye
        device = SSDPDevice(usn, root_desc)
        device.extra = {k[2:]: data[k] for k in data if k.startswith('x-')}
        if not self.filter or device.matches_target(self.filter):
            self.device_callback(device)

    def handle_search(self, data, addr):
        logger.debug("SEARCH")
        for device in self.local_devices:
            if 'st' not in data or not self.filter:
                self.send_search_response(device, addr)
            elif device.matches_target(data['st']):
                self.send_search_response(
                    device, addr, search_target=data['st'])

    def handle_search_response(self, data, addr):
        logger.debug("SEARCH RESPONSE")
        usn = data.get('usn')
        root_desc = data.get('location')
        device = SSDPDevice(usn, root_desc)
        device.extra = {k[2:]: data[k] for k in data if k.startswith('x-')}
        if not self.filter or device.matches_target(self.filter):
            self.device_callback(device)

    def error_received(self, exc):
        if exc == errno.EAGAIN or exc == errno.EWOULDBLOCK:
            logger.error('Error received: %s', exc)
        else:
            raise IOError("Unexpected connection error") from exc
