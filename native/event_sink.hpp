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

#include <cstddef>
#include <cstdint>
#include <string>

namespace ffl::datachannel {

struct DataChannelInfo {
    int id = -1;
    int stream = -1;
    std::string label;
    std::string protocol;
    bool ordered = true;
    bool unreliable = false;
    unsigned int maxPacketLifeTime = 0;
    unsigned int maxRetransmits = 0;
};

class EventSink {
public:
    virtual ~EventSink() = default;

    virtual void onLocalDescription(const std::string &sdp, const std::string &type) = 0;
    virtual void onLocalCandidate(const std::string &candidate, const std::string &mid) = 0;
    virtual void onConnectionState(int state) = 0;
    virtual void onIceState(int state) = 0;
    virtual void onGatheringState(int state) = 0;
    virtual void onSignalingState(int state) = 0;
    virtual void onDataChannel(const DataChannelInfo &info) = 0;
    virtual void onChannelOpen(int channelId) = 0;
    virtual void onChannelClosed(int channelId) = 0;
    virtual void onChannelError(int channelId, const std::string &message) = 0;
    virtual void onChannelText(int channelId, const std::string &message) = 0;
    virtual void onChannelBinary(int channelId, const std::uint8_t *data, std::size_t size) = 0;
    virtual void onChannelBufferedAmountLow(int channelId) = 0;
    virtual void onInternalError(const std::string &message) = 0;
};

} // namespace ffl::datachannel
