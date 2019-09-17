import time
import logging
import string
import random
from importlib import import_module
from paho.mqtt.client import Client
from connectors.connector import Connector
from connectors.mqtt.json_mqtt_uplink_converter import JsonMqttUplinkConverter
from threading import Thread
from tb_utility.tb_utility import TBUtility
from json import loads, dumps

log = logging.getLogger(__name__)


class MqttConnector(Connector, Thread):
    def __init__(self, gateway, config):
        super().__init__()
        self.__gateway = gateway
        self.__broker = config.get('broker')
        self.__mapping = config.get('mapping')
        # regexp -> [converter1, converter2]
        self.__sub_topics = {}
        client_id = ''.join(random.choice(string.ascii_lowercase) for _ in range(23))
        self._client = Client(client_id)
        self.setName(TBUtility.get_parameter(self.__broker,
                                        "name",
                                        'Mqtt Broker ' + ''.join(random.choice(string.ascii_lowercase) for _ in range(5))))
        if self.__broker["credentials"]["type"] == "basic":
            self._client.username_pw_set(self.__broker["credentials"]["username"], self.__broker["credentials"]["password"])

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        self._client.on_disconnect = self._on_disconnect
        self._connected = False
        self.__stopped = False
        self.daemon = True

    def open(self):
        self.__stopped = False
        self.start()
        try:
            while not self._connected:
                try:
                    self._client.connect(self.__broker['host'],
                                         TBUtility.get_parameter(self.__broker, 'port', 1883))
                    self._client.loop_start()
                except Exception as e:
                    log.error(e)
                time.sleep(1)

        except Exception as e:
            log.error(e)
            try:
                self.close()
            except Exception as e:
                log.debug(e)

    def run(self):
        while True:
            time.sleep(.1)
            if self.__stopped:
                break

    def close(self):
        self._client.loop_stop()
        self._client.disconnect()
        self.__stopped = True
        log.info('%s has been stopped.', self.getName())

    def getName(self):
        return self.name

    def _on_connect(self, client, userdata, flags, rc, *extra_params):
        result_codes = {
            1: "incorrect protocol version",
            2: "invalid client identifier",
            3: "server unavailable",
            4: "bad username or password",
            5: "not authorised",
        }
        if rc == 0:
            self._connected = True
            log.info('%s connected to %s:%s - successfully.', self.getName(), self.__broker["host"], TBUtility.get_parameter(self.__broker, "port", "1883"))

            for mapping in self.__mapping:
                try:
                    log.debug(mapping)
                    if not self.__sub_topics.get(mapping.get("topicFilter")):
                        self.__sub_topics[mapping["topicFilter"]] = []
                    if mapping["converter"]["type"] == "custom":
                        converter = 1
                        # extension_name = 'connectors.mqtt.' + mapping["converter"]["extension"] # TODO load custom extension
                        # converter = import_module(extension_name)
                    else:
                        converter = JsonMqttUplinkConverter(mapping)
                    self.__sub_topics[mapping["topicFilter"]].append({converter: None})
                    self._client.subscribe(mapping["topicFilter"])
                    log.info('Subscribe to %s', mapping["topicFilter"])
                except Exception as e:
                    log.exception(e)
            log.debug(self.__sub_topics)

        else:
            if rc in result_codes:
                log.error("%s connection FAIL with error %s %s!", self.getName(), rc, result_codes[rc])
            else:
                log.error("%s connection FAIL with unknown error!", self.getName())

    def _on_disconnect(self):
        log.debug('%s was disconnected.', self.getName())

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        if granted_qos[0] == 128:
            log.error("Subscribtion failed, check your configs.")

    def _on_message(self, client, userdata, message):
        content = self._decode(message)
        if self.__sub_topics.get(message.topic):
            for converter in self.__sub_topics.get(message.topic):
                log.debug(converter)
                if converter:
                    converted_content = converter(content)
                    # TODO Test this Check validity of the converter output AS UTILITY
                    if converted_content:
                        self.__sub_topics.get(message.topic)[converter] = converted_content
                    else:
                        continue

                else:
                    log.error('Cannot find converter for topic:"%s"!', message.topic)
                self.__gateway._send_to_storage(self.getName(),converted_content)

    @staticmethod
    def _decode(message):
        content = loads(message.payload.decode("utf-8"))
        return content