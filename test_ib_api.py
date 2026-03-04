import asyncio
from ib_insync import IB, util

async def test_connect():
    ib = IB()
    try:
        # Try to connect, with timeout
        await asyncio.wait_for(ib.connectAsync('127.0.0.1', 4002, clientId=1), timeout=3)
        # If connected, check available attributes
        print(f"Connected attributes:")
        print(f"  - ib.isConnected(): {ib.isConnected()}")
        print(f"  - hasattr(ib, 'client'): {hasattr(ib, 'client')}")
        if hasattr(ib, 'client'):
            print(f"  - ib.client: {ib.client}")
            print(f"  - dir(ib.client): {[x for x in dir(ib.client) if not x.startswith('_')]}")
        
        # Try to get account values
        av = await ib.accountValuesAsync()
        if av:
            print(f"\nAccount values available: {len(av)} items")
            # Print some sample account values
            for item in av[:3]:
                print(f"  - {item}")
    except asyncio.TimeoutError:
        print("Connection timeout - IBKR Gateway not responding at 127.0.0.1:4002")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
    finally:
        if ib.isConnected():
            await ib.disconnectAsync()

asyncio.run(test_connect())
