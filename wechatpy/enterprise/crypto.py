from __future__ import absolute_import, unicode_literals
import time
import string
import random
import hashlib
import base64
import struct
import socket

import six
# pycrypto
from Crypto.Cipher import AES

from ..utils import to_binary, to_text
from ..exceptions import InvalidSignatureException
from .exceptions import InvalidCorpIdException


def _b(x):
    if six.PY2:
        return x
    else:
        import codecs

        return codecs.latin_1_encode(x)[0]


def get_sha1(token, timestamp, nonce, encrypt):
    sort_list = [token, timestamp, nonce, encrypt]
    sort_list.sort()
    sort_str = to_binary(''.join(sort_list))
    sha1 = hashlib.sha1()
    sha1.update(sort_str)
    return sha1.hexdigest()


class PKCS7Encoder(object):
    block_size = 32

    @classmethod
    def encode(cls, text):
        length = len(text)
        padding_count = cls.block_size - length % cls.block_size
        if padding_count == 0:
            padding_count = cls.block_size
        padding = chr(padding_count)
        return text + padding * padding_count

    @classmethod
    def decode(cls, decrypted):
        padding = ord(decrypted[-1])
        if padding < 1 or padding > 32:
            padding = 0
        return decrypted[:-padding]


class PrpCrypto(object):

    def __init__(self, key):
        self.key = key
        self.mode = AES.MODE_CBC

    def get_random_string(self):
        rule = string.letters + string.digits
        rand_list = random.sample(rule, 16)
        return ''.join(rand_list)

    def encrypt(self, text, corp_id):
        text = '{random}{pack}{text}{corp_id}'.format(
            random=self.get_random_string(),
            pack=struct.pack('I', socket.htonl(len(text))),
            text=text,
            corp_id=corp_id
        )
        text = PKCS7Encoder.encode(text)

        cryptor = AES.new(self.key, self.mode, self.key[:16])
        ciphertext = to_binary(cryptor.encrypt(text))
        return base64.b64encode(ciphertext)

    def decrypt(self, text, corp_id):
        text = to_binary(text)
        cryptor = AES.new(self.key, self.mode, self.key[:16])
        plain_text = cryptor.decrypt(base64.b64decode(text))
        padding = ord(_b(plain_text)[-1])
        content = plain_text[16:-padding]
        xml_length = socket.ntohl(struct.unpack('I', content[:4])[0])
        xml_content = content[4:xml_length + 4]
        from_corp_id = content[xml_length + 4:]
        if from_corp_id != corp_id:
            raise InvalidCorpIdException()
        return xml_content


class WeChatCrypto(object):

    def __init__(self, token, encoding_aes_key, corp_id):
        encoding_aes_key = to_binary(encoding_aes_key + '=')
        self.key = base64.b64decode(encoding_aes_key)
        assert len(self.key) == 32
        self.token = token
        self.corp_id = corp_id

    def check_signature(self, signature, timestamp, nonce, echo_str):
        _signature = get_sha1(self.token, timestamp, nonce, echo_str)
        if _signature != signature:
            raise InvalidSignatureException()
        pc = PrpCrypto(self.key)
        return pc.decrypt(echo_str, self.corp_id)

    def encrypt_message(self, msg, nonce, timestamp=None):
        xml = """<xml>
        <Encrypt><![CDATA[{encrypt}]]></Encrypt>
        <MsgSignature><![CDATA[{signature}]]></MsgSignature>
        <TimeStamp>{timestamp}</TimeStamp>
        <Nonce><![CDATA[{nonce}]]></Nonce>
        </xml>"""

        timestamp = timestamp or to_binary(int(time.time()))
        pc = PrpCrypto(self.key)
        encrypt = pc.encrypt(msg, self.corp_id)
        signature = get_sha1(self.token, timestamp, nonce, encrypt)
        return xml.format(
            encrypt=encrypt,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce
        )

    def decrypt_message(self, msg, signature, timestamp, nonce):
        if isinstance(msg, six.string_types):
            from xml.etree import ElementTree

            parser = ElementTree.fromstring(to_text(msg).encode('utf-8'))
            msg = dict((child.tag, to_text(child.text)) for child in parser)
        encrypt = msg['Encrypt']
        _signature = get_sha1(self.token, timestamp, nonce, encrypt)
        if _signature != signature:
            raise InvalidSignatureException()
        pc = PrpCrypto(self.key)
        xml = pc.decrypt(encrypt, self.corp_id)
        parser = ElementTree.fromstring(to_text(xml).encode('utf-8'))
        message = dict((child.tag, to_text(child.text)) for child in parser)
        return message