import base64
from dataclasses import dataclass

import zeep

from core.exceptions import PacketaApiError

from .models import PacketaConfig

SOAP_WSDL = 'https://www.zasilkovna.cz/api/soap.wsdl'

# Lazy singleton - the WSDL is fetched over the network on first use, not at
# import time, so importing this module never requires network access (safe
# for tests/management commands that don't touch Packeta).
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = zeep.Client(wsdl=SOAP_WSDL)
    return _client


@dataclass
class CreatedPacket:
    packet_id: str
    barcode: str
    barcode_text: str


def create_packet(*, packet_attributes: dict) -> CreatedPacket:
    """Calls Packeta's createPacket - the same method creates a packet for
    any delivery type, the type is determined by which packet_attributes
    fields are set (see shipping.services.build_packet_attributes).
    """
    try:
        result = _get_client().service.createPacket(PacketaConfig.get_solo().api_password, packet_attributes)
    except zeep.exceptions.Fault as exc:
        raise PacketaApiError(str(exc.message)) from exc

    return CreatedPacket(packet_id=str(result.id), barcode=result.barcode, barcode_text=result.barcodeText)


def get_label_pdf(*, packet_id: str, label_format: str) -> bytes:
    """Fetches a packet's shipping label via packetLabelPdf. Packeta returns
    the PDF base64-encoded - this returns the decoded bytes.
    """
    try:
        result = _get_client().service.packetLabelPdf(
            PacketaConfig.get_solo().api_password, packet_id, label_format, 0,
        )
    except zeep.exceptions.Fault as exc:
        raise PacketaApiError(str(exc.message)) from exc

    return base64.b64decode(result)


@dataclass
class PacketStatus:
    status_code: int
    code_text: str
    status_text: str


def get_packet_status(*, packet_id: str) -> PacketStatus:
    """Fetches the current state of a packet via packetStatus - the latest
    status only (not the full history; see packetTracking for that).
    """
    try:
        result = _get_client().service.packetStatus(PacketaConfig.get_solo().api_password, packet_id)
    except zeep.exceptions.Fault as exc:
        raise PacketaApiError(str(exc.message)) from exc

    return PacketStatus(
        status_code=int(result.statusCode), code_text=result.codeText, status_text=result.statusText,
    )
