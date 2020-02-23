import asyncio
import base64
import logging
import pprint
import urllib.parse
from xml.etree import ElementTree

ROOT_DESC_PATH = "/root_desc.xml"
ICON_PATH = "/icon.png"

ROOT_DESC_TEMPLATE = """
<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
    <specVersion>
        <major>1</major>
        <minor>0</minor>
    </specVersion>
    <URLBase>http://{host}:{port}</URLBase>
    <device>
        <deviceType>{device_type}</deviceType>
        <friendlyName>{friendly_name}</friendlyName>
        <UDN>uuid:{uuid}</UDN>
        <UPC/>
        <iconList>
            <icon>
                <mimetype>image/png</mimetype>
                <width>32</width>
                <height>32</height>
                <depth>24</depth>
                <url>http://{host}:{port}{icon_path}</url>
            </icon>
        </iconList>
        <serviceList>
        </serviceList>
    </device>
</root>
"""

logger = logging.getLogger('scpd')


class MetadataServer():

    def __init__(self, device):
        self.host = device.host
        self.port = device.port

        self.root_desc = ROOT_DESC_TEMPLATE.format(
            host=device.host,
            port=device.port,
            device_type=device.type,
            friendly_name=device.name,
            uuid=device.uuid,
            icon_path=ICON_PATH,
        ).lstrip().encode('utf-8')

        self.icon = device.icon

        self.router = {
            f'GET {ROOT_DESC_PATH} HTTP/1.1': self.send_root_desc,
        }

        if self.icon:
            self.router[f'GET {ICON_PATH} HTTP/1.1'] = self.send_icon

    async def start(self):
        server = await asyncio.start_server(self.client_connected,
                                            port=self.port, host=self.host)

        addr = server.sockets[0].getsockname()
        logger.info(f'Serving on %s', addr)
        return server

    async def client_connected(self, reader, writer):
        line = await reader.readline()
        line = line.decode('latin1').rstrip()
        logger.info(line)
        if line in self.router.keys():
            self.router[line](writer)
        else:
            self.send_not_found(writer)
        await writer.drain()
        writer.close()

    # TODO add Date, Server, Connection: close

    def send_root_desc(self, writer):
        header = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/xml; charset=utf8\r\n"
            "Content-Length: {len}\r\n"
            "\r\n"
        ).format(len=len(self.root_desc))
        writer.write(header.encode('latin1'))
        writer.write(self.root_desc)

    def send_icon(self, writer):
        header = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: image/png\r\n"
            "Content-Length: {len}\r\n"
            "\r\n"
        ).format(len=len(self.icon))
        writer.write(header.encode('latin1'))
        writer.write(self.icon)

    def send_not_found(self, writer):
        body = "<html><body>Not found.</body></html>".encode('utf-8')
        header = (
            "HTTP/1.1 400 Not Found\r\n"
            "Content-Type: text/html; charset=utf8\r\n"
            "Content-Length: {len}\r\n"
            "\r\n"
        ).format(len=len(body))
        writer.write(header.encode('latin1'))
        writer.write(body)


class MetadataClient():

    def __init__(self, location):
        url = urllib.parse.urlparse(location)
        if not url.hostname or not url.port or not url.path:
            raise ValueError

        self.host = url.hostname
        self.port = url.port
        self.path = url.path

        self.reader = None
        self.writer = None

    async def connect(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.reader = reader
        self.writer = writer
        return self

    async def write_http_request(self):
        logger.info("Fetching %s", self.path)
        header = (
            "GET {path} HTTP/1.1\r\n"
            "HOST: {host}:{port}\r\n"
            "\r\n"
        ).format(host=self.host, port=self.port, path=self.path)
        self.writer.write(header.encode('latin1'))
        await self.writer.drain()

    async def fetch_metadata(self):
        await self.write_http_request()

        line = await self.reader.readline()
        line = line.decode('latin1').rstrip()
        if line != "HTTP/1.1 200 OK":
            logger.debug("Unexpected response: %s", line)
            return None
        await self.reader.readuntil(b'\r\n\r\n')  # read header

        root_desc = await self.reader.readuntil(b'</root>')  # uppercase??
        root_desc = root_desc.decode("utf-8")  # assumption: encoding is utf-8
        root_desc = root_desc + '\n'

        self.writer.close()
        return self.parse_metadata(root_desc)

    async def fetch_icon(self):
        await self.write_http_request()

        line = await self.reader.readline()
        line = line.decode('latin1').rstrip()
        if line != "HTTP/1.1 200 OK":
            logger.error("Unexpected response: %s", line)
            return None

        headers = {}
        while True:
            line = await self.reader.readline()
            if line == b'\r\n':
                break
            line = line.decode('latin1').rstrip()
            if not line or ':' not in line:
                logger.error("Unexpected header: %s", line)
                return None
            line = line.split(':', 1)
            headers[line[0].strip().lower()] = line[1].strip()

        if 'content-length' not in headers:
            logger.error("Missing content length header")
            return None
        try:
            length = int(headers['content-length'])
        except ValueError:
            return None

        data = await self.reader.readexactly(length)
        self.writer.close()
        return data

    def parse_metadata(self, root_desc):
        logger.debug("Parsing metadata")
        try:
            root = ElementTree.fromstring(root_desc)
            root = root.find('{urn:schemas-upnp-org:device-1-0}device')
        except (AttributeError, ElementTree.ParseError):
            return None
        if root is None:
            return None

        device = {}
        for prop in root:
            if len(prop) > 0:
                if prop.tag.endswith('iconList'):
                    icon = {}
                    for icon_prop in prop[0]:
                        key = icon_prop.tag.split('}')[1]
                        icon[key] = icon_prop.text
                    device['icon'] = icon
            else:
                key = prop.tag.split('}')[1]
                device[key] = prop.text

        return device
