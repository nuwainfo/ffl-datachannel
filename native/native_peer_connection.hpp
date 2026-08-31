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
#pragma once

#include "event_sink.hpp"

#include <rtc/rtc.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>

namespace ffl::datachannel {

struct DataChannelOptions {
    bool ordered = true;
    bool negotiated = false;
    bool hasMaxPacketLifeTime = false;
    bool hasMaxRetransmits = false;
    bool hasStream = false;
    unsigned int maxPacketLifeTime = 0;
    unsigned int maxRetransmits = 0;
    std::uint16_t stream = 0;
    std::string protocol;
};

class NativePeerConnection final {
public:
    NativePeerConnection(EventSink &sink, const std::vector<std::string> &iceServers);
    ~NativePeerConnection();

    NativePeerConnection(const NativePeerConnection &) = delete;
    NativePeerConnection &operator=(const NativePeerConnection &) = delete;

    std::string createOffer() const;
    std::string createAnswer() const;
    void setLocalDescription(const std::string &type) const;
    void setRemoteDescription(const std::string &sdp, const std::string &type) const;
    void addRemoteCandidate(const std::string &candidate, const std::string &mid) const;

    int createDataChannel(const std::string &label, const DataChannelOptions &options);
    void sendText(int channelId, const std::string &message) const;
    void sendBinary(int channelId, const char *data, std::size_t size) const;
    void closeChannel(int channelId) const;
    int getDataChannelStream(int channelId) const;
    int getBufferedAmount(int channelId) const;
    void setBufferedAmountLowThreshold(int channelId, int amount) const;

    void close();

private:
    static void ensureRuntimeReady();
    static void requireSuccess(const char *operation, int result);
    static std::string getString(int id, int (*getter)(int, char *, int));
    static std::string createDescription(int pc, int (*creator)(int, char *, int));

    static void RTC_API localDescriptionCallback(int pc, const char *sdp, const char *type, void *ptr);
    static void RTC_API localCandidateCallback(int pc, const char *candidate, const char *mid, void *ptr);
    static void RTC_API stateCallback(int pc, rtcState state, void *ptr);
    static void RTC_API iceStateCallback(int pc, rtcIceState state, void *ptr);
    static void RTC_API gatheringStateCallback(int pc, rtcGatheringState state, void *ptr);
    static void RTC_API signalingStateCallback(int pc, rtcSignalingState state, void *ptr);
    static void RTC_API dataChannelCallback(int pc, int dc, void *ptr);
    static void RTC_API channelOpenCallback(int dc, void *ptr);
    static void RTC_API channelClosedCallback(int dc, void *ptr);
    static void RTC_API channelErrorCallback(int dc, const char *error, void *ptr);
    static void RTC_API channelMessageCallback(int dc, const char *message, int size, void *ptr);
    static void RTC_API channelBufferedAmountLowCallback(int dc, void *ptr);

    DataChannelInfo readDataChannelInfo(int channelId) const;
    void registerDataChannel(int channelId);
    bool canEmit() const;

    EventSink &sink_;
    int pc_ = -1;
    std::atomic<bool> closing_{false};
    mutable std::mutex channelMutex_;
    std::unordered_set<int> channelIds_;
};

} // namespace ffl::datachannel
