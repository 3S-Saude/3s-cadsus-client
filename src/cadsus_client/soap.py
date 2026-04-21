from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from textwrap import dedent
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .exceptions import CadSUSParseError


class SoapDocumentType(str, Enum):
    CPF = "CPF"
    CNS = "CNS"


SOAP_TEMPLATE = dedent(
    """\
    <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:urn="urn:ihe:iti:xds-b:2007" xmlns:urn1="urn:oasis:names:tc:ebxml-regrep:xsd:lcm:3.0" xmlns:urn2="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0" xmlns:urn3="urn:ihe:iti:xds-b:2007">
       <soap:Body>
          <PRPA_IN201305UV02 xsi:schemaLocation="urn:hl7-org:v3 ./schema/HL7V3/NE2008/multicacheschemas/PRPA_IN201305UV02.xsd" ITSVersion="XML_1.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="urn:hl7-org:v3">
             <id root="2.16.840.1.113883.4.714" extension="123456"/>
             <creationTime value="20070428150301"/>
             <interactionId root="2.16.840.1.113883.1.6" extension="PRPA_IN201305UV02"/>
             <processingCode code="T"/>
             <processingModeCode code="T"/>
             <acceptAckCode code="AL"/>
             <receiver typeCode="RCV">
                <device classCode="DEV" determinerCode="INSTANCE">
                   <id root="2.16.840.1.113883.3.72.6.5.100.85"/>
                </device>
             </receiver>
             <sender typeCode="SND">
                <device classCode="DEV" determinerCode="INSTANCE">
                   <id root="2.16.840.1.113883.3.72.6.2"/>
                   <name>{system_code}</name>
                </device>
             </sender>
             <controlActProcess classCode="CACT" moodCode="EVN">
                <code code="PRPA_TE201305UV02" codeSystem="2.16.840.1.113883.1.6"/>
                <queryByParameter>
                   <queryId root="1.2.840.114350.1.13.28.1.18.5.999" extension="1840997084"/>
                   <statusCode code="new"/>
                   <responseModalityCode code="R"/>
                   <responsePriorityCode code="I"/>
                   <parameterList>
                      <livingSubjectId>
                         <value root="{document_root}" extension="{identifier}"/>
                         <semanticsText>LivingSubject.id</semanticsText>
                      </livingSubjectId>
                   </parameterList>
                </queryByParameter>
             </controlActProcess>
          </PRPA_IN201305UV02>
       </soap:Body>
    </soap:Envelope>
    """
).strip()

DOCUMENT_ROOTS = {
    SoapDocumentType.CPF: "2.16.840.1.113883.13.237",
    SoapDocumentType.CNS: "2.16.840.1.113883.13.236",
}

CPF_DOCUMENT_ROOT = DOCUMENT_ROOTS[SoapDocumentType.CPF]
CNS_DOCUMENT_ROOT = DOCUMENT_ROOTS[SoapDocumentType.CNS]


def build_busca_pessoa_envelope(
    identifier: str,
    document_type: SoapDocumentType,
    *,
    system_code: str,
) -> str:
    return SOAP_TEMPLATE.format(
        system_code=escape(system_code),
        document_root=DOCUMENT_ROOTS[document_type],
        identifier=escape(identifier),
    )


def parse_busca_pessoa_response(xml: str) -> dict[str, Any] | None:
    """Converte o XML SOAP do CADSUS em um dicionario com os dados do paciente."""

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CadSUSParseError(
            "Falha ao interpretar o XML SOAP retornado pelo CADSUS."
        ) from exc

    patient = _find_path(
        root,
        "Body",
        "PRPA_IN201306UV02",
        "controlActProcess",
        "subject",
        "registrationEvent",
        "subject1",
        "patient",
        "patientPerson",
    )
    if patient is None:
        return None

    return _extract_patient_data(patient)


def _extract_patient_data(patient: ET.Element) -> dict[str, Any]:
    data: dict[str, Any] = {
        "lista_cns": [],
        "cns": None,
        "cpf": None,
        "falecido": False,
        "data_falecimento": None,
    }

    if nome := _extract_given_name(_find_child(patient, "name")):
        data["nome"] = nome

    if race_code := _attribute_from_child(patient, "raceCode", "code"):
        data["raca_cor"] = race_code

    if birth_time := _attribute_from_child(patient, "birthTime", "value"):
        if birth_date := _format_cadsus_date(birth_time):
            data["data_nascimento"] = birth_date

    if gender_code := _attribute_from_child(patient, "administrativeGenderCode", "code"):
        data["sexo"] = gender_code

    address = _find_child(patient, "addr")
    if address is not None:
        address_mapping = {
            "logradouro": "streetName",
            "bairro": "additionalLocator",
            "municipio_ibge": "city",
            "cep": "postalCode",
            "numero": "houseNumber",
        }
        for field_name, tag_name in address_mapping.items():
            if value := _first_child_text(address, tag_name):
                data[field_name] = value

    if phone := _extract_phone(patient):
        data["telefone"] = phone

    lista_cns, cns, cpf = _extract_documents(patient)
    data["lista_cns"] = lista_cns
    data["cns"] = cns
    if cpf and len(cpf) == 11:
        cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    data["cpf"] = cpf

    if mother_name := _extract_mother_name(patient):
        data["nome_da_mae"] = mother_name

    if deceased_ind := _attribute_from_child(patient, "deceasedInd", "value"):
        data["falecido"] = deceased_ind.lower() == "true"

    if deceased_time := _attribute_from_child(patient, "deceasedTime", "value"):
        data["data_falecimento"] = _format_cadsus_datetime(deceased_time) or deceased_time

    return data


def _extract_documents(patient: ET.Element) -> tuple[list[str], str | None, str | None]:
    cns_values: list[str] = []
    seen_cns: set[str] = set()
    cpf: str | None = None

    for other_id in _find_children(patient, "asOtherIDs"):
        identifier = _find_child(other_id, "id")
        if identifier is None:
            continue

        root = identifier.attrib.get("root")
        extension = identifier.attrib.get("extension")
        if not extension:
            continue

        if root == CNS_DOCUMENT_ROOT and extension not in seen_cns:
            seen_cns.add(extension)
            cns_values.append(extension)
        elif root == CPF_DOCUMENT_ROOT:
            cpf = extension

    primary_cns = cns_values[0] if cns_values else None
    return cns_values, primary_cns, cpf


def _extract_phone(patient: ET.Element) -> str | None:
    for telecom in _find_children(patient, "telecom"):
        raw_value = telecom.attrib.get("value") or _normalized_text(telecom.text)
        if formatted_phone := _format_phone(raw_value):
            return formatted_phone
    return None


def _format_phone(raw_value: str | None) -> str | None:
    if not raw_value:
        return None

    digits = re.sub(r"\D", "", raw_value)
    if digits.startswith("55") and len(digits) in {12, 13}:
        digits = digits[2:]

    if len(digits) not in {10, 11}:
        return None

    ddd, number = digits[:2], digits[2:]
    return f"({ddd}){number[:-4]}-{number[-4:]}"


def _extract_mother_name(patient: ET.Element) -> str | None:
    fallback_name: str | None = None

    for relationship in _find_children(patient, "personalRelationship"):
        name = _extract_given_name(_find_path(relationship, "relationshipHolder1", "name"))
        if not name:
            continue

        relationship_code = _attribute_from_child(relationship, "code", "code")
        if relationship_code == "MTH":
            return name
        if fallback_name is None:
            fallback_name = name

    return fallback_name


def _extract_given_name(name_element: ET.Element | None) -> str | None:
    if name_element is None:
        return None

    given_names = [
        value
        for child in name_element
        if _local_name(child.tag) == "given"
        if (value := _normalized_text(child.text))
    ]
    if given_names:
        return " ".join(given_names)

    return _normalized_text(name_element.text)


def _format_cadsus_date(value: str) -> str | None:
    try:
        return datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _format_cadsus_datetime(value: str) -> str | None:
    for date_format in ("%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format).isoformat()
        except ValueError:
            continue
    return None


def _attribute_from_child(element: ET.Element, child_name: str, attribute_name: str) -> str | None:
    child = _find_child(element, child_name)
    if child is None:
        return None
    return child.attrib.get(attribute_name)


def _first_child_text(element: ET.Element, child_name: str) -> str | None:
    child = _find_child(element, child_name)
    if child is None:
        return None
    return _normalized_text(child.text)


def _find_path(element: ET.Element, *names: str) -> ET.Element | None:
    current = element
    for name in names:
        current = _find_child(current, name)
        if current is None:
            return None
    return current


def _find_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
