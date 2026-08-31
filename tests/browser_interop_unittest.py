from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ffl_datachannel import RTCPeerConnection, RTCSessionDescription


class BrowserInteropHarness:
    PAYLOAD_SIZE = 256 * 1024
    BROWSER_PAGE = """<!doctype html>
<meta charset=\"utf-8\">
<title>ffl-datachannel browser interop</title>
<script>
(() => {
  const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const postJson = (path, body) => fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  }).then(response => {
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  });

  window.testResult = null;
  (async () => {
    const connection = new RTCPeerConnection();
    let receivedBytes = 0;
    let receivedMessages = 0;
    const expectedMessages = __EXPECTED_MESSAGES__;
    connection.onicecandidate = async event => {
      if (event.candidate) await postJson('/candidate/browser', event.candidate.toJSON());
    };
    connection.ondatachannel = event => {
      const channel = event.channel;
      channel.binaryType = 'arraybuffer';
      channel.onmessage = messageEvent => {
        if (typeof messageEvent.data === 'string') {
          if (messageEvent.data !== 'EOF') {
            throw new Error(`Unexpected native text message: ${messageEvent.data}`);
          }
          if (receivedMessages !== expectedMessages) {
            throw new Error(`EOF arrived after ${receivedMessages}, expected ${expectedMessages} messages`);
          }
          window.testResult = {receivedBytes, receivedMessages, eof: true};
          return;
        }
        receivedBytes += messageEvent.data.byteLength;
        receivedMessages += 1;
        channel.send(`ACK:${receivedBytes}`);
      };
    };

    const offer = await fetch('/offer').then(response => response.json());
    await connection.setRemoteDescription(offer);
    const answer = await connection.createAnswer();
    await connection.setLocalDescription(answer);
    await postJson('/answer', connection.localDescription);

    const deadline = Date.now() + 15000;
    while (!window.testResult && Date.now() < deadline) {
      const candidate = await fetch('/candidate/server').then(response => response.json());
      if (candidate.candidate) await connection.addIceCandidate(candidate);
      await delay(25);
    }
    if (!window.testResult) throw new Error('Timed out waiting for the native DataChannel payload');
  })().catch(error => {
    window.testResult = {error: String(error)};
  });
})();
</script>"""

    def __init__(self, loop, payloadSize=PAYLOAD_SIZE, messageCount=1):
        self._loop = loop
        self._payloadSize = payloadSize
        self._messageCount = messageCount
        self._peer = RTCPeerConnection()
        self._channel = None
        self._serverCandidates = deque()
        self._pendingBrowserCandidates = deque()
        self._candidateLock = threading.Lock()
        self._ackFuture = loop.create_future()
        self._bufferFlushed = asyncio.Event()
        self._bufferFlushed.set()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._createRequestHandler())
        self._serverThread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.baseURL = f"http://127.0.0.1:{self._server.server_port}"

    def _createRequestHandler(self):
        harness = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requestPath = urlparse(self.path).path
                if requestPath == "/":
                    self._sendText(
                        harness.BROWSER_PAGE.replace("__EXPECTED_MESSAGES__", str(harness._messageCount)),
                        "text/html; charset=utf-8",
                    )
                    return
                if requestPath == "/offer":
                    self._sendJson(harness.getOffer())
                    return
                if requestPath == "/candidate/server":
                    self._sendJson(harness.takeServerCandidate())
                    return
                self.send_error(404)

            def do_POST(self):
                requestPath = urlparse(self.path).path
                requestData = self._readJson()
                try:
                    if requestPath == "/answer":
                        harness.submitAnswer(requestData)
                    elif requestPath == "/candidate/browser":
                        harness.submitBrowserCandidate(requestData)
                    else:
                        self.send_error(404)
                        return
                except Exception as error:
                    self.send_error(500, str(error))
                    return
                self._sendJson({"ok": True})

            def _readJson(self):
                contentLength = int(self.headers["Content-Length"])
                return json.loads(self.rfile.read(contentLength))

            def _sendJson(self, value):
                self._sendText(json.dumps(value), "application/json")

            def _sendText(self, value, contentType):
                content = value.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", contentType)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, formatString, *args):
                return

        return RequestHandler

    async def prepare(self):
        @self._peer.on("icecandidate")
        async def handleNativeCandidate(event):
            if event.candidate is None:
                return
            with self._candidateLock:
                self._serverCandidates.append({
                    "candidate": f"candidate:{event.candidate.to_sdp()}",
                    "sdpMid": event.candidate.sdpMid,
                    "sdpMLineIndex": event.candidate.sdpMLineIndex,
                })

        self._channel = self._peer.createDataChannel("browser-interop", ordered=True)

        @self._channel.on("bufferedamountlow")
        def handleBufferFlushed():
            self._bufferFlushed.set()

        @self._channel.on("open")
        async def sendPayloads():
            self._channel.bufferedAmountLowThreshold = 0
            payloads = [b"x" * self._payloadSize] * self._messageCount
            for payload in payloads:
                await self._bufferFlushed.wait()
                self._channel.send(payload)
                self._bufferFlushed.clear()

            await self._ackFuture
            await self._bufferFlushed.wait()
            self._channel.send("EOF")
            self._bufferFlushed.clear()

        @self._channel.on("message")
        def handleBrowserMessage(message):
            expectedBytes = self._payloadSize * self._messageCount
            if message == f"ACK:{expectedBytes}" and not self._ackFuture.done():
                self._ackFuture.set_result(message)

        offer = await self._peer.createOffer()
        await self._peer.setLocalDescription(offer)
        self._serverThread.start()

    def getOffer(self):
        description = self._peer.localDescription
        if description is None:
            raise RuntimeError("Native offer is not ready")
        return {"type": description.type, "sdp": description.sdp}

    def takeServerCandidate(self):
        with self._candidateLock:
            if not self._serverCandidates:
                return {}
            return self._serverCandidates.popleft()

    def submitAnswer(self, answer):
        future = asyncio.run_coroutine_threadsafe(self._setAnswer(answer), self._loop)
        future.result(timeout=10)

    def submitBrowserCandidate(self, candidate):
        future = asyncio.run_coroutine_threadsafe(self._addBrowserCandidate(candidate), self._loop)
        future.result(timeout=10)

    async def _setAnswer(self, answer):
        await self._peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        while self._pendingBrowserCandidates:
            await self._peer.addIceCandidate(self._pendingBrowserCandidates.popleft())

    async def _addBrowserCandidate(self, candidate):
        if self._peer.remoteDescription is None:
            self._pendingBrowserCandidates.append(candidate)
            return
        await self._peer.addIceCandidate(candidate)

    async def waitForAcknowledgement(self):
        return await asyncio.wait_for(self._ackFuture, timeout=20)

    async def close(self):
        await asyncio.to_thread(self._server.shutdown)
        await asyncio.to_thread(self._serverThread.join, 5)
        self._server.server_close()
        await self._peer.close()


class BrowserInteropTest(unittest.IsolatedAsyncioTestCase):
    def _requireSelectedBrowser(self, browserName):
        selectedBrowsers = os.getenv("FFL_DATACHANNEL_BROWSER", "all").strip().lower()
        if selectedBrowsers in ("all", browserName):
            return
        self.skipTest(f"FFL_DATACHANNEL_BROWSER={selectedBrowsers} excludes {browserName}")

    async def _runBrowserInterop(self, browserName, payloadSize=BrowserInteropHarness.PAYLOAD_SIZE, messageCount=1):
        harness = BrowserInteropHarness(asyncio.get_running_loop(), payloadSize, messageCount)
        await harness.prepare()
        try:
            browserResult = await asyncio.to_thread(self._runBrowserPage, browserName, harness.baseURL)
            acknowledgement = await harness.waitForAcknowledgement()
            expectedBytes = payloadSize * messageCount
            self.assertEqual(
                {"receivedBytes": expectedBytes, "receivedMessages": messageCount, "eof": True},
                browserResult,
            )
            self.assertEqual(f"ACK:{expectedBytes}", acknowledgement)
        finally:
            await harness.close()

    @staticmethod
    def _runBrowserPage(browserName, baseURL):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.firefox.options import Options as FirefoxOptions

        if browserName == "chrome":
            options = ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            driver = webdriver.Chrome(options=options)
        elif browserName == "firefox":
            options = FirefoxOptions()
            options.add_argument("-headless")
            driver = webdriver.Firefox(options=options)
        else:
            raise ValueError(f"Unsupported browser: {browserName}")

        try:
            driver.get(baseURL)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = driver.execute_script("return window.testResult")
                if result is not None:
                    if "error" in result:
                        raise RuntimeError(result["error"])
                    return result
                time.sleep(0.05)
            raise TimeoutError(f"{browserName} did not complete the DataChannel transfer")
        finally:
            driver.quit()

    async def testChromeNativeDataChannelInterop(self):
        self._requireSelectedBrowser("chrome")
        await self._runBrowserInterop("chrome")

    async def testFirefoxNativeDataChannelInterop(self):
        self._requireSelectedBrowser("firefox")
        await self._runBrowserInterop("firefox")

    async def testChromeNativeDataChannelPreservesOversizeBinaryMessage(self):
        self._requireSelectedBrowser("chrome")
        await self._runBrowserInterop("chrome", BrowserInteropHarness.PAYLOAD_SIZE + 31)

    async def testChromeNativeDataChannelWaitsForBufferDrainBetweenMessages(self):
        self._requireSelectedBrowser("chrome")
        await self._runBrowserInterop("chrome", messageCount=2)

    async def testFirefoxNativeDataChannelWaitsForBufferDrainBetweenMessages(self):
        self._requireSelectedBrowser("firefox")
        await self._runBrowserInterop("firefox", messageCount=2)

    async def testChromeNativeDataChannelSustainsFileSizedTransfer(self):
        self._requireSelectedBrowser("chrome")
        await self._runBrowserInterop("chrome", messageCount=48)

    async def testFirefoxNativeDataChannelSustainsFileSizedTransfer(self):
        self._requireSelectedBrowser("firefox")
        await self._runBrowserInterop("firefox", messageCount=48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
