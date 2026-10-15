// Minimal Speech synthesis transport, using the protocol in Microsoft's JS SDK:
// https://github.com/microsoft/cognitive-services-speech-sdk-js
// Auth and SSML arrive on stdin; credentials never appear in process arguments.
import { randomUUID } from 'node:crypto';

const buffers = [];
for await (const part of process.stdin) buffers.push(part);
const input = JSON.parse(Buffer.concat(buffers).toString('utf8'));
const id = randomUUID().replaceAll('-', '');
const url = new URL(`wss://${input.endpoint.r}.tts.speech.microsoft.com/tts/cognitiveservices/websocket/v1`);
const token = input.endpoint.t;
url.searchParams.set('Authorization', /^Bearer /i.test(token) ? token : `Bearer ${token}`);
url.searchParams.set('X-ConnectionId', id);

function frame(path, body, type = 'application/json') {
  return `Path:${path}\r\nX-RequestId:${id}\r\nX-Timestamp:${new Date().toISOString()}\r\nContent-Type:${type}\r\n\r\n${body}`;
}

try {
  const result = await new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    const audio = [], metadata = [];
    let finished = false;
    const timer = setTimeout(() => finish(new Error('Microsoft synthesis timed out')), (input.timeout || 180) * 1000);
    function finish(error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.close();
      if (error) reject(error);
      else resolve({ audio: Buffer.concat(audio), metadata });
    }
    socket.addEventListener('open', () => {
      socket.send(frame('speech.config', JSON.stringify({ context: {
        system: { name: 'SpeechSDK', version: '1.0.0', build: 'JavaScript', lang: 'JavaScript' },
        os: { platform: 'Node', name: 'Termux', version: process.version }
      } })));
      socket.send(frame('synthesis.context', JSON.stringify({ synthesis: {
        audio: { metadataOptions: {
          bookmarkEnabled: false, punctuationBoundaryEnabled: 'true', sentenceBoundaryEnabled: 'true',
          sessionEndEnabled: true, visemeEnabled: false, wordBoundaryEnabled: 'true'
        }, outputFormat: 'raw-24khz-16bit-mono-pcm' },
        language: { autoDetection: false }
      } })));
      socket.send(frame('ssml', input.ssml, 'application/ssml+xml'));
    });
    socket.addEventListener('error', () => finish(new Error('Microsoft synthesis WebSocket connection failed')));
    socket.addEventListener('close', event => {
      if (!finished) finish(new Error(`Microsoft synthesis closed before turn.end (code ${event.code})`));
    });
    socket.addEventListener('message', event => {
      try {
        if (typeof event.data === 'string') {
          const separator = event.data.indexOf('\r\n\r\n');
          if (separator < 0) throw new Error('Invalid Microsoft text frame');
          const header = event.data.slice(0, separator);
          const path = /^Path:(.*)$/im.exec(header)?.[1].trim().toLowerCase();
          const body = event.data.slice(separator + 4);
          if (path === 'audio.metadata') metadata.push(...JSON.parse(body).Metadata);
          if (path === 'turn.end') finish();
        } else {
          const data = Buffer.from(event.data);
          if (data.length < 2) throw new Error('Invalid Microsoft binary frame');
          const length = data.readUInt16BE(0);
          if (length + 2 > data.length) throw new Error('Incomplete Microsoft binary frame');
          const header = data.subarray(2, length + 2).toString('utf8');
          if (/^Path:audio\r?$/im.test(header)) audio.push(data.subarray(length + 2));
        }
      } catch (error) { finish(error); }
    });
  });
  if (!result.audio.length || result.audio.length % 2) throw new Error('Microsoft returned empty or incomplete PCM');
  process.stdout.write(result.audio);
  process.stderr.write(JSON.stringify({ metadata: result.metadata }));
} catch (error) {
  // Do not print the URL or underlying socket event, which may contain the token.
  process.stderr.write(JSON.stringify({ error: error.message }));
  process.exitCode = 1;
}
