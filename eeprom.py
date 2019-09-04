#!/usr/bin/env python3
#
# Copyright 2019, Erik van Zijst <erik.van.zijst@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import atexit
import os
import readline
import sys
from io import BytesIO
from itertools import count
from struct import pack
from time import sleep
from typing import IO

from serial import Serial
from serial.tools import list_ports


def atoi(val: str) -> int:
    """Parses an address string into an integer.

    Supports decimal, hexadecimal and octal notation.
    """
    return int(val,
               16 if val.startswith('0x') else
               8 if val.startswith('0o') else
               10)


class AT28C256(object):
    usage = """AT28C256 EEPROM Programmer

Read or write individual addresses, dump out the full contents to a file, or
load an image file onto the EEPROM.

To read a single byte:
> [r|read] [addr]

To write a byte to a specific address:
> [w|write] [addr] [value]

To dump the entire EEPROM to a file:
> [d|dump] [filename]

To load a local file into the EEPROM:
> [l|load] [filename]

Address supports hex (0xFF) and octal (0o7) notation.
"""

    def __init__(self, port: Serial):
        self.port = port
        self.MAXPAYLOAD = 62

    def _receive(self, ack: bool = False) -> bytearray:
        """Reads a single message off the serial port and returns its contents.

        This function can optionally send an acknowledgement.
        """
        l = int.from_bytes(self.port.read(1), byteorder='big', signed=False)
        buf = memoryview(bytearray(l))
        tot = 0
        while tot < l:
            tot += self.port.readinto(buf)
        if ack:
            self._send(b'')
        return buf.obj

    def _send(self, data: bytes, ack: bool = False) -> None:
        """Wraps the specified data in a length header and transmits it as a
        single message. `data` should not exceed MAXPAYLOAD bytes.

        This function can optionally wait for an acknowledgement.
        """
        assert len(data) <= self.MAXPAYLOAD
        self.port.write(int.to_bytes(len(data), length=1, byteorder='big',
                                     signed=False))
        self.port.write(data)

        if ack:
            assert len(self._receive()) == 0

    def read(self, addr: str) -> None:
        self._send(pack('>cH', b'r', atoi(addr)))
        val = self._receive()
        print(int.from_bytes(val, byteorder='big', signed=False), '/',
              '0x' + val.hex())

    def write(self, addr: str, val: str) -> None:
        self._send(pack('>cHB', b'w', atoi(addr), atoi(val)), ack=True)
        print('OK')

    def dump(self, filename: str) -> None:
        with open(filename, 'wb') as f:
            self._send(b'd')     # send dump command

            cnt = 0
            while cnt < 0x8000:
                buf = self._receive(ack=True)
                f.write(buf)
                f.flush()
                cnt += len(buf)
                print('\r%d%%' % ((cnt / 0x8000) * 100), end='')

        print('\nComplete.')

    def load(self, filename: str) -> None:
        try:
            with open(filename, 'rb') as f:
                size = min(0x8000, os.fstat(f.fileno()).st_size)
                print('Loading %d bytes of %s into EEPROM...' % (size, filename))
                self.fload(f, size)
        except FileNotFoundError:
            print('File not found: ' + filename)

    def fload(self, f: IO, size: int) -> None:
        self._send(pack('>cH', b'l', size), ack=True)

        with open('fload.bin', 'wb') as fout:
            cnt = 0
            while True:
                data = f.read(min(self.MAXPAYLOAD, size - cnt))
                if not data:
                    break
                cnt += len(data)
                self._send(data, ack=True)
                fout.write(data)
                print('\r%d%%' % ((cnt * 100) / size), end='')

        print('\nComplete.')

    def test(self, *args) -> None:
        bio = BytesIO()
        j = 0
        for i in range(1024):
            bio.write(int.to_bytes(j, byteorder='big', length=1))
            j = (j + 1) % 16

        bio.seek(0)
        self.fload(bio, 512)

        sleep(.5)
        gen = count()
        for row in range(32):
            print('0x' + int.to_bytes(row, byteorder='big', length=1, signed=False).hex() + '0:  ', end='')
            for col in range(16):
                addr = next(gen)
                self._send(pack('>cH', b'r', addr))
                val = self._receive()
                print(val.hex() + ' ', end='')
            print('')

    def quit(self, *args) -> None:
        raise EOFError()

    def repl(self) -> None:
        print(self.usage)
        sleep(1)

        while True:
            try:
                expr = input('> ').split()
                {'r': self.read,
                 'read': self.read,
                 'w': self.write,
                 'write': self.write,
                 'd': self.dump,
                 'dump': self.dump,
                 'l': self.load,
                 'load': self.load,
                 't': self.test,
                 'quit': self.quit,
                 'q': self.quit}[expr[0]](*expr[1:])

            except EOFError:
                break
            except (ValueError, KeyError, IndexError, TypeError) as e:
                print('Invalid command:', e)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AT28C256 EEPROM Programmer')
    parser.add_argument('port', nargs='?',
                        help='the serial port the Arduino is '
                             'connected to (on OSX typically '
                             '/dev/tty.usbmodemXXXX)')
    args = parser.parse_args()

    dev = args.port
    if not args.port:
        try:
            # attempt to autodetect the Arduino
            dev = next(
                filter(lambda p: p.product and 'arduino' in p.product.lower() or
                       p.manufacturer and 'arduino' in p.manufacturer.lower(),
                       list_ports.comports())).device
            print('Found Arduino at port', dev)
        except StopIteration:
            print('Cannot find Arduino. If it is connected, specify the port '
                  'manually.', file=sys.stderr)
            exit(1)

    histfile = os.path.join(os.path.expanduser("~"), ".eeprom_history")
    try:
        readline.read_history_file(histfile)
        # default history len is -1 (infinite), which may grow unruly
        readline.set_history_length(1000)
    except FileNotFoundError:
        pass

    atexit.register(readline.write_history_file, histfile)
    AT28C256(Serial(port=dev, baudrate=19200, timeout=30)).repl()
