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
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "native_peer_connection.hpp"

#include <cstdint>
#include <exception>
#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace ffl::datachannel {
namespace {

constexpr const char *CAPSULE_NAME = "ffl_datachannel.NativePeerConnection";

PyObject *nativeErrorType = nullptr;

enum class EventType : int {
    LocalDescription = 1,
    LocalCandidate = 2,
    ConnectionState = 3,
    IceState = 4,
    GatheringState = 5,
    SignalingState = 6,
    DataChannel = 7,
    ChannelOpen = 8,
    ChannelClosed = 9,
    ChannelError = 10,
    ChannelText = 11,
    ChannelBinary = 12,
    ChannelBufferedAmountLow = 13,
    InternalError = 14,
};

class GILGuard final {
public:
    GILGuard() : state_(PyGILState_Ensure()) {}
    ~GILGuard() { PyGILState_Release(state_); }

    GILGuard(const GILGuard &) = delete;
    GILGuard &operator=(const GILGuard &) = delete;

private:
    PyGILState_STATE state_;
};

template <typename Function>
auto runWithoutGIL(Function &&function) -> decltype(function()) {
    PyThreadState *threadState = PyEval_SaveThread();
    try {
        if constexpr (std::is_void_v<decltype(function())>) {
            function();
            PyEval_RestoreThread(threadState);
            return;
        } else {
            auto result = function();
            PyEval_RestoreThread(threadState);
            return result;
        }
    } catch (...) {
        PyEval_RestoreThread(threadState);
        throw;
    }
}

class PythonPeerBridge final : public EventSink {
public:
    PythonPeerBridge(PyObject *callback, std::vector<std::string> iceServers)
        : callback_(callback) {
        if (!PyCallable_Check(callback_)) {
            throw std::invalid_argument("native event callback must be callable");
        }

        Py_INCREF(callback_);
        try {
            peer_ = std::make_unique<NativePeerConnection>(*this, iceServers);
        } catch (...) {
            Py_DECREF(callback_);
            callback_ = nullptr;
            throw;
        }
    }

    ~PythonPeerBridge() override {
        if (peer_) {
            runWithoutGIL([this] {
                peer_->close();
            });
            peer_.reset();
        }
        Py_XDECREF(callback_);
    }

    NativePeerConnection &peer() {
        return *peer_;
    }

    void onLocalDescription(const std::string &sdp, const std::string &type) override {
        GILGuard guard;
        emit(EventType::LocalDescription, Py_BuildValue("(ss)", sdp.c_str(), type.c_str()));
    }

    void onLocalCandidate(const std::string &candidate, const std::string &mid) override {
        GILGuard guard;
        emit(EventType::LocalCandidate, Py_BuildValue("(ss)", candidate.c_str(), mid.c_str()));
    }

    void onConnectionState(int state) override {
        emitState(EventType::ConnectionState, state);
    }

    void onIceState(int state) override {
        emitState(EventType::IceState, state);
    }

    void onGatheringState(int state) override {
        emitState(EventType::GatheringState, state);
    }

    void onSignalingState(int state) override {
        emitState(EventType::SignalingState, state);
    }

    void onDataChannel(const DataChannelInfo &info) override {
        GILGuard guard;
        emit(
            EventType::DataChannel,
            Py_BuildValue(
                "(iissiiII)",
                info.id,
                info.stream,
                info.label.c_str(),
                info.protocol.c_str(),
                info.ordered ? 1 : 0,
                info.unreliable ? 1 : 0,
                info.maxPacketLifeTime,
                info.maxRetransmits
            )
        );
    }

    void onChannelOpen(int channelId) override {
        emitChannelId(EventType::ChannelOpen, channelId);
    }

    void onChannelClosed(int channelId) override {
        emitChannelId(EventType::ChannelClosed, channelId);
    }

    void onChannelError(int channelId, const std::string &message) override {
        GILGuard guard;
        emit(EventType::ChannelError, Py_BuildValue("(is)", channelId, message.c_str()));
    }

    void onChannelText(int channelId, const std::string &message) override {
        GILGuard guard;
        emit(EventType::ChannelText, Py_BuildValue("(is)", channelId, message.c_str()));
    }

    void onChannelBinary(int channelId, const std::uint8_t *data, std::size_t size) override {
        GILGuard guard;
        const Py_ssize_t pythonSize = static_cast<Py_ssize_t>(size);
        const char *bytes = size == 0 ? "" : reinterpret_cast<const char *>(data);
        emit(
            EventType::ChannelBinary,
            Py_BuildValue("(iy#)", channelId, bytes, pythonSize)
        );
    }

    void onChannelBufferedAmountLow(int channelId) override {
        emitChannelId(EventType::ChannelBufferedAmountLow, channelId);
    }

    void onInternalError(const std::string &message) override {
        GILGuard guard;
        emit(EventType::InternalError, Py_BuildValue("(s)", message.c_str()));
    }

private:
    void emitState(EventType eventType, int state) {
        GILGuard guard;
        emit(eventType, Py_BuildValue("(i)", state));
    }

    void emitChannelId(EventType eventType, int channelId) {
        GILGuard guard;
        emit(eventType, Py_BuildValue("(i)", channelId));
    }

    void emit(EventType eventType, PyObject *payload) {
        if (!payload) {
            PyErr_WriteUnraisable(callback_);
            return;
        }

        PyObject *result = PyObject_CallFunction(callback_, "iO", static_cast<int>(eventType), payload);
        Py_DECREF(payload);
        if (!result) {
            PyErr_WriteUnraisable(callback_);
            return;
        }
        Py_DECREF(result);
    }

    PyObject *callback_ = nullptr;
    std::unique_ptr<NativePeerConnection> peer_;
};

PythonPeerBridge *getBridge(PyObject *capsule) {
    return static_cast<PythonPeerBridge *>(PyCapsule_GetPointer(capsule, CAPSULE_NAME));
}

void capsuleDestructor(PyObject *capsule) {
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        PyErr_Clear();
        return;
    }
    delete bridge;
}

PyObject *translateCurrentException() {
    try {
        throw;
    } catch (const std::exception &error) {
        PyErr_SetString(nativeErrorType, error.what());
    } catch (...) {
        PyErr_SetString(nativeErrorType, "Unknown native ffl_datachannel error");
    }
    return nullptr;
}

std::vector<std::string> parseIceServers(PyObject *iceServersObject) {
    PyObject *sequence = PySequence_Fast(iceServersObject, "ice_servers must be a sequence of strings");
    if (!sequence) {
        throw std::invalid_argument("ice_servers must be a sequence of strings");
    }

    std::vector<std::string> iceServers;
    const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    iceServers.reserve(static_cast<std::size_t>(count));

    for (Py_ssize_t index = 0; index < count; ++index) {
        PyObject *item = PySequence_Fast_GET_ITEM(sequence, index);
        if (!PyUnicode_Check(item)) {
            Py_DECREF(sequence);
            throw std::invalid_argument("ice_servers entries must be strings");
        }

        const char *value = PyUnicode_AsUTF8(item);
        if (!value) {
            Py_DECREF(sequence);
            throw std::runtime_error("Unable to decode ICE server as UTF-8");
        }
        iceServers.emplace_back(value);
    }

    Py_DECREF(sequence);
    return iceServers;
}

PyObject *createPeerConnection(PyObject *, PyObject *args) {
    PyObject *iceServersObject = nullptr;
    PyObject *callback = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &iceServersObject, &callback)) {
        return nullptr;
    }

    try {
        std::vector<std::string> iceServers = parseIceServers(iceServersObject);
        auto *bridge = new PythonPeerBridge(callback, std::move(iceServers));
        PyObject *capsule = PyCapsule_New(bridge, CAPSULE_NAME, capsuleDestructor);
        if (!capsule) {
            delete bridge;
            return nullptr;
        }
        return capsule;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *closePeerConnection(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return nullptr;
    }

    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        runWithoutGIL([bridge] {
            bridge->peer().close();
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *createDescription(PyObject *args, bool offer) {
    PyObject *capsule = nullptr;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const std::string sdp = runWithoutGIL([bridge, offer] {
            return offer ? bridge->peer().createOffer() : bridge->peer().createAnswer();
        });
        return PyUnicode_FromStringAndSize(sdp.data(), static_cast<Py_ssize_t>(sdp.size()));
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *createOffer(PyObject *, PyObject *args) {
    return createDescription(args, true);
}

PyObject *createAnswer(PyObject *, PyObject *args) {
    return createDescription(args, false);
}

PyObject *getLocalDescription(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const std::string sdp = runWithoutGIL([bridge] {
            return bridge->peer().getLocalDescription();
        });
        return PyUnicode_FromStringAndSize(sdp.data(), static_cast<Py_ssize_t>(sdp.size()));
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *setLocalDescription(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    const char *type = nullptr;
    if (!PyArg_ParseTuple(args, "Os", &capsule, &type)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const std::string typeCopy(type);
        runWithoutGIL([bridge, &typeCopy] {
            bridge->peer().setLocalDescription(typeCopy);
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *setRemoteDescription(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    const char *sdp = nullptr;
    const char *type = nullptr;
    if (!PyArg_ParseTuple(args, "Oss", &capsule, &sdp, &type)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const std::string sdpCopy(sdp);
        const std::string typeCopy(type);
        runWithoutGIL([bridge, &sdpCopy, &typeCopy] {
            bridge->peer().setRemoteDescription(sdpCopy, typeCopy);
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *addRemoteCandidate(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    const char *candidate = nullptr;
    const char *mid = nullptr;
    if (!PyArg_ParseTuple(args, "Oss", &capsule, &candidate, &mid)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const std::string candidateCopy(candidate);
        const std::string midCopy(mid);
        runWithoutGIL([bridge, &candidateCopy, &midCopy] {
            bridge->peer().addRemoteCandidate(candidateCopy, midCopy);
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *createDataChannel(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    const char *label = nullptr;
    int ordered = 1;
    const char *protocol = nullptr;
    int negotiated = 0;
    int maxPacketLifeTime = -1;
    int maxRetransmits = -1;
    int stream = -1;

    if (!PyArg_ParseTuple(
            args,
            "Ospspiii",
            &capsule,
            &label,
            &ordered,
            &protocol,
            &negotiated,
            &maxPacketLifeTime,
            &maxRetransmits,
            &stream)) {
        return nullptr;
    }

    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        DataChannelOptions options;
        options.ordered = ordered != 0;
        options.protocol = protocol;
        options.negotiated = negotiated != 0;
        options.hasMaxPacketLifeTime = maxPacketLifeTime >= 0;
        options.hasMaxRetransmits = maxRetransmits >= 0;
        options.hasStream = stream >= 0;
        options.maxPacketLifeTime = options.hasMaxPacketLifeTime ? static_cast<unsigned int>(maxPacketLifeTime) : 0;
        options.maxRetransmits = options.hasMaxRetransmits ? static_cast<unsigned int>(maxRetransmits) : 0;
        options.stream = options.hasStream ? static_cast<std::uint16_t>(stream) : 0;

        const int channelId = runWithoutGIL([bridge, &options, labelCopy = std::string(label)] {
            return bridge->peer().createDataChannel(labelCopy, options);
        });
        return PyLong_FromLong(channelId);
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *sendMessage(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    int channelId = -1;
    PyObject *data = nullptr;
    if (!PyArg_ParseTuple(args, "OiO", &capsule, &channelId, &data)) {
        return nullptr;
    }

    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        if (PyUnicode_Check(data)) {
            const char *text = PyUnicode_AsUTF8(data);
            if (!text) {
                return nullptr;
            }
            const std::string textCopy(text);
            runWithoutGIL([bridge, channelId, &textCopy] {
                bridge->peer().sendText(channelId, textCopy);
            });
            Py_RETURN_NONE;
        }

        Py_buffer buffer{};
        if (PyObject_GetBuffer(data, &buffer, PyBUF_CONTIG_RO) != 0) {
            PyErr_SetString(PyExc_TypeError, "DataChannel.send() accepts str or a bytes-like object");
            return nullptr;
        }

        try {
            const char *binaryData = static_cast<const char *>(buffer.buf);
            const std::size_t binarySize = static_cast<std::size_t>(buffer.len);

            // Never enter libdatachannel while holding the Python GIL.
            //
            // Keep the Py_buffer export alive until sendBinary() returns, so
            // the backing storage cannot be resized or freed during the native
            // call. Mutable exporters (for example bytearray or writable
            // memoryview) must not be modified concurrently by another Python
            // thread until RTCDataChannel.send() returns. FastFileLink's WebRTC
            // send path owns each chunk exclusively for this synchronous call,
            // so copying every mutable bytes-like object here would only add
            // avoidable memory bandwidth and allocation overhead.
            runWithoutGIL([bridge, channelId, binaryData, binarySize] {
                bridge->peer().sendBinary(
                    channelId,
                    binaryData,
                    binarySize
                );
            });
        } catch (...) {
            PyBuffer_Release(&buffer);
            return translateCurrentException();
        }

        PyBuffer_Release(&buffer);
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *closeChannel(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    int channelId = -1;
    if (!PyArg_ParseTuple(args, "Oi", &capsule, &channelId)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        runWithoutGIL([bridge, channelId] {
            bridge->peer().closeChannel(channelId);
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *getDataChannelStream(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    int channelId = -1;
    if (!PyArg_ParseTuple(args, "Oi", &capsule, &channelId)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const auto stream = runWithoutGIL([bridge, channelId] {
            return bridge->peer().getDataChannelStream(channelId);
        });
        return PyLong_FromLong(stream);
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *getBufferedAmount(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    int channelId = -1;
    if (!PyArg_ParseTuple(args, "Oi", &capsule, &channelId)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        const auto bufferedAmount = runWithoutGIL([bridge, channelId] {
            return bridge->peer().getBufferedAmount(channelId);
        });
        return PyLong_FromLong(bufferedAmount);
    } catch (...) {
        return translateCurrentException();
    }
}

PyObject *setBufferedAmountLowThreshold(PyObject *, PyObject *args) {
    PyObject *capsule = nullptr;
    int channelId = -1;
    int amount = 0;
    if (!PyArg_ParseTuple(args, "Oii", &capsule, &channelId, &amount)) {
        return nullptr;
    }
    PythonPeerBridge *bridge = getBridge(capsule);
    if (!bridge) {
        return nullptr;
    }

    try {
        runWithoutGIL([bridge, channelId, amount] {
            bridge->peer().setBufferedAmountLowThreshold(channelId, amount);
        });
        Py_RETURN_NONE;
    } catch (...) {
        return translateCurrentException();
    }
}

PyMethodDef moduleMethods[] = {
    {"create_peer_connection", createPeerConnection, METH_VARARGS, nullptr},
    {"close_peer_connection", closePeerConnection, METH_VARARGS, nullptr},
    {"create_offer", createOffer, METH_VARARGS, nullptr},
    {"create_answer", createAnswer, METH_VARARGS, nullptr},
    {"get_local_description", getLocalDescription, METH_VARARGS, nullptr},
    {"set_local_description", setLocalDescription, METH_VARARGS, nullptr},
    {"set_remote_description", setRemoteDescription, METH_VARARGS, nullptr},
    {"add_remote_candidate", addRemoteCandidate, METH_VARARGS, nullptr},
    {"create_data_channel", createDataChannel, METH_VARARGS, nullptr},
    {"send_message", sendMessage, METH_VARARGS, nullptr},
    {"close_channel", closeChannel, METH_VARARGS, nullptr},
    {"get_data_channel_stream", getDataChannelStream, METH_VARARGS, nullptr},
    {"get_buffered_amount", getBufferedAmount, METH_VARARGS, nullptr},
    {"set_buffered_amount_low_threshold", setBufferedAmountLowThreshold, METH_VARARGS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef moduleDefinition = {
    PyModuleDef_HEAD_INIT,
    "_ffl_datachannel",
    "Native libdatachannel bridge for ffl_datachannel.",
    -1,
    moduleMethods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

bool addIntConstant(PyObject *module, const char *name, int value) {
    return PyModule_AddIntConstant(module, name, value) == 0;
}

} // namespace
} // namespace ffl::datachannel

PyMODINIT_FUNC PyInit__ffl_datachannel() {
    using namespace ffl::datachannel;

    PyObject *module = PyModule_Create(&moduleDefinition);
    if (!module) {
        return nullptr;
    }

    nativeErrorType = PyErr_NewException("ffl_datachannel.NativeError", PyExc_RuntimeError, nullptr);
    if (!nativeErrorType) {
        Py_DECREF(module);
        return nullptr;
    }
    if (PyModule_AddObject(module, "NativeError", nativeErrorType) != 0) {
        Py_DECREF(nativeErrorType);
        nativeErrorType = nullptr;
        Py_DECREF(module);
        return nullptr;
    }

    const bool constantsAdded =
        addIntConstant(module, "EVENT_LOCAL_DESCRIPTION", static_cast<int>(EventType::LocalDescription)) &&
        addIntConstant(module, "EVENT_LOCAL_CANDIDATE", static_cast<int>(EventType::LocalCandidate)) &&
        addIntConstant(module, "EVENT_CONNECTION_STATE", static_cast<int>(EventType::ConnectionState)) &&
        addIntConstant(module, "EVENT_ICE_STATE", static_cast<int>(EventType::IceState)) &&
        addIntConstant(module, "EVENT_GATHERING_STATE", static_cast<int>(EventType::GatheringState)) &&
        addIntConstant(module, "EVENT_SIGNALING_STATE", static_cast<int>(EventType::SignalingState)) &&
        addIntConstant(module, "EVENT_DATA_CHANNEL", static_cast<int>(EventType::DataChannel)) &&
        addIntConstant(module, "EVENT_CHANNEL_OPEN", static_cast<int>(EventType::ChannelOpen)) &&
        addIntConstant(module, "EVENT_CHANNEL_CLOSED", static_cast<int>(EventType::ChannelClosed)) &&
        addIntConstant(module, "EVENT_CHANNEL_ERROR", static_cast<int>(EventType::ChannelError)) &&
        addIntConstant(module, "EVENT_CHANNEL_TEXT", static_cast<int>(EventType::ChannelText)) &&
        addIntConstant(module, "EVENT_CHANNEL_BINARY", static_cast<int>(EventType::ChannelBinary)) &&
        addIntConstant(module, "EVENT_CHANNEL_BUFFERED_AMOUNT_LOW", static_cast<int>(EventType::ChannelBufferedAmountLow)) &&
        addIntConstant(module, "EVENT_INTERNAL_ERROR", static_cast<int>(EventType::InternalError)) &&
        addIntConstant(module, "RTC_NEW", RTC_NEW) &&
        addIntConstant(module, "RTC_CONNECTING", RTC_CONNECTING) &&
        addIntConstant(module, "RTC_CONNECTED", RTC_CONNECTED) &&
        addIntConstant(module, "RTC_DISCONNECTED", RTC_DISCONNECTED) &&
        addIntConstant(module, "RTC_FAILED", RTC_FAILED) &&
        addIntConstant(module, "RTC_CLOSED", RTC_CLOSED) &&
        addIntConstant(module, "RTC_ICE_NEW", RTC_ICE_NEW) &&
        addIntConstant(module, "RTC_ICE_CHECKING", RTC_ICE_CHECKING) &&
        addIntConstant(module, "RTC_ICE_CONNECTED", RTC_ICE_CONNECTED) &&
        addIntConstant(module, "RTC_ICE_COMPLETED", RTC_ICE_COMPLETED) &&
        addIntConstant(module, "RTC_ICE_FAILED", RTC_ICE_FAILED) &&
        addIntConstant(module, "RTC_ICE_DISCONNECTED", RTC_ICE_DISCONNECTED) &&
        addIntConstant(module, "RTC_ICE_CLOSED", RTC_ICE_CLOSED) &&
        addIntConstant(module, "RTC_GATHERING_NEW", RTC_GATHERING_NEW) &&
        addIntConstant(module, "RTC_GATHERING_INPROGRESS", RTC_GATHERING_INPROGRESS) &&
        addIntConstant(module, "RTC_GATHERING_COMPLETE", RTC_GATHERING_COMPLETE) &&
        addIntConstant(module, "RTC_SIGNALING_STABLE", RTC_SIGNALING_STABLE) &&
        addIntConstant(module, "RTC_SIGNALING_HAVE_LOCAL_OFFER", RTC_SIGNALING_HAVE_LOCAL_OFFER) &&
        addIntConstant(module, "RTC_SIGNALING_HAVE_REMOTE_OFFER", RTC_SIGNALING_HAVE_REMOTE_OFFER) &&
        addIntConstant(module, "RTC_SIGNALING_HAVE_LOCAL_PRANSWER", RTC_SIGNALING_HAVE_LOCAL_PRANSWER) &&
        addIntConstant(module, "RTC_SIGNALING_HAVE_REMOTE_PRANSWER", RTC_SIGNALING_HAVE_REMOTE_PRANSWER);

    if (!constantsAdded) {
        Py_DECREF(module);
        return nullptr;
    }

    return module;
}
