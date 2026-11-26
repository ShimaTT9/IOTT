-- Wireshark dissector for the IoT telemetry protocol (UDP port 5005).
--
-- Install (Ubuntu):
--   mkdir -p ~/.local/lib/wireshark/plugins
--   cp iot_dissector.lua ~/.local/lib/wireshark/plugins/
-- or run once:  wireshark -X lua_script:iot_dissector.lua capture.pcap
-- Display filter examples:  iot   iot.type == 1   iot.sensor_id == 2

local iot = Proto("iot", "IoT Telemetry Protocol")

local msg_types = { [0] = "DATA", [1] = "ACK" }

local f_type   = ProtoField.uint8("iot.type", "Type", base.DEC, msg_types)
local f_qos    = ProtoField.uint8("iot.qos", "QoS", base.DEC)
local f_sensor = ProtoField.uint16("iot.sensor_id", "Sensor ID", base.DEC)
local f_seq    = ProtoField.uint32("iot.seq", "Sequence", base.DEC)
local f_ts     = ProtoField.double("iot.timestamp", "Timestamp (s)")
local f_temp   = ProtoField.float("iot.temperature", "Temperature (C)")
local f_hum    = ProtoField.float("iot.humidity", "Humidity (%)")
local f_pad    = ProtoField.bytes("iot.padding", "Padding")

iot.fields = { f_type, f_qos, f_sensor, f_seq, f_ts, f_temp, f_hum, f_pad }

function iot.dissector(buffer, pinfo, tree)
    if buffer:len() < 16 then return 0 end
    pinfo.cols.protocol = "IoT"
    local mtype = buffer(0, 1):uint()
    local subtree = tree:add(iot, buffer(), "IoT Telemetry Protocol")
    subtree:add(f_type, buffer(0, 1))
    subtree:add(f_qos, buffer(1, 1))
    subtree:add(f_sensor, buffer(2, 2))
    subtree:add(f_seq, buffer(4, 4))
    subtree:add(f_ts, buffer(8, 8))
    if mtype == 0 and buffer:len() >= 48 then
        subtree:add(f_temp, buffer(16, 4))
        subtree:add(f_hum, buffer(20, 4))
        subtree:add(f_pad, buffer(24, 24))
    end
    pinfo.cols.info = string.format("%s qos=%d sensor=%d seq=%d",
        msg_types[mtype] or "?", buffer(1, 1):uint(), buffer(2, 2):uint(),
        buffer(4, 4):uint())
    return buffer:len()
end

DissectorTable.get("udp.port"):add(5005, iot)
