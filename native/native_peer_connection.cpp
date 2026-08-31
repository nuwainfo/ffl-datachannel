/**
SPDX-License-Identifier: Apache-2.0

FastFileLink CLI - Fast, no-fuss file sharing
Copyright (C) 2025-2026 FastFileLink contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
#include "native_peer_connection.hpp"

#include <rtc/rtc.h>

#include <cerrno>
#include <climits>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace ffl::datachannel {
namespace {

std::once_flag runtimeOnce;
// FastFileLink sends 256 KiB plaintext chunks.  E2EE adds a 31-byte
// authenticated frame header, so a native channel must accept that complete
// browser-compatible message while retaining SCTP's normal backpressure.
constexpr int MAXIMUM_DATA_CHANNEL_MESSAGE_SIZE = 256 * 1024 + 31;

bool readIntEnvironmentVariable(const char *name, int &output) {
    const char *value = std::getenv(name);
    if (!value || !*value) {
        return false;
    }

    errno = 0;
    char *end = nullptr;
    const long long parsed = std::strtoll(value, &end, 10);
    if (errno == ERANGE || end == value || *end != '\0' ||
        parsed < INT_MIN || parsed > INT_MAX) {
        throw std::runtime_error(
            std::string(name) + " must be an integer between " +
            std::to_string(INT_MIN) + " and " + std::to_string(INT_MAX)
        );
    }

    output = static_cast<int>(parsed);
    return true;
}

bool readNonNegativeIntEnvironmentVariable(const char *name, int &output) {
    if (!readIntEnvironmentVariable(name, output)) {
        return false;
    }
    if (output < 0) {
        throw std::runtime_error(std::string(name) + " cannot be negative");
    }
    return true;
}

std::string resultMessage(const char *operation, int result) {
    return std::string(operation) + " failed with libdatachannel result " + std::to_string(result);
}

std::string normalizeRemoteMaxMessageSize(std::string sdp) {
    constexpr const char attribute[] = "a=max-message-size:";
    std::size_t offset = 0;
    while ((offset = sdp.find(attribute, offset)) != std::string::npos) {
        const std::size_t valueBegin = offset + sizeof(attribute) - 1;
        const std::size_t valueEnd = sdp.find_first_of("\r\n", valueBegin);
        sdp.replace(valueBegin, valueEnd - valueBegin, "0");
        offset = valueBegin + 1;
    }
    return sdp;
}

} // namespace

NativePeerConnection::NativePeerConnection(EventSink &sink, const std::vector<std::string> &iceServers)
    : sink_(sink) {
    ensureRuntimeReady();

    std::vector<const char *> iceServerPointers;
    iceServerPointers.reserve(iceServers.size());
    for (const std::string &iceServer : iceServers) {
        iceServerPointers.push_back(iceServer.c_str());
    }

    rtcConfiguration config{};
    config.iceServers = iceServerPointers.empty() ? nullptr : iceServerPointers.data();
    config.iceServersCount = static_cast<int>(iceServerPointers.size());
    config.disableAutoNegotiation = true;

    // Optional per-PeerConnection MTU override for benchmark parity tests.
    int configuredMtu = 0;
    if (readNonNegativeIntEnvironmentVariable("FFL_DATACHANNEL_MTU", configuredMtu)) {
        config.mtu = configuredMtu;
    }
    // Keep one RTCDataChannel.send() call as one WebRTC message. libdatachannel's
    // 256 KiB default is too small for FastFileLink's 256 KiB E2EE frame plus
    // its authenticated header; SCTP performs packet-level fragmentation itself.
    config.maxMessageSize = MAXIMUM_DATA_CHANNEL_MESSAGE_SIZE;

    pc_ = rtcCreatePeerConnection(&config);
    requireSuccess("rtcCreatePeerConnection", pc_ < 0 ? pc_ : RTC_ERR_SUCCESS);

    try {
        rtcSetUserPointer(pc_, this);
        requireSuccess("rtcSetLocalDescriptionCallback", rtcSetLocalDescriptionCallback(pc_, localDescriptionCallback));
        requireSuccess("rtcSetLocalCandidateCallback", rtcSetLocalCandidateCallback(pc_, localCandidateCallback));
        requireSuccess("rtcSetStateChangeCallback", rtcSetStateChangeCallback(pc_, stateCallback));
        requireSuccess("rtcSetIceStateChangeCallback", rtcSetIceStateChangeCallback(pc_, iceStateCallback));
        requireSuccess("rtcSetGatheringStateChangeCallback", rtcSetGatheringStateChangeCallback(pc_, gatheringStateCallback));
        requireSuccess("rtcSetSignalingStateChangeCallback", rtcSetSignalingStateChangeCallback(pc_, signalingStateCallback));
        requireSuccess("rtcSetDataChannelCallback", rtcSetDataChannelCallback(pc_, dataChannelCallback));
    } catch (...) {
        closing_ = true;
        rtcDeletePeerConnection(pc_);
        pc_ = -1;
        throw;
    }
}

NativePeerConnection::~NativePeerConnection() {
    close();
}

void NativePeerConnection::ensureRuntimeReady() {
    std::call_once(runtimeOnce, [] {
        rtcSctpSettings sctpSettings{};
        bool hasCustomSctpSettings = false;

        hasCustomSctpSettings |= readNonNegativeIntEnvironmentVariable(
            "FFL_DATACHANNEL_SCTP_RECV_BUFFER_SIZE",
            sctpSettings.recvBufferSize
        );
        hasCustomSctpSettings |= readNonNegativeIntEnvironmentVariable(
            "FFL_DATACHANNEL_SCTP_SEND_BUFFER_SIZE",
            sctpSettings.sendBufferSize
        );

        // libdatachannel semantics:
        //   unset -> leave optimized default untouched
        //       0 -> optimized default (currently 10 MTUs)
        //      <0 -> disable the max-burst limiter
        //      >0 -> explicit maximum burst in MTUs
        //
        // This experiment intentionally does NOT change
        // initialCongestionWindow or any other SCTP tuning knob.
        hasCustomSctpSettings |= readIntEnvironmentVariable(
            "FFL_DATACHANNEL_SCTP_MAX_BURST",
            sctpSettings.maxBurst
        );

        if (hasCustomSctpSettings) {
            const int result = rtcSetSctpSettings(&sctpSettings);
            if (result < 0) {
                throw std::runtime_error(resultMessage("rtcSetSctpSettings", result));
            }
        }

        rtcPreload();
    });
}

void NativePeerConnection::requireSuccess(const char *operation, int result) {
    if (result < 0) {
        throw std::runtime_error(resultMessage(operation, result));
    }
}

std::string NativePeerConnection::getString(int id, int (*getter)(int, char *, int)) {
    const int requiredSize = getter(id, nullptr, 0);
    requireSuccess("libdatachannel string size query", requiredSize);
    if (requiredSize <= 1) {
        return {};
    }

    std::vector<char> buffer(static_cast<std::size_t>(requiredSize));
    const int result = getter(id, buffer.data(), requiredSize);
    requireSuccess("libdatachannel string query", result);
    return std::string(buffer.data());
}

std::string NativePeerConnection::createDescription(int pc, int (*creator)(int, char *, int)) {
    // rtcCreateOffer/rtcCreateAnswer generate a new SDP on every call, so the
    // usual null-buffer size probe would create one SDP and then copy another.
    std::vector<char> buffer(64 * 1024);
    const int result = creator(pc, buffer.data(), static_cast<int>(buffer.size()));
    requireSuccess("libdatachannel description creation", result);
    return std::string(buffer.data(), static_cast<std::size_t>(result - 1));
}

std::string NativePeerConnection::createOffer() const {
    return createDescription(pc_, rtcCreateOffer);
}

std::string NativePeerConnection::createAnswer() const {
    return createDescription(pc_, rtcCreateAnswer);
}

std::string NativePeerConnection::getLocalDescription() const {
    return getString(pc_, rtcGetLocalDescription);
}

void NativePeerConnection::setLocalDescription(const std::string &type) const {
    requireSuccess("rtcSetLocalDescription", rtcSetLocalDescription(pc_, type.c_str()));
}

void NativePeerConnection::setRemoteDescription(const std::string &sdp, const std::string &type) const {
    // aiortc lets SCTP fragment an application message even when browsers
    // advertise their conservative 256 KiB receive value. Do the same here so
    // RTCDataChannel.send() preserves its one-call, one-message API contract.
    const std::string normalizedSdp = normalizeRemoteMaxMessageSize(sdp);
    requireSuccess("rtcSetRemoteDescription", rtcSetRemoteDescription(pc_, normalizedSdp.c_str(), type.c_str()));
}

void NativePeerConnection::addRemoteCandidate(const std::string &candidate, const std::string &mid) const {
    const char *midPointer = mid.empty() ? nullptr : mid.c_str();
    requireSuccess("rtcAddRemoteCandidate", rtcAddRemoteCandidate(pc_, candidate.c_str(), midPointer));
}

int NativePeerConnection::createDataChannel(const std::string &label, const DataChannelOptions &options) {
    if (options.hasMaxPacketLifeTime && options.hasMaxRetransmits) {
        throw std::invalid_argument("maxPacketLifeTime and maxRetransmits are mutually exclusive");
    }

    rtcDataChannelInit init{};
    init.reliability.unordered = !options.ordered;
    init.reliability.unreliable = options.hasMaxPacketLifeTime || options.hasMaxRetransmits;
    init.reliability.maxPacketLifeTime = options.maxPacketLifeTime;
    init.reliability.maxRetransmits = options.maxRetransmits;
    init.protocol = options.protocol.c_str();
    init.negotiated = options.negotiated;
    init.manualStream = options.hasStream;
    init.stream = options.stream;

    const int channelId = rtcCreateDataChannelEx(pc_, label.c_str(), &init);
    requireSuccess("rtcCreateDataChannelEx", channelId < 0 ? channelId : RTC_ERR_SUCCESS);
    try {
        registerDataChannel(channelId);
    } catch (...) {
        rtcDeleteDataChannel(channelId);
        throw;
    }
    return channelId;
}

void NativePeerConnection::sendText(int channelId, const std::string &message) const {
    requireSuccess("rtcSendMessage(text)", rtcSendMessage(channelId, message.c_str(), -1));
}

void NativePeerConnection::sendBinary(int channelId, const char *data, std::size_t size) const {
    if (size > static_cast<std::size_t>(INT_MAX)) {
        throw std::overflow_error("DataChannel message exceeds libdatachannel C API size limit");
    }
    const char *message = size == 0 ? "" : data;
    requireSuccess("rtcSendMessage(binary)", rtcSendMessage(channelId, message, static_cast<int>(size)));
}

void NativePeerConnection::closeChannel(int channelId) const {
    requireSuccess("rtcClose(DataChannel)", rtcClose(channelId));
}

int NativePeerConnection::getDataChannelStream(int channelId) const {
    const int stream = rtcGetDataChannelStream(channelId);
    requireSuccess("rtcGetDataChannelStream", stream);
    return stream;
}

int NativePeerConnection::getBufferedAmount(int channelId) const {
    const int amount = rtcGetBufferedAmount(channelId);
    requireSuccess("rtcGetBufferedAmount", amount);
    return amount;
}

void NativePeerConnection::setBufferedAmountLowThreshold(int channelId, int amount) const {
    if (amount < 0) {
        throw std::invalid_argument("bufferedAmountLowThreshold cannot be negative");
    }
    requireSuccess("rtcSetBufferedAmountLowThreshold", rtcSetBufferedAmountLowThreshold(channelId, amount));
}

void NativePeerConnection::close() {
    if (closing_.exchange(true)) {
        return;
    }

    std::vector<int> channelIds;
    {
        std::lock_guard<std::mutex> lock(channelMutex_);
        channelIds.assign(channelIds_.begin(), channelIds_.end());
        channelIds_.clear();
    }

    for (const int channelId : channelIds) {
        rtcDeleteDataChannel(channelId);
    }

    if (pc_ >= 0) {
        rtcDeletePeerConnection(pc_);
        pc_ = -1;
    }
}

DataChannelInfo NativePeerConnection::readDataChannelInfo(int channelId) const {
    DataChannelInfo info;
    info.id = channelId;
    info.stream = rtcGetDataChannelStream(channelId);
    requireSuccess("rtcGetDataChannelStream", info.stream);
    info.label = getString(channelId, rtcGetDataChannelLabel);
    info.protocol = getString(channelId, rtcGetDataChannelProtocol);

    rtcReliability reliability{};
    requireSuccess("rtcGetDataChannelReliability", rtcGetDataChannelReliability(channelId, &reliability));
    info.ordered = !reliability.unordered;
    info.unreliable = reliability.unreliable;
    info.maxPacketLifeTime = reliability.maxPacketLifeTime;
    info.maxRetransmits = reliability.maxRetransmits;
    return info;
}

void NativePeerConnection::registerDataChannel(int channelId) {
    rtcSetUserPointer(channelId, this);
    requireSuccess("rtcSetOpenCallback", rtcSetOpenCallback(channelId, channelOpenCallback));
    requireSuccess("rtcSetClosedCallback", rtcSetClosedCallback(channelId, channelClosedCallback));
    requireSuccess("rtcSetErrorCallback", rtcSetErrorCallback(channelId, channelErrorCallback));
    requireSuccess("rtcSetMessageCallback", rtcSetMessageCallback(channelId, channelMessageCallback));
    requireSuccess("rtcSetBufferedAmountLowCallback", rtcSetBufferedAmountLowCallback(channelId, channelBufferedAmountLowCallback));

    std::lock_guard<std::mutex> lock(channelMutex_);
    channelIds_.insert(channelId);
}

bool NativePeerConnection::canEmit() const {
    return !closing_.load();
}

void RTC_API NativePeerConnection::localDescriptionCallback(int, const char *sdp, const char *type, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (!self || !self->canEmit() || !sdp || !type) {
        return;
    }
    self->sink_.onLocalDescription(sdp, type);
}

void RTC_API NativePeerConnection::localCandidateCallback(int, const char *candidate, const char *mid, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (!self || !self->canEmit() || !candidate) {
        return;
    }
    self->sink_.onLocalCandidate(candidate, mid ? mid : "");
}

void RTC_API NativePeerConnection::stateCallback(int, rtcState state, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onConnectionState(static_cast<int>(state));
    }
}

void RTC_API NativePeerConnection::iceStateCallback(int, rtcIceState state, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onIceState(static_cast<int>(state));
    }
}

void RTC_API NativePeerConnection::gatheringStateCallback(int, rtcGatheringState state, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onGatheringState(static_cast<int>(state));
    }
}

void RTC_API NativePeerConnection::signalingStateCallback(int, rtcSignalingState state, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onSignalingState(static_cast<int>(state));
    }
}

void RTC_API NativePeerConnection::dataChannelCallback(int, int dc, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (!self || !self->canEmit()) {
        return;
    }

    try {
        self->registerDataChannel(dc);
        self->sink_.onDataChannel(self->readDataChannelInfo(dc));
    } catch (const std::exception &error) {
        rtcDeleteDataChannel(dc);
        self->sink_.onInternalError(error.what());
    }
}

void RTC_API NativePeerConnection::channelOpenCallback(int dc, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onChannelOpen(dc);
    }
}

void RTC_API NativePeerConnection::channelClosedCallback(int dc, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onChannelClosed(dc);
    }
}

void RTC_API NativePeerConnection::channelErrorCallback(int dc, const char *error, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onChannelError(dc, error ? error : "Unknown DataChannel error");
    }
}

void RTC_API NativePeerConnection::channelMessageCallback(int dc, const char *message, int size, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (!self || !self->canEmit()) {
        return;
    }

    if (size < 0) {
        if (message) {
            self->sink_.onChannelText(dc, message);
        }
        return;
    }

    self->sink_.onChannelBinary(
        dc,
        reinterpret_cast<const std::uint8_t *>(message),
        static_cast<std::size_t>(size)
    );
}

void RTC_API NativePeerConnection::channelBufferedAmountLowCallback(int dc, void *ptr) {
    auto *self = static_cast<NativePeerConnection *>(ptr);
    if (self && self->canEmit()) {
        self->sink_.onChannelBufferedAmountLow(dc);
    }
}

} // namespace ffl::datachannel
