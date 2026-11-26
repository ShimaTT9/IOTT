"""Binary message format shared by sensor.py and collector.py.

Every message starts with the same 16-byte header (network byte order):

    offset  size  field       meaning
    0       1     type        0 = DATA, 1 = ACK
    1       1     qos         0 = fire-and-forget, 1 = acknowledged
    2       2     sensor_id   which sensor sent the reading
    4       4     seq         sequence number, used for loss and duplicate checks
    8       8     timestamp   time the reading was taken (float, seconds), used
                              for latency; retransmissions keep the original value

A DATA message adds a 32-byte payload: temperature (float32),
humidity (float32) and 24 bytes of padding, 48 bytes in total.
An ACK is the header alone (16 bytes) and echoes sensor_id, seq and timestamp.
"""

import struct
from collections import namedtuple

MSG_DATA = 0
MSG_ACK = 1

HEADER = struct.Struct("!BBHId")      # 1 + 1 + 2 + 4 + 8 = 16 bytes
PAYLOAD = struct.Struct("!ff24x")     # 4 + 4 + 24 padding = 32 bytes

HEADER_SIZE = HEADER.size             # 16
PAYLOAD_SIZE = PAYLOAD.size           # 32
DATA_SIZE = HEADER_SIZE + PAYLOAD_SIZE  # 48
ACK_SIZE = HEADER_SIZE                # 16
UDP_IP_OVERHEAD = 8 + 20              # UDP header + IPv4 header, bytes

Message = namedtuple(
    "Message", "type qos sensor_id seq timestamp temperature humidity")


class ProtocolError(ValueError):
    """Raised when a datagram is not a valid telemetry message."""


def pack_data(qos, sensor_id, seq, timestamp, temperature, humidity):
    """Build a 48-byte DATA message."""
    return (HEADER.pack(MSG_DATA, qos, sensor_id, seq & 0xFFFFFFFF, timestamp)
            + PAYLOAD.pack(temperature, humidity))


def pack_ack(sensor_id, seq, timestamp):
    """Build a 16-byte ACK for the DATA message (sensor_id, seq, timestamp)."""
    return HEADER.pack(MSG_ACK, 1, sensor_id, seq & 0xFFFFFFFF, timestamp)


def unpack(datagram):
    """Parse a datagram into a Message, or raise ProtocolError."""
    if len(datagram) < HEADER_SIZE:
        raise ProtocolError("short datagram: %d bytes" % len(datagram))
    mtype, qos, sensor_id, seq, timestamp = HEADER.unpack_from(datagram, 0)
    if qos not in (0, 1):
        raise ProtocolError("bad qos value %d" % qos)
    if mtype == MSG_DATA:
        if len(datagram) != DATA_SIZE:
            raise ProtocolError("DATA must be %d bytes, got %d"
                                % (DATA_SIZE, len(datagram)))
        temperature, humidity = PAYLOAD.unpack_from(datagram, HEADER_SIZE)
        return Message(mtype, qos, sensor_id, seq, timestamp,
                       temperature, humidity)
    if mtype == MSG_ACK:
        if len(datagram) != ACK_SIZE:
            raise ProtocolError("ACK must be %d bytes, got %d"
                                % (ACK_SIZE, len(datagram)))
        return Message(mtype, qos, sensor_id, seq, timestamp, None, None)
    raise ProtocolError("unknown message type %d" % mtype)


def wire_bytes(app_bytes):
    """Bytes on the IP network for one datagram of app_bytes payload."""
    return app_bytes + UDP_IP_OVERHEAD
