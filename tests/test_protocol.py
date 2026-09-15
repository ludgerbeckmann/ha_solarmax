"""Tests for the pure MaxComm codec."""

import pytest

from custom_components.solarmax.protocol import (
    DEVICE_FIELDS,
    FIELD_MAP_INVERTER,
    ProtocolError,
    RetryableProtocolError,
    build_request,
    calculate_checksum,
    parse_response,
    scale_value,
)


def test_build_request():
    """Test request building with a specific address and field list."""
    request = build_request(1, ["PAC", "SYS"])
    assert request.startswith("{FB;01;")
    assert "|64:PAC;SYS|" in request
    assert request.endswith("}")
    payload = request[1 : request.rindex("|") + 1]
    assert request[-5:-1] == calculate_checksum(payload)


def test_build_request_custom_address():
    """Test request building with a custom inverter address."""
    request = build_request(2, ["PAC"])
    assert request.startswith("{FB;02;")
    assert "PAC" in request
    assert request.endswith("}")


def test_build_request_overflow_guard():
    """Test that build_request raises when request exceeds 255 bytes."""
    huge_fields = [f"K{i:03d}" for i in range(100)]
    with pytest.raises(ProtocolError, match="Request too large"):
        build_request(1, huge_fields)


def test_build_request_within_limit():
    """Test that build_request succeeds for FIELD_MAP_INVERTER (under 255 bytes)."""
    request = build_request(1, list(FIELD_MAP_INVERTER))
    assert len(request) <= 255
    assert request.startswith("{")
    assert request.endswith("}")


def test_build_request_device_info():
    """Test that build_request succeeds for the device-info field set."""
    request = build_request(1, DEVICE_FIELDS)
    assert len(request) <= 255
    assert "TYP" in request
    assert "SWV" in request
    assert "DIN" in request
    assert "BDN" in request


def test_calculate_checksum():
    """Test checksum calculation."""
    data = "FB;01;3A|64:PAC|"
    checksum = calculate_checksum(data)

    assert isinstance(checksum, str)
    assert len(checksum) == 4
    # Sum of ASCII values of the data, as 4-digit uppercase hex
    assert checksum == format(sum(ord(c) for c in data), "04X")


def test_scale_value():
    """Test data value scaling."""
    # Test power values (divided by 2)
    assert scale_value("PAC", 3000) == 1500.0
    assert scale_value("PDC", 2000) == 1000.0

    # Test voltage values (divided by 10)
    assert scale_value("UL1", 2300) == 230.0
    assert scale_value("UDC", 4000) == 400.0

    # Test current values (divided by 100)
    assert scale_value("IDC", 650) == 6.5
    assert scale_value("IL1", 1050) == 10.5

    # Test status values (unchanged)
    assert scale_value("SYS", 20019) == 20019
    assert scale_value("SAL", 0) == 0

    # Test daily energy values (divided by 10, 0.1 kWh/digit)
    assert scale_value("KDY", 1234) == 123.4
    assert scale_value("KLD", 85) == 8.5

    # Test frequency values (divided by 100, 0.01 Hz/digit)
    assert scale_value("TNF", 5000) == 50.0

    # Raw 1:1 fields stay int (no scaling): monthly energy, temperature, hours
    assert scale_value("KMT", 350) == 350
    assert scale_value("TKK", 42) == 42
    assert isinstance(scale_value("KMT", 350), int)


def test_parse_response_roundtrip():
    """Basic single-field roundtrip through parse_response."""
    frame_payload = "01;FB;18|64:PAC=BB8|"
    frame = "{" + frame_payload + calculate_checksum(frame_payload) + "}"
    result = parse_response(frame)
    assert result["PAC"]["raw_value"] == 3000
    assert result["PAC"]["value"] == 1500.0


def test_parse_response_bad_crc_is_retryable():
    """A corrupted CRC is a transient error, not fatal."""
    with pytest.raises(RetryableProtocolError):
        parse_response("{01;FB;18|64:PAC=BB8|0000}")


def test_parse_response_ipr_is_fatal():
    """An IPR (invalid protocol) response is a deterministic, fatal error."""
    payload = "01;FB;16|3E8:IPR|"
    with pytest.raises(ProtocolError):
        parse_response("{" + payload + calculate_checksum(payload) + "}")


def test_parse_response():
    """Test response parsing."""
    inner = "01;FB;1F|64:PAC=BB8;SYS=4E33,0;SAL=0|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    result = parse_response(response)

    assert isinstance(result, dict)
    assert "PAC" in result
    assert "SYS" in result
    assert "SAL" in result

    # Check value conversion
    assert result["PAC"]["value"] == 1500.0  # 3000 / 2
    assert result["SYS"]["value"] == 20019  # 0x4E33
    assert result["SAL"]["value"] == 0  # 0x0


def test_parse_response_invalid_response():
    """Test conversion of invalid/unframed data raises RetryableProtocolError."""
    with pytest.raises(RetryableProtocolError):
        parse_response("invalid_response")


def test_parse_response_rejects_non_maxcomm_frame_without_checksum():
    """Ignoring CRC must not accept an unrelated brace-delimited payload."""
    with pytest.raises(RetryableProtocolError, match="frame structure"):
        parse_response('{"error":"unknown command"}', verify_checksum=False)


def test_parse_response_can_ignore_bad_checksum_on_valid_frame():
    """Ignoring CRC still accepts a structurally valid MaxComm frame."""
    result = parse_response("{01;FB;18|64:PAC=BB8|0000}", verify_checksum=False)
    assert result["PAC"]["value"] == 1500.0


def test_parse_response_valid_crc():
    """Test response with valid CRC is parsed successfully."""
    # Build a response with correct CRC
    inner = "01;FB;1A|64:PAC=1F4;UDC=BB8|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    result = parse_response(response)

    assert "PAC" in result
    assert result["PAC"]["value"] == 250.0  # 500 / 2
    assert result["UDC"]["value"] == 300.0  # 3000 / 10


def test_parse_response_invalid_crc():
    """Test response with invalid CRC raises RetryableProtocolError."""
    # Use a response with wrong CRC
    response = "{01;FB;1A|64:PAC=1F4;UDC=BB8|0000}"
    with pytest.raises(RetryableProtocolError, match="checksum verification failed"):
        parse_response(response)


def test_parse_response_ipr_error():
    """Test IPR (invalid protocol) error response raises ProtocolError."""
    inner = "01;FB;0E|3E8:IPR|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    with pytest.raises(ProtocolError, match="invalid protocol"):
        parse_response(response)


def test_parse_response_ipn_error():
    """Test IPN (invalid port) error response raises ProtocolError."""
    inner = "01;FB;0E|3E8:IPN|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    with pytest.raises(ProtocolError, match="invalid port"):
        parse_response(response)


def test_parse_response_ipr_zero_padded_port():
    """Test IPR detection with zero-padded port (03E8)."""
    inner = "01;FB;0F|03E8:IPR|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    with pytest.raises(ProtocolError, match="invalid protocol"):
        parse_response(response)


def test_parse_response_typ_swv():
    """Test parsing of TYP and SWV keys."""
    # TYP=20650 (0x50AA), SWV=40 (0x28)
    inner = "01;FB;1A|64:TYP=50AA;SWV=28|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    result = parse_response(response)

    assert result["TYP"]["value"] == 20650
    assert result["TYP"]["raw_value"] == 0x50AA
    assert result["SWV"]["value"] == 40
    assert result["SWV"]["raw_value"] == 0x28


def test_parse_response_not_applicable_key():
    """Test 'not applicable' keys (no '=' sign) are skipped gracefully."""
    # PAC has a value, UDC is "not applicable" (no '=')
    inner = "01;FB;1A|64:PAC=1F4;UDC|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"
    result = parse_response(response)

    assert "PAC" in result
    assert "UDC" not in result  # Not applicable keys are skipped


def test_parse_response_multi_frame():
    """Test parsing of multi-frame responses (continuation frames end with ')')."""
    # Build a realistic multi-frame response like a real inverter sends
    # Frame 1 ends with ')' (continuation), Frame 2 ends with '}' (final)
    inner1 = "01;FB;FF|64:PAC=2FA;UDC=D23;IDC=89;UL1=91C;UL2=8F0;UL3=8FD;IL1=84;IL2=82;IL3=87;KDY=D;KMT=102;KYR=602;KT0=9225;KHR=7848;TKK=24;SYS=4E28,0;SAL=0;TYP=50AA;DIN=9973BB;BDN=391;PIN=41A0;PRL=4;ULH=A55;ULL=730;TNH=141E;TNL=128E;PDC=340;PD01=1BE;PD02=182;U|"  # noqa: E501
    crc1 = calculate_checksum(inner1)
    frame1 = "{" + inner1 + crc1 + ")"  # Continuation frame ends with ')'

    inner2 = (
        "01;FB;53|D01=D23;UD02=AA0;ID01=43;ID02=46;KLM=294;KLY=11DF;TNF=138A;CAC=193D|"
    )
    crc2 = calculate_checksum(inner2)
    frame2 = "{" + inner2 + crc2 + "}"  # Final frame ends with '}'

    response = frame1 + frame2
    result = parse_response(response)

    # Field split across frames: "U" + "D01=D23" -> "UD01"
    assert "UD01" in result
    assert result["UD01"]["raw_value"] == 0xD23

    # Fields from first frame
    assert "PAC" in result
    assert result["PAC"]["value"] == 0x2FA / 2

    # Fields from second frame
    assert "CAC" in result
    assert result["CAC"]["raw_value"] == 0x193D
    assert "TNF" in result
    assert result["TNF"]["value"] == 0x138A / 100.0


def test_parse_response_skips_malformed_field():
    """A malformed field must not discard other valid fields."""
    inner = "01;FB;30|64:PAC=BB8;KDY=;SYS=4E33,0;SAL=0|"
    crc = calculate_checksum(inner)
    response = "{" + inner + crc + "}"

    result = parse_response(response)

    assert set(result) == {"PAC", "SYS", "SAL"}
    assert result["PAC"]["raw_value"] == 3000
    assert result["SYS"]["raw_value"] == 0x4E33
    assert result["SAL"]["raw_value"] == 0
