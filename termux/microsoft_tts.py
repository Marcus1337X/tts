#!/usr/bin/env python3
"""Direct Microsoft Translator/Speech narration, adapted from Marcus1337X/tts.

Source: https://github.com/Marcus1337X/tts (wangwangit/tts, 749645c).
MIT License — Copyright (c) 2025 一只会飞的旺旺

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import argparse
import base64
from email.utils import formatdate
import hashlib
import hmac
from html import escape
import io
import json
import math
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4
import wave

ENDPOINT='https://dev.microsofttranslator.com/apps/endpoint?api-version=1.0'
USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0'
# Public application signing constant used by the source project, not a user API key.
APP_KEY='oik6PdDdMnOXemTbwvMn9de/h9lFnfBaCWbGMMZqqoSaQaqUOqjVGm5NqsmjcBI1x+sS9ugjB55HEJWRiFXYFw=='
DEFAULTS={'voice':'zh-CN-XiaochenNeural','speed':1.25,'pitch':0,'volume':0,'style':'newscast',
          'sentence_pause':400,'comma_pause':None}


def parameters(settings=None):
    result={**DEFAULTS,**(settings or {})}
    for key,low,high in [('speed',.5,2),('pitch',-50,50),('volume',-1,1)]:
        value=float(result[key])
        if not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{key} must be within {low}..{high}')
        result[key]=value
    for key in ('voice','style'):
        if not isinstance(result[key],str) or not re.fullmatch(r'[A-Za-z0-9_:.-]+',result[key]):
            raise ValueError(f'Invalid {key}')
    for key in ('sentence_pause','comma_pause'):
        value=result[key]
        if key=='comma_pause' and value is None: continue
        if isinstance(value,str):
            match=re.fullmatch(r'(\d+)(?:ms)?',value)
            if not match: raise ValueError(f'{key} must be an integer number of milliseconds')
            value=int(match.group(1))
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or int(value)!=value or not 0<=value<=20000:
            raise ValueError(f'{key} must be within 0..20000 milliseconds')
        result[key]=int(value)
    return {key:result[key] for key in DEFAULTS}


def signature(url=ENDPOINT):
    identifier=uuid4().hex
    date=formatdate(usegmt=True).lower()
    encoded=quote(url.split('://',1)[1],safe="~()*!.'-_")
    data=f'MSTranslatorAndroidApp{encoded}{date}{identifier}'.lower().encode()
    digest=base64.b64encode(hmac.new(base64.b64decode(APP_KEY),data,hashlib.sha256).digest()).decode()
    return f'MSTranslatorAndroidApp::{digest}::{date}::{identifier}'


def post(url,body,headers,timeout):
    for attempt in range(3):
        try:
            with urlopen(Request(url,data=body,headers=headers,method='POST'),timeout=timeout) as response:
                return response.read()
        except HTTPError as error:
            # Never include authentication headers or the token response in errors.
            if error.code not in (429,500,502,503,504) or attempt==2:
                raise ValueError(f'Microsoft request failed: HTTP {error.code}') from None
            time.sleep(attempt+1)


def endpoint(timeout=60):
    raw=post(ENDPOINT,b'',{'Accept-Language':'zh-Hans','X-ClientVersion':'4.0.530a 5fe1dc6c',
        'X-UserId':'0f04d16a175c411e','X-HomeGeographicRegion':'zh-Hans-CN',
        'X-ClientTraceId':uuid4().hex,'X-MT-Signature':signature(),'User-Agent':USER_AGENT,
        'Content-Type':'application/json; charset=utf-8'},timeout)
    data=json.loads(raw)
    if not isinstance(data.get('t'),str) or not data['t'] or not re.fullmatch(r'[a-z0-9-]+',data.get('r','')):
        raise ValueError('Microsoft returned an invalid speech endpoint')
    return data


def ssml(text,settings=None):
    settings=parameters(settings)
    if not isinstance(text,str) or not text.strip(): raise ValueError('Narration text is empty')
    if any(ord(char)<32 and char not in '\n\r\t' for char in text):
        raise ValueError('Narration contains invalid XML control characters')
    rate=math.trunc((settings['speed']-1)*100)
    pitch=math.trunc(settings['pitch']);volume=math.trunc(settings['volume']*100)
    # WordBoundary metadata disables Comma-exact. Use explicit breaks only for
    # configured pauses. Disable the natural sentence-boundary pause so the
    # requested break is not added on top of it. Preserve all original text.
    parts=[];cursor=0
    for match in re.finditer(r'[，,]|[。！？!?]+|\.(?=\s|$)',text):
        parts.append(escape(text[cursor:match.end()],quote=True));cursor=match.end()
        if text[cursor:].strip(' \r\n\t“”‘’"\''):
            key='comma_pause' if match.group() in ('，',',') else 'sentence_pause'
            if settings[key] is not None: parts.append(f'<break time="{settings[key]}ms"/>')
    parts.append(escape(text[cursor:],quote=True))
    spoken=''.join(parts)
    body=(f'<speak xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="http://www.w3.org/2001/mstts" version="1.0" xml:lang="zh-CN">'
          f'<voice name="{settings["voice"]}"><mstts:silence type="Sentenceboundary-exact" value="0ms"/>'
          f'<mstts:express-as style="{settings["style"]}" styledegree="2.0" role="default">'
          f'<prosody rate="{rate:+d}%" pitch="{pitch:+d}Hz" volume="{volume:+d}%">{spoken}</prosody>'
          '</mstts:express-as></voice></speak>')
    if len(body.encode())>64000:
        raise ValueError('Complete narration SSML exceeds 64 KB; no audio has been split or generated')
    return body


def synthesize(text,settings=None,timeout=180):
    settings=parameters(settings)
    body=ssml(text,settings).encode()
    start=time.monotonic()
    auth=endpoint(min(timeout,60))
    url=f'https://{auth["r"]}.tts.speech.microsoft.com/cognitiveservices/v1'
    audio=post(url,body,{'Authorization':auth['t'],'Content-Type':'application/ssml+xml',
               'User-Agent':USER_AGENT,'X-Microsoft-OutputFormat':'riff-24khz-16bit-mono-pcm'},timeout)
    try:
        with wave.open(io.BytesIO(audio)) as stream:
            if (stream.getnchannels(),stream.getsampwidth(),stream.getframerate())!=(1,2,24000) or not stream.getnframes():
                raise ValueError('Microsoft returned unexpected or empty PCM audio')
            seconds=stream.getnframes()/stream.getframerate()
    except (wave.Error,EOFError) as error:
        raise ValueError('Microsoft did not return a complete PCM WAV') from error
    return audio,{'engine':'microsoft-translator-speech','parameters':settings,
        'elapsed_seconds':time.monotonic()-start,'audio_seconds':seconds,'sample_rate':24000,
        'mode':'one_full_text_request','offline':False,'subjective_listening':'not_checked'}


def synthesize_with_boundaries(text,settings=None,timeout=180):
    """Collect PCM and native word/sentence events from the same synthesis turn."""
    settings=parameters(settings)
    body=ssml(text,settings)
    start=time.monotonic()
    auth=endpoint(min(timeout,60))
    helper=Path(__file__).with_name('microsoft_speech_stream.mjs')
    process=subprocess.run(['node',str(helper)],input=json.dumps({'endpoint':auth,'ssml':body,'timeout':timeout}).encode(),
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout+15)
    try: metadata=json.loads(process.stderr)
    except json.JSONDecodeError:
        raise ValueError('Microsoft timestamp transport failed; requires Node.js 22+ with WebSocket support') from None
    if process.returncode:
        raise ValueError(metadata.get('error','Microsoft timestamp transport failed'))
    words=[item for item in metadata.get('metadata',[]) if item.get('Type')=='WordBoundary']
    if not words: raise ValueError('Microsoft returned no word timestamps; refusing guessed alignment')
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as stream:
        stream.setparams((1,2,24000,0,'NONE','not compressed'))
        stream.writeframes(process.stdout)
    seconds=len(process.stdout)/48000
    return buffer.getvalue(),{'engine':'microsoft-translator-speech','parameters':settings,
        'elapsed_seconds':time.monotonic()-start,'audio_seconds':seconds,'sample_rate':24000,
        'mode':'one_full_text_request_native_boundaries','offline':False,'subjective_listening':'not_checked'},metadata['metadata']


def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--text-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--voice',default=DEFAULTS['voice'])
    parser.add_argument('--speed',type=float,default=DEFAULTS['speed'])
    parser.add_argument('--pitch',type=float,default=DEFAULTS['pitch'])
    parser.add_argument('--style',default=DEFAULTS['style'])
    parser.add_argument('--sentence-pause',default=DEFAULTS['sentence_pause'],help='Exact sentence pause, e.g. 400ms')
    parser.add_argument('--comma-pause',default=DEFAULTS['comma_pause'],help='Optional comma pause; omit for natural punctuation timing')
    parser.add_argument('--timestamps',type=Path,help='Write native synthesis events from the same turn; requires Node.js 22+')
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(f'Existing audio preserved: {args.output}')
    if args.timestamps and args.timestamps.exists(): raise FileExistsError(f'Existing timestamps preserved: {args.timestamps}')
    settings={key:getattr(args,key) for key in ('voice','speed','pitch','style','sentence_pause','comma_pause')}
    if args.timestamps:
        audio,report,events=synthesize_with_boundaries(args.text_file.read_text(),settings)
    else: audio,report=synthesize(args.text_file.read_text(),settings)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as output: output.write(audio)
    if args.timestamps:
        args.timestamps.parent.mkdir(parents=True,exist_ok=True)
        with args.timestamps.open('x') as output: output.write(json.dumps(events,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,KeyError) as error:
        raise SystemExit('ERROR: '+str(error))
