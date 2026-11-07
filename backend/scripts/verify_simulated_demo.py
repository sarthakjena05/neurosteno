"""Two real-time demo rounds against a running server; never bypasses EEG SEND.

Run: .venv/Scripts/python scripts/verify_simulated_demo.py --url http://127.0.0.1:8000
Uses the same REST commands and WebSocket state as the frontend. No hardware or gestures.
"""
import argparse
import asyncio
import json
import time

import httpx
from websockets.asyncio.client import connect


async def verify(url):
    async with httpx.AsyncClient(base_url=url, timeout=15) as client:
        async def post(path, body):
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        async def command(action, **params):
            return await post('/api/command', {'action': action, **params})

        await post('/api/mode', {'mode': 'simulated', 'seed': 1})
        await command('set_input_mode', mode='eeg_only')
        await command('set_gestures_enabled', on=False)
        await command('set_auto_adapt', on=False)
        await command('set_choices', n=5)
        await command('set_policy', policy='explicit')
        await command('set_routing', mode='static')
        await command('sim_mistake_rate', rate=0)
        await command('home')
        ws_url = url.replace('http', 'ws', 1) + '/ws'
        async with connect(ws_url) as ws:
            async def state():
                while True:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if frame['type'] == 'state':
                        return frame['state']

            s = await state()
            baseline = len(s['sent'])
            for index, target in enumerate(('food-water/mouth/lips', 'comfort/temperature/too-cold'), 1):
                await command('sim_autopilot', node_id=target)
                start = time.monotonic()
                review_at = None
                checked_at = 0
                while time.monotonic() - start < 180:
                    s = await state()
                    if time.monotonic() - checked_at > 2:
                        for path in ('/api/health', '/api/state', '/api/interaction'):
                            response = await client.get(path)
                            response.raise_for_status()
                            response.json()
                        checked_at = time.monotonic()
                    assert s['input']['mode'] == 'eeg_only'
                    assert not s['gestures']['enabled']
                    if s['phase'] == 'MESSAGE_REVIEW' and review_at is None:
                        # The frontend SEND click sends precisely this command.
                        assert s['review']['node_id'] == target
                        await command('sim_gaze', key='yes')
                        review_at = time.monotonic()
                        print(f'Round {index}: explicit simulated EEG YES at review', flush=True)
                    if review_at and time.monotonic() - review_at > 60:
                        raise AssertionError('Final YES failed to SEND within 60 seconds')
                    if len(s['sent']) >= baseline + index:
                        assert review_at is not None
                        assert s['sent'][0]['node_id'] == target
                        assert s['sent'][0]['confirmed_by'] == 'decoded final YES'
                        print(f'Round {index}: SENT {target}; review {time.monotonic()-review_at:.1f}s; total {time.monotonic()-start:.1f}s', flush=True)
                        while s['phase'] == 'MESSAGE_SENT':
                            s = await state()
                        break
                else:
                    raise AssertionError(f'Round {index} timed out: {s["phase"]}')
            for path in ('/api/health', '/api/state', '/api/interaction', '/api/profiles', '/api/vocabulary', '/api/recordings'):
                response = await client.get(path)
                response.raise_for_status()
                response.json()
            print('PASS: two consecutive EEG-only rounds, live WebSocket, and endpoint checks', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    asyncio.run(verify(parser.parse_args().url.rstrip('/')))
