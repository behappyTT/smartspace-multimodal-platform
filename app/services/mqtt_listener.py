"""MQTT 接入服务。

该模块用于让平台同时支持 HTTP 和 MQTT 两种协议接入。
其中：
- HTTP 便于本地接口调试和 Swagger 演示
- MQTT 更适合树莓派真实设备持续上报，尤其是后续接入 SKU:SEN0501 这类传感器节点
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from app import schemas
from app.database import SessionLocal
from app.services.normalizer import process_upload


LOGGER = logging.getLogger(__name__)

MQTT_BROKER_HOST = os.getenv("SMARTSPACE_MQTT_HOST", "127.0.0.1")
MQTT_BROKER_PORT = int(os.getenv("SMARTSPACE_MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("SMARTSPACE_MQTT_TOPIC", "smartspace/sensor/upload/#")


def parse_mqtt_source_context(topic: str, qos: int, retain: bool) -> dict[str, str]:
    """根据 MQTT topic 和消息属性构造来源信息。"""

    topic_parts = topic.split("/")
    source_name = topic_parts[3] if len(topic_parts) >= 4 else "mqtt_source"
    collector_mode = topic_parts[4] if len(topic_parts) >= 5 else "unspecified"

    return {
        "transport": "mqtt",
        "source_name": source_name,
        "collector_mode": collector_mode,
        "mqtt_topic": topic,
        "mqtt_qos": str(qos),
        "mqtt_retain": str(retain).lower(),
        "client_ip": "mqtt_broker",
        "user_agent": "mqtt_client",
    }


def handle_mqtt_payload(payload_dict: dict, topic: str, qos: int, retain: bool) -> dict:
    """处理单条 MQTT 消息。

    这个函数独立出来，便于本地测试时不依赖真实 MQTT Broker。
    """

    source_context = parse_mqtt_source_context(topic=topic, qos=qos, retain=retain)
    payload = schemas.SensorUploadPayload.model_validate(payload_dict)

    db = SessionLocal()
    try:
        result = process_upload(db=db, payload=payload, source_context=source_context)
        return result.model_dump()
    finally:
        db.close()


@dataclass
class MqttIngestionService:
    """MQTT 订阅服务。"""

    broker_host: str = MQTT_BROKER_HOST
    broker_port: int = MQTT_BROKER_PORT
    topic: str = MQTT_TOPIC
    client: mqtt.Client | None = None
    started: bool = False

    def start(self) -> None:
        """启动 MQTT 监听。

        如果本地没有 MQTT Broker，不让整个 FastAPI 服务启动失败，而是记录警告后继续运行。
        """

        if self.started:
            return

        try:
            self.client = mqtt.Client()
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            self.client.loop_start()
            self.started = True
            LOGGER.info("MQTT listener started: %s:%s %s", self.broker_host, self.broker_port, self.topic)
        except Exception as exc:
            LOGGER.warning("MQTT listener start failed: %s", exc)
            self.client = None
            self.started = False

    def stop(self) -> None:
        """停止 MQTT 监听。"""

        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()
        self.client = None
        self.started = False

    def on_connect(self, client: mqtt.Client, _userdata, _flags, reason_code, _properties=None) -> None:
        """连接成功后订阅主题。"""

        LOGGER.info("MQTT connected, reason_code=%s", reason_code)
        client.subscribe(self.topic)

    def on_message(self, _client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
        """处理 MQTT 消息。"""

        try:
            payload_dict = json.loads(msg.payload.decode("utf-8"))
            result = handle_mqtt_payload(
                payload_dict=payload_dict,
                topic=msg.topic,
                qos=msg.qos,
                retain=msg.retain,
            )
            LOGGER.info("MQTT payload stored successfully: %s", result)
        except Exception as exc:
            LOGGER.warning("MQTT payload process failed: %s", exc)


mqtt_ingestion_service = MqttIngestionService()
